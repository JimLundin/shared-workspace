"""Selectable, incrementally updated Markdown on a per-pixel transparent surface."""

import math
from collections.abc import Callable

from .qt import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetricsF,
    QFrame,
    QMenu,
    QPalette,
    QPoint,
    Qt,
    QTextBlockFormat,
    QTextBrowser,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
    QWheelEvent,
    QWidget,
    shiboken6,
)
from .rich_text import Span, changed_tail
from .toast_style import Ink

INSET = 16
TEXT_PADDING = 8


def font(size: float = 10.5, *, bold: bool = False, family: str = "Segoe UI") -> QFont:
    value = QFont(family)
    value.setPointSizeF(size)
    value.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return value


def units(text: str) -> int:
    """Qt cursor positions count UTF-16 units, including astral emoji."""
    return len(text.encode("utf-16-le")) // 2


def character_style(span: Span, ink: Ink) -> QTextCharFormat:
    style = QTextCharFormat()
    size = 10.5
    if "heading" in span.tags:
        size = 15 if "h1" in span.tags else 13 if "h2" in span.tags else 11.5
    elif any(tag in span.tags for tag in ("step", "step_detail", "step_error")):
        size = 9.5
    face = font(size, bold="bold" in span.tags or "heading" in span.tags, family="Consolas" if "code" in span.tags else "Segoe UI")
    face.setItalic("italic" in span.tags)
    face.setStrikeOut("strike" in span.tags)
    style.setFont(face)
    color = ink.foreground
    if any(tag in span.tags for tag in ("muted", "quote", "table_label", "rule", "step_detail")):
        color = ink.secondary
    if "step_error" in span.tags:
        color = ink.error
    if span.link:
        color = ink.link
        style.setAnchor(True)
        style.setAnchorHref(span.link)
        style.setFontUnderline(True)
    if "code" in span.tags:
        style.setBackground(QColor(118, 106, 154, 32))
    style.setForeground(QColor(color))
    return style


def paragraph_style(span: Span) -> QTextBlockFormat:
    style = QTextBlockFormat()
    hanging = QFontMetricsF(font()).horizontalAdvance(span.hanging)
    style.setLeftMargin(min(span.indent, 6) * 16 + hanging + (4 if "codeblock" in span.tags else 0))
    style.setTextIndent(-hanging)
    style.setLineHeight(125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
    return style


class Document:
    __slots__ = ("ink", "qt", "spans")

    def __init__(self, parent: QWidget | None = None) -> None:
        self.qt = QTextDocument(parent)
        self.qt.setDefaultFont(font())
        self.qt.setDocumentMargin(0)
        self.qt.setUndoRedoEnabled(False)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.qt.setDefaultTextOption(option)
        self.spans: tuple[Span, ...] = ()
        self.ink = Ink()

    def load(self, spans: tuple[Span, ...], width: float) -> float:
        if spans != self.spans:
            tail = changed_tail(self.spans, spans)
            previous = "".join(span.text for span in self.spans)
            self.spans = spans
            cursor = QTextCursor(self.qt)
            cursor.setPosition(units(previous[:tail.offset]))
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            cursor.beginEditBlock()
            cursor.removeSelectedText()
            for span in tail.spans:
                style = character_style(span, self.ink)
                for part in span.text.splitlines(keepends=True):
                    if part.strip() or cursor.atBlockStart():
                        cursor.setBlockFormat(paragraph_style(span))
                    cursor.insertText(part, style)
            cursor.endEditBlock()
        width = max(1.0, width)
        if self.qt.textWidth() != width:
            self.qt.setTextWidth(width)
        return self.height

    @property
    def height(self) -> float:
        return self.qt.size().height() if self.spans else 0.0

    def intrinsic_width(self, spans: tuple[Span, ...], maximum: float) -> float:
        if not spans:
            return 0
        self.load(spans, maximum)
        widest = 0.0
        block = self.qt.begin()
        while block.isValid():
            layout = block.layout()
            if layout.lineCount() > 1:
                return maximum
            if layout.lineCount():
                line = layout.lineAt(0)
                widest = max(widest, line.x() + line.naturalTextWidth())
            block = block.next()
        return min(maximum, math.ceil(widest) + 1)

    def fit(self, available: float) -> float:
        """End a shortened viewport on a complete rendered line."""
        if self.height <= available:
            return math.ceil(self.height)
        used = 0.0
        block = self.qt.begin()
        document_layout = self.qt.documentLayout()
        while block.isValid():
            layout = block.layout()
            top = document_layout.blockBoundingRect(block).top()
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                bottom = top + line.y() + line.height()
                if math.ceil(bottom) > available:
                    return math.ceil(used)
                used = bottom
            block = block.next()
        return math.ceil(used)

    def recolor(self, ink: Ink) -> None:
        if self.ink == ink:
            return
        self.ink = ink
        cursor = QTextCursor(self.qt)
        cursor.beginEditBlock()
        offset = 0
        for span in self.spans:
            cursor.setPosition(offset)
            offset += units(span.text)
            cursor.setPosition(offset, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(character_style(span, ink))
        cursor.endEditBlock()

    def close(self) -> None:
        shiboken6.delete(self.qt)


class TextView(QTextBrowser):
    __slots__ = ("content", "on_menu", "on_scroll")

    def __init__(self, parent: QWidget, on_scroll: Callable[[int], None], on_menu: Callable[[QPoint], None]) -> None:
        super().__init__(parent)
        self.content = Document(self)
        self.setDocument(self.content.qt)
        self.on_scroll, self.on_menu = on_scroll, on_menu
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenExternalLinks(True)
        self.setUndoRedoEnabled(False)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.setAccessibleName("Conversation text")

    @property
    def offset(self) -> int:
        return self.verticalScrollBar().value()

    def update_spans(self, spans: tuple[Span, ...], width: float) -> None:
        offset = self.offset
        cursor = self.textCursor()
        anchor, position = cursor.anchor(), cursor.position()
        self.content.load(spans, width)
        # QTextDocument edits move the editor's cursor too. Restore both ends:
        # keeping only its position would select all newly inserted text.
        limit = self.document().characterCount() - 1
        cursor.setPosition(min(anchor, limit))
        cursor.setPosition(min(position, limit), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.verticalScrollBar().setValue(offset)

    def recolor(self, ink: Ink) -> None:
        offset = self.offset
        self.content.recolor(ink)
        colors = self.palette()
        colors.setColor(QPalette.ColorRole.Text, QColor(ink.foreground))
        colors.setColor(QPalette.ColorRole.Highlight, QColor("#556797"))
        colors.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        colors.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self.setPalette(colors)
        self.content.qt.size()  # Resolve layout before restoring a pixel position.
        self.verticalScrollBar().setValue(offset)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 — Qt virtual method
        self.on_scroll(event.angleDelta().y())
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        self.on_menu(event.globalPos())
        event.accept()

    def copy_menu(self, point: QPoint, history: Callable[[], None]) -> None:
        menu = QMenu(self)
        copy = menu.addAction("Copy selection", self.copy)
        copy.setEnabled(self.textCursor().hasSelection())
        menu.addSeparator()
        menu.addAction("Agent session", history)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(point)
