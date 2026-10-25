"""One rounded conversation surface with independent steps and attached result."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from winapi import appearance, inputs, windows
from winapi.composition import Glass
from winapi.shapes import Rect

from . import notification_reading as reading
from . import notification_text
from .agent_session import StreamState
from .agent_window import log_view
from .notifications import (
    REVEAL_SECONDS,
    Busy,
    Content,
    Frame,
    Motion,
    Recording,
    Settled,
    Toast,
    Tone,
    Turn,
    Working,
    can_stop,
    caption,
    heading,
    hint,
    order_of,
    spinning,
    steps,
    tone,
)
from .qt import (
    QAbstractButton,
    QColor,
    QContextMenuEvent,
    QFontMetricsF,
    QKeyEvent,
    QLabel,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPoint,
    QRectF,
    Qt,
    QTextCharFormat,
    QTextCursor,
    QTextEdit,
    QWidget,
    shiboken6,
)
from .qt_text import INSET, TEXT_PADDING, Document, TextView, font, units
from .rich_text import Span
from .toast_style import Ink, Palette, palette

if TYPE_CHECKING:
    from .agent_toasts import AgentToasts


@dataclass(frozen=True, slots=True)
class Prepared:
    width: int
    spans: tuple[Span, ...]
    body_height: int
    header_height: int
    label_width: int
    hint: str
    hint_height: int
    caption: str
    details: tuple[Span, ...]
    details_height: int
    details_limit: int
    padding: int

    @property
    def preferred(self) -> int:
        return self.header_height + min(self.details_height, self.details_limit) + self.body_height + self.hint_height + self.padding


@dataclass(frozen=True, slots=True)
class Layout:
    details_height: int
    body_height: int
    hint: str
    hint_height: int
    height: int


@dataclass(frozen=True, slots=True)
class Reveal:
    start: int
    end: int
    started: float


class Measure:
    __slots__ = ("body", "details", "scale")

    def __init__(self) -> None:
        self.body, self.details = Document(), Document()
        self.scale = 1.0

    def text_height(self, text: str, size: float, width: float, *, bold: bool = False) -> float:
        metrics = QFontMetricsF(font(size, bold=bold))
        return metrics.boundingRect(QRectF(0, 0, max(1, width), 10000), Qt.TextFlag.TextWordWrap.value, text).height() if text else 0

    def prepare(self, content: Content, maximum: int, *, expanded: bool = False) -> Prepared:
        scale = self.scale
        user = tone(content) is Tone.USER
        maximum_width = min(maximum / scale, 280 if user else maximum / scale)
        spans = notification_text.spans(content)
        details = notification_text.details(content) if expanded else ()
        meta, footer = caption(content), "You" if user else hint(content)
        controls = 24 + (32 if can_stop(content) else 0) + (28 if isinstance(content, Turn) and steps(content) else 0)
        label = heading(content)
        label_width = max(QFontMetricsF(font(9.5, bold=True)).horizontalAdvance(label),
                          QFontMetricsF(font(8.5)).horizontalAdvance(meta))
        body_width = self.body.intrinsic_width(spans, max(1, maximum_width - INSET * 2))
        detail_width = self.details.intrinsic_width(details, max(1, maximum_width - INSET * 2))
        footer_width = QFontMetricsF(font(8.5)).horizontalAdvance(footer)
        width = min(maximum_width, max(100, body_width + INSET * 2, detail_width + INSET * 2,
                                      footer_width + INSET * 2, label_width + controls + INSET * 2 if not user else 0))
        text_width = max(1, width - INSET * 2)
        body_height = self.body.load(spans, text_width) + TEXT_PADDING * 2 if spans else 0
        details_height = self.details.load(details, text_width) + TEXT_PADDING * 2 if details else 0
        label_width = max(1, width - controls - INSET * 2)
        header = 8 if user else 14 + max(28, self.text_height(label, 9.5, label_width, bold=True)
                                       + self.text_height(meta, 8.5, label_width)) + 8
        footer_height = self.text_height(footer, 8.5, text_width) + 8 if footer else 0
        return Prepared(math.ceil(width * scale), spans, math.ceil(body_height * scale), math.ceil(header * scale),
                        math.floor(label_width * scale), footer, math.ceil(footer_height * scale), meta, details,
                        math.ceil(details_height * scale), round(200 * scale), round(8 * scale))

    def fit_body(self, document: Document, spans: tuple[Span, ...], width: int, available: int) -> int:
        if not spans:
            return 0
        document.load(spans, max(1, width / self.scale - INSET * 2))
        fitted = document.fit(available / self.scale - TEXT_PADDING * 2)
        return math.ceil((fitted + TEXT_PADDING * 2) * self.scale) if fitted else 0

    def layout(self, prepared: Prepared, width: int, maximum: int) -> Layout:
        available = max(0, maximum - prepared.header_height - prepared.hint_height - prepared.padding)
        budget = min(prepared.details_limit, max(0, available - min(prepared.body_height, available // 2)))
        details = (self.fit_body(self.details, prepared.details, width, budget)
                   if prepared.details_height > budget else prepared.details_height)
        available -= details
        body = (self.fit_body(self.body, prepared.spans, width, available)
                if prepared.body_height > available else prepared.body_height)
        return Layout(details, body, prepared.hint, prepared.hint_height,
                      prepared.header_height + details + body + prepared.hint_height + prepared.padding)

    def close(self) -> None:
        self.body.close()
        self.details.close()


class Icon(Enum):
    STOP = auto()
    DISCLOSURE = auto()


class IconButton(QAbstractButton):
    __slots__ = ("expanded", "ink", "key_click", "kind")

    def __init__(self, parent: QWidget, icon: Icon, key_click: Callable[[], None]) -> None:
        super().__init__(parent)
        self.kind = icon
        self.key_click = key_click
        self.expanded = False
        self.ink = "#f6f3ff"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Stop agent" if icon is Icon.STOP else "Show steps")
        self.setToolTip(self.accessibleName())

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self.ink), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setOpacity(.45 if not self.isEnabled() else .65 if self.isDown() else 1)
        cx, cy = self.width() / 2, self.height() / 2
        path = QPainterPath()
        match self.kind:
            case Icon.STOP:
                path.moveTo(cx - 4, cy - 4)
                path.lineTo(cx + 4, cy + 4)
                path.moveTo(cx + 4, cy - 4)
                path.lineTo(cx - 4, cy + 4)
            case Icon.DISCLOSURE:
                direction = -1 if self.expanded else 1
                path.moveTo(cx - 3.5, cy - direction * 1.5)
                path.lineTo(cx, cy + direction * 2)
                path.lineTo(cx + 3.5, cy - direction * 1.5)
        painter.drawPath(path)
        if self.hasFocus():
            painter.setPen(QPen(QColor(self.ink), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 8, 8)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.setDown(True)
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.setDown(False)
            if self.isEnabled() and not event.isAutoRepeat():
                self.key_click()
            event.accept()
        else:
            super().keyReleaseEvent(event)


class Bubble(QWidget):
    __slots__ = ("card",)

    def __init__(self, card: Card) -> None:
        super().__init__()
        self.card = card
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Toolkit · Agent notification")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        card = self.card
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 26, 26)
        # A nonzero alpha also makes all empty space in the bubble a real mouse
        # target. Text and icon padding need no extra invisible native windows.
        painter.fillPath(outline, QColor(1, 1, 1, 1) if card.glass else QColor(card.colors.surface))
        edge = QColor(card.colors.edge)
        edge.setAlpha(85 if card.glass else 255)
        painter.setPen(QPen(edge, 1))
        painter.drawPath(outline)
        if card.content is None or card.prepared is None or tone(card.content) is Tone.USER:
            return
        header = card.prepared[2].header_height / card.owner.scale
        cx, cy = INSET + 7, 14 + (header - 22) / 2
        painter.setPen(QPen(QColor(card.ink.foreground), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if spinning(card.content):
            track = QColor(card.ink.foreground)
            track.setAlpha(50)
            painter.setPen(QPen(track, 1.6))
            painter.drawEllipse(QPoint(round(cx), round(cy)), 6, 6)
            painter.setPen(QPen(QColor(card.ink.foreground), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(cx - 6, cy - 6, 12, 12), round(card.spin_angle * 16), 240 * 16)
        elif isinstance(card.content, Turn) and card.content.state is Settled.FINISHED:
            check = QPainterPath()
            check.moveTo(cx - 4, cy)
            check.lineTo(cx - 1, cy + 3)
            check.lineTo(cx + 5, cy - 3)
            painter.drawPath(check)
        elif isinstance(card.content, Turn) and card.content.state is Settled.STOPPED:
            painter.drawLine(round(cx - 4), round(cy), round(cx + 4), round(cy))
        else:
            painter.setBrush(QColor(card.ink.foreground))
            painter.drawEllipse(QPoint(round(cx), round(cy)), 2, 2)
        if card.text.isVisible() and isinstance(card.content, Turn):
            painter.setPen(QPen(edge, 1))
            top = card.text.y() - TEXT_PADDING
            painter.drawLine(0, top, self.width(), top)
        for text in (card.text, card.details):
            scroll = text.verticalScrollBar()
            if text.isVisible() and scroll.maximum() > 0:
                height = text.height()
                total = height + scroll.maximum()
                start = text.y() + height * scroll.value() / total
                end = start + max(8, height * height / total)
                painter.setPen(QPen(QColor(card.ink.secondary), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(self.width() - 6, round(start), self.width() - 6, min(text.y() + height, round(end)))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        prepared = self.card.prepared
        if (event.button() is Qt.MouseButton.LeftButton and prepared is not None
                and event.position().y() < prepared[2].header_height / self.card.owner.scale):
            self.card.toggle()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        self.card.menu(event.globalPos())
        event.accept()


class Card:
    __slots__ = (
        "bounds", "caption", "closed", "colors", "content", "detail_spans", "details", "disclosure", "expanded", "frame", "frost", "glass",
        "handle", "height", "hint", "hovered", "ink", "key", "label", "layer", "layout", "line_height", "look", "maximum", "motion", "order",
        "owner", "page_origin", "prepared", "reader", "retiring", "reveals", "right", "spans", "speed", "spin_angle", "stop", "tail_pending",
        "text", "width", "window",
    )

    def __init__(self, owner: AgentToasts, toast: Toast, width: int) -> None:
        self.owner, self.key, self.order = owner, toast.key, order_of(toast)
        self.content: Content | None = None
        self.prepared: tuple[Content, int, Prepared] | None = None
        self.layout: Layout | None = None
        self.bounds: Rect | None = None
        self.look: tuple[Tone, appearance.Preference, appearance.Frost] | None = None
        self.frost = appearance.Frost.SMOKED
        self.glass = self.closed = self.hovered = self.retiring = self.expanded = False
        self.tail_pending = False
        self.motion: Motion | None = None
        self.reader: reading.State = reading.Waiting(0, owner.clock() + reading.START_SECONDS)
        self.page_origin = self.speed = self.spin_angle = 0.0
        self.line_height = round(QFontMetricsF(font()).height() * owner.scale * 1.38)
        self.width, self.height, self.maximum, self.right = width, 1, 0, 0
        self.frame = Frame(0, width, 1, 0)
        self.spans: tuple[Span, ...] = ()
        self.detail_spans: tuple[Span, ...] = ()
        self.reveals: tuple[Reveal, ...] = ()
        self.colors, self.ink = palette(tone(toast.content)), Ink()
        self.layer: Glass | None = None
        self.window = Bubble(self)
        self.window.setWindowOpacity(0)
        self.label, self.caption, self.hint = (QLabel(self.window) for _ in range(3))
        for label, face in ((self.label, font(9.5, bold=True)), (self.caption, font(8.5)), (self.hint, font(8.5))):
            label.setFont(face)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text = TextView(self.window, self.scroll_delta, self.menu)
        self.details = TextView(self.window, self.scroll_details, lambda point: self.details.copy_menu(point, self.history))
        self.details.setAccessibleName("Agent steps")
        for text in (self.text, self.details):
            text.verticalScrollBar().valueChanged.connect(self.window.update)
            text.verticalScrollBar().rangeChanged.connect(self.window.update)
        self.stop = IconButton(self.window, Icon.STOP, self.cancel)
        self.stop.clicked.connect(self.cancel)
        self.disclosure = IconButton(self.window, Icon.DISCLOSURE, lambda: self.toggle(animate=False))
        self.disclosure.clicked.connect(self.toggle)
        self.handle = int(self.window.winId())
        windows.make_overlay(self.handle, click_through=False)

    @property
    def y(self) -> float:
        return self.frame.y

    @property
    def alpha(self) -> float:
        return self.frame.alpha

    def history(self) -> None:
        self.owner.show(focus=True)

    def menu(self, point: QPoint) -> None:
        self.text.copy_menu(point, self.history)

    def cancel(self) -> None:
        match self.content:
            case Turn(state=Working()):
                self.owner.agent.stop()
            case Recording() if self.owner.agent.on_recording is not None:
                self.owner.agent.on_recording("stop")
            case _:
                pass

    def toggle(self, *, animate: bool = True) -> None:
        if self.closed or self.retiring or not isinstance(self.content, Turn) or not steps(self.content):
            return
        self.expanded = not self.expanded
        self.prepared = None
        self.layout = None
        self.owner.sync(self.owner.clock(), animate=animate and self.owner.motion_enabled())

    def scroll_details(self, delta: int) -> None:
        if not self.closed and self.expanded:
            scroll = self.details.verticalScrollBar()
            scroll.setValue(scroll.value() + round(-delta / 120 * self.line_height / self.owner.scale * 3))
            self.owner.set_hover(self.key, True)

    def check_pointer(self) -> None:
        if not self.closed and self.bounds is not None:
            x, y = inputs.cursor_position()
            self.owner.set_hover(self.key, self.bounds.contains(x, y))

    def scroll_delta(self, delta: int) -> None:
        if not delta or self.closed or self.prepared is None:
            return
        scroll = self.text.verticalScrollBar()
        scroll.setValue(scroll.value() + round(-delta / 120 * self.line_height / self.owner.scale * 3))
        self.reader = reading.Paused(self.text.offset * self.owner.scale)
        self.page_origin = self.reader.offset
        self.owner.set_hover(self.key, True)
        self.owner.tick()

    def overflow(self) -> int:
        if self.prepared is None or self.layout is None:
            return 0
        return max(0, self.prepared[2].body_height - self.layout.body_height)

    def needs_reading(self) -> bool:
        return not self.retiring and not isinstance(self.content, Recording) and self.reader.offset < self.overflow()

    def read(self, now: float, *, paused: bool, smooth: bool) -> bool:
        if isinstance(self.content, Recording) or self.prepared is None or self.layout is None:
            return False
        limit = self.overflow()
        self.reader = reading.advance(self.reader, now, limit, self.speed, paused=paused)
        if limit and not paused and self.text.height() > 1:
            offset = reading.displayed(self.reader.offset, limit, self.layout.body_height, self.line_height, smooth=smooth, origin=self.page_origin)
            self.text.verticalScrollBar().setValue(round(offset / self.owner.scale))
        return not isinstance(self.reader, reading.Finished)

    def apply_style(self, content: Content, preference: appearance.Preference) -> None:
        look = (tone(content), preference, self.frost)
        if self.look == look:
            return
        self.look = look
        self.colors, self.ink = palette(tone(content)), Ink()
        if isinstance(preference, appearance.Contrast):
            self.colors = Palette(preference.background, preference.foreground, preference.foreground, preference.background)
            self.ink = Ink(*(preference.foreground for _ in range(4)))
        elif preference is appearance.Material.GLASS and self.frost is appearance.Frost.BRIGHT:
            self.ink = Ink("#202435", "#3b3d50", "#234782", "#8e3144")
        if preference is appearance.Material.GLASS and self.layer is None and self.owner.loop.composition is not None:
            try:
                self.layer = self.owner.loop.composition.glass()
            except OSError as error:
                log_view(f"Rounded glass unavailable; using the solid material: {error}")
        self.glass = preference is appearance.Material.GLASS and self.layer is not None
        if self.layer is not None:
            self.layer.color(self.colors.tint, .82 if self.frost is appearance.Frost.SMOKED else .22)
            if not self.glass:
                self.layer.hide()
        if not self.glass and not isinstance(preference, appearance.Contrast):
            self.ink = Ink()
        for label, color in ((self.label, self.ink.foreground), (self.caption, self.ink.secondary), (self.hint, self.ink.secondary)):
            label.setStyleSheet(f"background: transparent; color: {color};")
        for button in (self.stop, self.disclosure):
            button.ink = self.ink.foreground
            button.update()
        self.text.recolor(self.ink)
        self.details.recolor(self.ink)
        self.window.update()
        if self.bounds is not None and self.layer is not None and self.glass:
            self.layer.position(self.bounds, self.owner.scale, self.alpha)
            if self.window.isVisible():
                self.layer.show(self.handle)

    def measure(self, content: Content, width: int) -> Prepared:
        if self.prepared is None or self.prepared[:2] != (content, width):
            self.prepared = (content, width, self.owner.measure.prepare(content, width, expanded=self.expanded))
        return self.prepared[2]

    def render(self, content: Content, maximum_width: int, maximum: int, preference: appearance.Preference) -> None:
        prepared = self.measure(content, maximum_width)
        width = prepared.width
        if (content == self.content and (width, maximum) == (self.width, self.maximum)
                and self.look == (tone(content), preference, self.frost) and self.layout is not None):
            return
        previous = self.content
        self.layout = self.owner.measure.layout(prepared, width, maximum)
        self.content, self.width, self.maximum, self.height = content, width, maximum, self.layout.height
        self.apply_style(content, preference)
        user = tone(content) is Tone.USER
        self.label.setText(heading(content))
        self.caption.setText(prepared.caption)
        self.label.setVisible(not user)
        self.caption.setVisible(not user and bool(prepared.caption))
        self.hint.setText(self.layout.hint)
        self.hint.setAlignment((Qt.AlignmentFlag.AlignRight if user else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        self.stop.setVisible(can_stop(content))
        self.stop.setEnabled(not (isinstance(content, Turn) and isinstance(content.state, Working) and content.state.phase is Busy.STOPPING)
                             and not (isinstance(content, Recording) and content.state.phase == "stopping"))
        self.disclosure.setVisible(isinstance(content, Turn) and bool(steps(content)))
        self.disclosure.expanded = self.expanded
        self.disclosure.setAccessibleName("Hide steps" if self.expanded else "Show steps")
        self.disclosure.setToolTip(self.disclosure.accessibleName())
        self.disclosure.update()
        text_width = max(1, width / self.owner.scale - INSET * 2)
        if prepared.details:
            self.details.update_spans(prepared.details, text_width)
            self.detail_spans = prepared.details
        if prepared.spans != self.spans:
            offset = units("".join(span.text for span in self.spans))
            self.spans = prepared.spans
            self.text.update_spans(prepared.spans, text_width)
            self.speed = reading.pace("".join(span.text for span in prepared.spans), prepared.body_height, self.line_height)
            before, after = notification_text.answer(previous), notification_text.answer(content)
            if (before is not None and after is not None and before.identity == after.identity and after.state is not StreamState.INTERRUPTED
                    and self.owner.motion_enabled() and not isinstance(preference, appearance.Contrast)):
                end = units("".join(span.text for span in self.spans))
                if end > offset:
                    self.reveals = (*self.reveals[-11:], Reveal(offset, end, self.owner.clock()))
        self.place_content(self.frame.width if previous is not None else width, self.frame.height if previous is not None else self.height,
                           self.frame.details if previous is not None else self.layout.details_height)
        if isinstance(content, Recording):
            self.tail_pending = True  # Follow the newest transcript after Qt resolves the final viewport.
        self.window.setAccessibleName(f"{heading(content)}. {prepared.caption}".strip())
        self.window.update()

    def fade_text(self, now: float, *, animate: bool) -> bool:
        self.reveals = tuple(reveal for reveal in self.reveals if animate and now - reveal.started < REVEAL_SECONDS)
        selections: list[QTextEdit.ExtraSelection] = []
        start, end = QColor(self.ink.secondary), QColor(self.ink.foreground)
        for reveal in self.reveals:
            amount = max(0.0, min(1.0, (now - reveal.started) / REVEAL_SECONDS))
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(self.text.document())
            selection.cursor.setPosition(min(reveal.start, self.text.document().characterCount() - 1))
            selection.cursor.setPosition(min(reveal.end, self.text.document().characterCount() - 1), QTextCursor.MoveMode.KeepAnchor)
            pairs = ((start.red(), end.red()), (start.green(), end.green()), (start.blue(), end.blue()))
            color = QColor(*(round(a + (b - a) * amount) for a, b in pairs))
            selection.format = QTextCharFormat()
            selection.format.setForeground(color)
            selections.append(selection)
        self.text.setExtraSelections(selections)
        return bool(self.reveals)

    def match_background(self, sample: appearance.Backdrop, preference: appearance.Preference) -> None:
        if not self.glass or self.bounds != sample.bounds or self.content is None or self.retiring:
            return
        if sample.frost is not self.frost:
            self.frost = sample.frost
            self.apply_style(self.content, preference)

    def place_content(self, width: float, height: float, details: float) -> None:
        if self.layout is None or self.prepared is None:
            return
        scale = self.owner.scale
        layout, prepared = self.layout, self.prepared[2]
        wide = width / scale
        header = prepared.header_height / scale
        controls = (32 if not self.stop.isHidden() else 0) + (28 if not self.disclosure.isHidden() else 0)
        label_width = max(1, round(wide - INSET * 2 - 24 - controls))
        caption_height = round(QFontMetricsF(font(8.5)).height()) if prepared.caption else 0
        self.label.setGeometry(INSET + 24, 14, label_width, max(1, round(header - 22) - caption_height))
        self.caption.setGeometry(INSET + 24, round(header - 8) - caption_height, label_width, max(1, caption_height))
        right = round(wide) - INSET
        if not self.stop.isHidden():
            self.stop.setGeometry(right - 28, 12, 28, max(28, round(header - 20)))
            right -= 32
        if not self.disclosure.isHidden():
            self.disclosure.setGeometry(right - 28, 12, 28, max(28, round(header - 20)))
        detail_height = max(0, min(details, height - prepared.header_height - layout.hint_height - prepared.padding))
        top = prepared.header_height + detail_height
        body_height = max(0, min(layout.body_height, height - top - layout.hint_height - prepared.padding))
        text_width = max(1, round(self.width / scale) - INSET * 2)
        for text, start, visible in ((self.details, prepared.header_height, detail_height), (self.text, top, body_height)):
            if visible > TEXT_PADDING * 2 * scale:
                offset = text.offset
                text.setGeometry(INSET, round(start / scale) + TEXT_PADDING, text_width, max(1, round(visible / scale) - TEXT_PADDING * 2))
                text.show()
                text.verticalScrollBar().setValue(offset)
            else:
                text.hide()
        if layout.hint_height:
            self.hint.setGeometry(INSET, round((top + body_height) / scale), text_width, max(1, round(layout.hint_height / scale) - 4))
            self.hint.show()
        else:
            self.hint.hide()
        self.window.update()

    def pulse(self, now: float) -> None:
        self.spin_angle = (90 - now * 300) % 360
        self.window.update()

    def position(self, right: int, frame: Frame) -> None:
        # Native bounds can differ by a pixel after Qt rounds at fractional DPI.
        # Compare the requested frame, or an idle tick will undo that rounding
        # repeatedly and keep the shared native event queue busy.
        if self.bounds is not None and self.right == right and self.frame == frame:
            return
        bounds = Rect(right - round(frame.width), round(frame.y), right, round(frame.y + frame.height))
        if self.bounds != bounds or self.frame.details != frame.details:
            self.place_content(bounds.width, bounds.height, frame.details)
            windows.move(self.handle, bounds)
            self.bounds = bounds
        if self.alpha != frame.alpha:
            self.window.setWindowOpacity(max(0.0, min(1.0, frame.alpha)))
        self.right, self.frame = right, frame

    def target(self, y: float, alpha: float, now: float, *, animate: bool, retarget: bool = False) -> None:
        end = Frame(y, self.width, self.height, alpha, self.layout.details_height if self.layout is not None else self.frame.details)
        if not animate:
            self.motion = None
            self.position(self.right, end)
            return
        if not retarget and self.motion is not None and self.motion.end == end:
            return
        current = self.motion.sample(now) if self.motion is not None else self.frame
        self.motion = Motion(current, end, now) if current != end else None

    def reveal(self) -> None:
        self.window.show()
        windows.show_quietly(self.handle, True)
        if self.layer is not None and self.glass:
            self.layer.position(windows.outer_rect(self.handle), self.owner.scale, self.alpha)
            self.layer.show(self.handle)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.window.close()
        if self.layer is not None:
            self.layer.close()
        shiboken6.delete(self.window)
