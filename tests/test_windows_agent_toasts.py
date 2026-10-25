"""Real notification windows, with controlled time and no microphone or key input."""

import ctypes
import math
import sys
import threading
import time
import unittest
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from ctypes import wintypes
from itertools import pairwise
from typing import Protocol, cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows native agent notifications")

from assistant.agent_session import Answer, Begin, Failed, Question, ReasoningSummary, Said, Session, StreamState, Succeeded, ToolActivity, transition
from assistant.agent_toasts import AgentToasts
from assistant.agent_window import AgentWindow
from assistant.notifications import MAX_VISIBLE, WIDTH, Kind, MessageKey, TurnKey
from assistant.panel import PanelPreferences
from assistant.qt import QFont, QImage, QPainter, QPoint, QRegion, Qt, QTest, QTextCursor, QWidget, shiboken6
from assistant.qt_text import Document, TextView
from assistant.qt_ui import application
from assistant.recording_state import RecordingAction, RecordingState
from assistant.rich_text import formatted
from assistant.toast_card import Card
from winapi import appearance, screen, system, windows
from winapi.shapes import Rect

from .test_agent_session import asking


class Notifications:
    __slots__ = ("agent", "animated", "jobs", "now", "recorded", "requested", "root", "view")

    def __init__(self, *, animated: bool) -> None:
        screen.use_physical_pixels()
        self.root = application()
        self.now = 100.0
        self.animated = animated
        self.jobs: deque[Callable[[], None]] = deque()
        self.requested = threading.Event()
        self.recorded: list[RecordingAction] = []
        self.view: AgentToasts
        self.agent = AgentWindow(
            self.stop, post=self.jobs.append, create_view=self.build,
            on_waiting=lambda waiting: self.requested.set() if waiting else None, on_recording=self.recorded.append,
        )
        self.pump()
        assert self.agent.ready.is_set(), self.agent.all_text()

    def build(self, agent: AgentWindow) -> AgentToasts:
        self.view = AgentToasts(
            self.root, agent, preferences=PanelPreferences(Rect(30, 30, 430, 790)), remember=False,
            clock=lambda: self.now, motion_enabled=lambda: self.animated,
        )
        return self.view

    def pump(self) -> None:
        while self.jobs:
            self.jobs.popleft()()
        self.root.processEvents()
        if hasattr(self, "view") and not self.view.closed:
            self.view.loop.pump()

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.view.refresh()
        self.view.tick()
        self.pump()

    def stop(self) -> None:
        self.agent.stopping()

    def close(self) -> None:
        self.agent.close()
        self.pump()
        self.root.processEvents()


@contextmanager
def notifications(*, animated: bool = False) -> Generator[Notifications]:
    # The user's actual pointer must not affect a deterministic expiry check.
    with patch("assistant.agent_toasts.inputs.cursor_position", return_value=(-100000, -100000)):
        panel = Notifications(animated=animated)
        try:
            yield panel
        finally:
            panel.close()


def scroll_position(card: Card) -> int:
    return card.text.offset


def select(text: TextView, value: str) -> None:
    cursor = text.document().find(value)
    assert not cursor.isNull()
    text.setTextCursor(cursor)


def tail_visible(text: TextView) -> bool:
    cursor = QTextCursor(text.document())
    cursor.movePosition(QTextCursor.MoveOperation.End)
    bounds = text.cursorRect(cursor)
    return text.isVisible() and bounds.top() >= 0 and bounds.bottom() < text.viewport().height() + 1


def anchor(text: TextView) -> int:
    return text.cursorForPosition(QPoint(0, 1)).position()


def widget_rect(widget: QWidget) -> Rect:
    outer = windows.outer_rect(int(widget.window().winId()))
    point = widget.mapTo(widget.window(), QPoint(0, 0))
    scale = widget.devicePixelRatioF()
    left, top = outer.left + round(point.x() * scale), outer.top + round(point.y() * scale)
    return Rect(left, top, left + round(widget.width() * scale), top + round(widget.height() * scale))


def top(area: Rect) -> int:
    return area.top


class WindowAt(Protocol):
    argtypes: tuple[object, ...]
    restype: object

    def __call__(self, point: wintypes.POINT) -> int: ...


class WindowClass(Protocol):
    argtypes: tuple[object, ...]
    restype: object

    def __call__(self, handle: int, name: ctypes.Array[ctypes.c_wchar], count: int) -> int: ...


def test_long_reply_keeps_its_ending_scrolls_and_stays_until_read() -> None:
    with notifications(animated=True) as panel:
        text = "A useful detail. " * 170 + "\n\nThe final instruction is to save the workbook."
        panel.agent.say(text)
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[MessageKey(0)]
        assert card.text.toPlainText() == "".join(span.text for span in formatted(text))
        assert scroll_position(card) == 0
        panel.advance(31)
        assert panel.view.cards[MessageKey(0)] is card and scroll_position(card) > 0
        panel.advance(10000)
        assert tail_visible(card.text)
        panel.advance(31)
        panel.advance(.3)
        assert not panel.view.cards


def test_long_replies_take_turns_scrolling_instead_of_expiring_unread() -> None:
    with notifications(animated=True) as panel:
        panel.agent.say("First answer. " * 220)
        panel.agent.say("Second answer. " * 220)
        panel.pump()
        panel.advance(.3)
        first, second = panel.view.cards[MessageKey(0)], panel.view.cards[MessageKey(1)]
        panel.advance(31)
        assert scroll_position(first) > 0 and scroll_position(second) == 0
        assert not first.retiring and not second.retiring


def test_wheel_pauses_reading_and_streaming_preserves_the_chosen_position() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Read the notes", panel.stop)
        publish = panel.agent.updates()
        text = "A useful detail. " * 220
        publish(Answer("stream", text, 1))
        panel.pump()
        panel.advance(.3)
        panel.advance(31)
        card = panel.view.cards[TurnKey(0)]
        before = scroll_position(card)
        assert card.bounds is not None
        with patch("assistant.agent_toasts.inputs.cursor_position", return_value=(card.bounds.left + 8, card.bounds.top + 8)):
            card.scroll_delta(120)
            panel.view.observe_pointer()
            panel.pump()
            assert scroll_position(card) < before and card.hovered
            position = anchor(card.text)
            panel.now += 20
            panel.view.tick()
            panel.pump()
            assert anchor(card.text) == position
            publish(Answer("stream", text + "\n\nMore details arrive.", 2))
            panel.pump()
            assert anchor(card.text) == position


def test_reduced_motion_keeps_the_whole_reply_and_advances_by_pages() -> None:
    with notifications() as panel:
        panel.agent.say("A useful detail. " * 220 + "\n\nThe end.")
        panel.pump()
        card = panel.view.cards[MessageKey(0)]
        assert scroll_position(card) == 0
        positions: list[int] = []
        for _ in range(20):
            panel.advance(1)
            positions.append(scroll_position(card))
        assert positions == sorted(positions) and len(set(positions)) < len(positions)
        panel.advance(10000)
        assert tail_visible(card.text) and "The end." in card.text.toPlainText()


def test_wheel_over_empty_glass_reaches_text_without_focusing_other_apps() -> None:
    with notifications() as panel, patch("assistant.agent_panel.windows.focus"):
        panel.agent.say("A useful detail. " * 220)
        panel.pump()
        card = panel.view.cards[MessageKey(0)]
        router = panel.view.wheel
        assert router.ready.wait(timeout=2) and router.active, router.error
        bounds = windows.outer_rect(card.handle)
        foreground = windows.foreground()
        assert not router.dispatch(bounds.left - 1, bounds.top, -120)
        assert router.dispatch(bounds.left + 2, bounds.top + 2, -120)
        deadline = time.monotonic() + 1
        while scroll_position(card) == 0 and time.monotonic() < deadline:
            panel.pump()
            time.sleep(.005)
        assert scroll_position(card) > 0 and windows.foreground() == foreground
        panel.view.show(focus=True)
        panel.pump()
        assert not router.regions and not router.dispatch(bounds.left + 2, bounds.top + 2, -120)
        panel.agent.close()
        panel.pump()
        assert router.thread is not None and not router.thread.is_alive() and not router.active


def test_compact_header_and_native_stop_padding_are_clickable_without_activation() -> None:
    with notifications() as panel:
        panel.view.material_preference = lambda: appearance.Material.GLASS
        panel.agent.begin("Check the notes", panel.stop)
        panel.pump()
        card = panel.view.cards[TurnKey(0)]
        bounds, stop = windows.outer_rect(card.handle), widget_rect(card.stop)
        assert bounds.width == card.width < round(WIDTH * panel.view.scale * .65)
        assert stop.width >= 28 and stop.height >= 28 and bounds.left < stop.left < stop.right < bounds.right
        foreground = windows.foreground()
        # Check the native window under the icon's padding, then send the click
        # to that HWND. This exercises Qt's actual child hit test and event path.
        point = wintypes.POINT(stop.left + 4, stop.top + 4)
        hit = cast(WindowAt, ctypes.WinDLL("user32").WindowFromPoint)
        hit.argtypes, hit.restype = (cast(object, wintypes.POINT),), ctypes.c_void_p
        deadline = time.monotonic() + 1
        while int(hit(point) or 0) != card.handle and time.monotonic() < deadline:
            panel.pump()
            time.sleep(.01)
        observed = int(hit(point) or 0)
        class_name = ctypes.create_unicode_buffer(256)
        window_class = cast(WindowClass, ctypes.WinDLL("user32").GetClassNameW)
        window_class.argtypes, window_class.restype = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int), ctypes.c_int
        window_class(observed, class_name, len(class_name))
        if class_name.value == "LockScreenBackstopFrame":
            raise unittest.SkipTest("Windows is locked; the lock screen covers the native click target.")
        assert int(hit(point) or 0) == card.handle, (card.handle, int(hit(point) or 0), bounds, stop, card.alpha, card.window.isVisible())
        if card.layer is not None:
            for empty in (wintypes.POINT(bounds.left + 1, bounds.top + 1), wintypes.POINT(bounds.left - 8, bounds.top + 20)):
                assert int(hit(empty) or 0) not in (card.handle, card.layer.handle), "Rounded corners and shadows must pass clicks through."
        local = (point.x - bounds.left) | ((point.y - bounds.top) << 16)
        assert system.post_message(card.handle, 0x0201, 1, local)
        assert system.post_message(card.handle, 0x0202, 0, local)
        panel.pump()
        panel.pump()
        assert panel.agent.state.stopping and not card.stop.isEnabled()
        assert windows.foreground() == foreground


def test_stop_has_no_different_fill_in_normal_pressed_or_disabled_states() -> None:
    with notifications() as panel:
        panel.agent.begin("Check the notes", panel.stop)
        panel.pump()
        button = panel.view.cards[TurnKey(0)].stop
        for enabled, down in ((True, False), (True, True), (False, False)):
            button.setEnabled(enabled)
            button.setDown(down)
            image = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            button.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
            painter.end()
            # The background is supplied only by the parent bubble, even while
            # pressed. The button paints its icon and optional focus outline.
            assert image.pixelColor(1, 1).alpha() <= 1


def test_background_material_sampling_never_waits_for_desktop_pixels() -> None:
    started, release = threading.Event(), threading.Event()
    sampler = appearance.FrostSampler()
    bounds = Rect(10, 20, 300, 400)
    calls: list[tuple[Rect, ...]] = []

    def sample(rects: tuple[Rect, ...]) -> tuple[appearance.Backdrop, ...]:
        calls.append(rects)
        started.set()
        assert release.wait(2)
        return tuple(appearance.Backdrop(rect, appearance.Frost.LIGHT) for rect in rects)

    with patch.object(appearance, "sample_backgrounds", sample):
        try:
            sampler.submit((bounds,))
            assert started.wait(1)
            assert sampler.read() == ()
            sampler.submit((Rect(0, 0, 500, 600),))
            assert calls == [(bounds,)]
            release.set()
            assert sampler.pending is not None
            sampler.pending.result(timeout=1)
            assert sampler.read() == (appearance.Backdrop(bounds, appearance.Frost.LIGHT),)
            assert sampler.read() == ()
        finally:
            release.set()
            sampler.close()


def test_material_modes_choose_readable_ink_and_reject_stale_backgrounds() -> None:
    dark = tuple(map(appearance.colorref, ("#181d2a", "#353c50")))
    assert appearance.frost_for(dark) is appearance.Frost.LIGHT
    assert appearance.frost_for((appearance.colorref("#fafbfe"),)) is appearance.Frost.BRIGHT
    assert appearance.frost_for((*dark, appearance.colorref("#ffffff"))) is appearance.Frost.SMOKED
    assert appearance.frost_for(()) is appearance.Frost.SMOKED
    with notifications() as panel:
        panel.view.material_preference = lambda: appearance.Material.GLASS
        panel.agent.say("The workbook is ready.")
        panel.pump()
        card = panel.view.cards[MessageKey(0)]
        if not card.glass:
            return
        assert card.bounds is not None
        select(card.text, "workbook")
        for frost, foreground in ((appearance.Frost.LIGHT, "#f6f3ff"), (appearance.Frost.BRIGHT, "#202435"),
                                  (appearance.Frost.SMOKED, "#f6f3ff")):
            card.match_background(appearance.Backdrop(card.bounds, frost), appearance.Material.GLASS)
            assert card.ink.foreground == foreground
            assert card.text.textCursor().selectedText() == "workbook"
        card.match_background(appearance.Backdrop(Rect(0, 0, 1, 1), appearance.Frost.BRIGHT), appearance.Material.GLASS)
        assert card.frost is appearance.Frost.SMOKED
        assert card.text.toPlainText() == "The workbook is ready."


def test_material_changes_preserve_the_native_scroll_range_and_reading_position() -> None:
    with notifications() as panel:
        panel.agent.say("A useful detail. " * 220 + "\n\nThe end.")
        panel.pump()
        card = panel.view.cards[MessageKey(0)]
        assert card.bounds is not None
        card.scroll_delta(-480)
        panel.pump()
        position, original = scroll_position(card), anchor(card.text)
        assert position > 0
        for frost in (appearance.Frost.LIGHT, appearance.Frost.SMOKED, appearance.Frost.BRIGHT) * 3:
            card.frost = frost
            assert card.content is not None
            card.apply_style(card.content, appearance.Material.GLASS)
            panel.pump()
            assert scroll_position(card) == position and anchor(card.text) == original
            scroll = card.text.verticalScrollBar()
            assert 0 <= scroll.value() <= scroll.maximum()


def test_one_bubble_keeps_its_result_when_finished_and_when_steps_collapse() -> None:
    with notifications() as panel:
        foreground = windows.foreground()
        panel.agent.begin("Read the workbook", panel.stop)
        panel.pump()
        card = panel.view.cards[TurnKey(0)]
        handle, text = card.handle, card.text
        publish = panel.agent.updates()
        publish(ToolActivity("read", "read_file", '{"path":"budget.xlsx"}', 1, Succeeded('{"rows":24}')))
        publish(ToolActivity("check", "read_file", "forecast.xlsx", 2, Succeeded("The totals match.")))
        publish(Answer("answer", "The workbook is **ready**.", 3, StreamState.COMPLETE))
        panel.pump()
        assert len(panel.view.cards) == 2 and panel.view.cards[TurnKey(0)] is card
        assert card.details.isHidden() and card.text.toPlainText() == "The workbook is ready."
        compact, stop = windows.outer_rect(handle), widget_rect(card.stop)
        card.disclosure.click()
        panel.pump()
        expanded = windows.outer_rect(handle)
        assert card.expanded and expanded.height > compact.height
        steps = card.details.toPlainText()
        assert "Path: budget.xlsx" in steps and "Rows: 24" in steps
        assert steps.index("budget.xlsx") < steps.index("forecast.xlsx")
        assert card.details.geometry().bottom() < card.text.y()
        assert widget_rect(card.stop).right == stop.right
        panel.agent.finish()
        panel.pump()
        assert panel.view.cards[TurnKey(0)] is card and card.handle == handle
        assert card.stop.isHidden() and card.text.document().find("ready").charFormat().fontWeight() >= QFont.Weight.DemiBold
        card.disclosure.click()
        panel.pump()
        assert not bool(card.expanded) and card.details.isHidden()
        assert card.text is text and card.text.toPlainText() == "The workbook is ready." and tail_visible(card.text)
        assert windows.outer_rect(handle).height < expanded.height
        assert windows.foreground() == foreground


def test_reversing_disclosure_preserves_selection_and_visible_attached_result() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Check the workbook", panel.stop)
        publish = panel.agent.updates()
        for index in range(4):
            publish(ToolActivity(str(index), "read_file", f"sheet-{index}.xlsx", index + 1, Succeeded("Read 24 rows.")))
        answer = "The result stays attached while you open and close the steps."
        publish(Answer("answer", answer, 5, StreamState.COMPLETE))
        panel.agent.finish()
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[TurnKey(0)]
        select(card.text, "result")
        before = windows.outer_rect(card.handle)
        card.disclosure.click()
        panel.pump()
        assert windows.outer_rect(card.handle) == before
        assert card.motion is not None and card.motion.end.details > 0
        for _ in range(5):
            panel.now += .016
            panel.view.tick()
            panel.pump()
            assert card.text.toPlainText() == answer and card.text.height() > 20
            if card.details.isVisible():
                assert card.details.geometry().bottom() < card.text.y()
        current = windows.outer_rect(card.handle)
        card.disclosure.click()
        panel.pump()
        assert windows.outer_rect(card.handle) == current
        panel.advance(.3)
        assert not bool(card.motion) and card.frame.details == 0 and card.details.isHidden()
        assert card.text.textCursor().selectedText() == "result" and tail_visible(card.text)


def test_long_steps_scroll_independently_and_keep_the_result_on_reopen() -> None:
    with notifications() as panel:
        panel.agent.begin("Check the workbooks", panel.stop)
        publish = panel.agent.updates()
        for index in range(15):
            publish(ToolActivity(str(index), "read_file", f"sheet-{index}.xlsx", index + 1, Succeeded("Read 24 rows.")))
        publish(Answer("answer", "All fifteen sheets match.", 20, StreamState.COMPLETE))
        panel.agent.finish()
        panel.pump()
        card = panel.view.cards[TurnKey(0)]
        card.toggle(animate=False)
        panel.pump()
        assert card.details.toPlainText().count("Read file") == 15 and card.details.verticalScrollBar().maximum() > 0
        reply = anchor(card.text)
        card.scroll_details(-240)
        panel.pump()
        position = card.details.offset
        assert position > 0 and anchor(card.text) == reply and tail_visible(card.text)
        assert any(region.target == id(card.details) for region in panel.view.wheel.regions)
        card.toggle(animate=False)
        panel.pump()
        assert not panel.view.wheel.regions and card.text.toPlainText() == "All fifteen sheets match."
        card.toggle(animate=False)
        panel.pump()
        assert card.details.offset == position and anchor(card.text) == reply


def test_keyboard_disclosure_is_immediate_and_keeps_the_result_attached() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Read the workbook", panel.stop)
        publish = panel.agent.updates()
        publish(ToolActivity("read", "read_file", "budget.xlsx", 1, Succeeded("24 rows")))
        publish(Answer("reply", "The workbook is ready.", 2, StreamState.COMPLETE))
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[TurnKey(0)]
        for key, expanded in ((Qt.Key.Key_Return, True), (Qt.Key.Key_Space, False)):
            QTest.keyClick(card.disclosure, key)
            panel.pump()
            assert card.expanded is expanded and card.motion is None
            assert card.text.toPlainText() == "The workbook is ready." and tail_visible(card.text)


def test_long_result_keeps_its_reading_position_while_steps_open_and_close() -> None:
    with notifications() as panel:
        panel.agent.begin("Read the workbook", panel.stop)
        publish = panel.agent.updates()
        publish(ToolActivity("read", "read_file", "budget.xlsx", 1, Succeeded("24 rows")))
        publish(Answer("reply", "A useful detail. " * 220, 2, StreamState.COMPLETE))
        panel.agent.finish()
        panel.pump()
        card = panel.view.cards[TurnKey(0)]
        card.scroll_delta(-240)
        panel.pump()
        position, first = card.text.offset, anchor(card.text)
        assert position > 0
        for _ in range(4):
            card.toggle(animate=False)
            panel.pump()
            assert card.text.offset == position and anchor(card.text) == first


def test_native_shadow_follows_the_foreground_after_fractional_dpi_rounding() -> None:
    with notifications(animated=True) as panel:
        panel.agent.say("Ready.")
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[MessageKey(0)]
        if card.layer is None:
            return
        panel.view.render(transition(Session(identity=panel.agent.state.identity), Said("Ready to review the plan.", 1)))
        panel.pump()
        for _ in range(18):
            panel.now += .016
            panel.view.tick()
            panel.pump()
            bounds = windows.outer_rect(card.handle)
            padding = round(28 * panel.view.scale)
            assert card.bounds == bounds
            assert card.layer.bounds == Rect(bounds.left - padding, bounds.top - padding, bounds.right + padding, bounds.bottom + padding)


def test_short_replies_grow_horizontally_then_wrap_at_the_width_limit() -> None:
    with notifications() as panel:
        sizes: list[tuple[int, int]] = []
        for text in ("Ready.", "Ready to review.", "Ready to review the plan.", "Ready to review the plan. " * 4):
            panel.view.render(transition(Session(identity="width-probe"), Said(text, 1)))
            panel.pump()
            card = panel.view.cards[MessageKey(0)]
            sizes.append((card.width, card.height))
            assert windows.outer_rect(card.handle).width == card.width and tail_visible(card.text)
        cap = round(WIDTH * panel.view.scale)
        assert sizes[0][0] < sizes[1][0] < sizes[2][0] < sizes[3][0] == cap
        assert sizes[0][1] == sizes[1][1] == sizes[2][1] < sizes[3][1]


def test_rendered_markdown_width_ignores_link_destinations_and_wraps_long_words() -> None:
    with notifications() as panel:
        widths: list[int] = []
        for text in ("## A heading\n\n**Ready**\n\n- OK", "[Docs](https://example.org/" + "a" * 160 + ")", "W" * 80):
            panel.view.render(transition(Session(identity="width-probe"), Said(text, 1)))
            panel.pump()
            card = panel.view.cards[MessageKey(0)]
            widths.append(card.width)
            block = card.text.document().begin()
            while block.isValid():
                layout = block.layout()
                for index in range(layout.lineCount()):
                    line = layout.lineAt(index)
                    assert line.x() + line.naturalTextWidth() <= card.text.width() + 1
                block = block.next()
        assert widths[1] < widths[0] < widths[2] == round(WIDTH * panel.view.scale)


def test_width_changes_retarget_from_current_bounds_with_right_edge_anchored() -> None:
    with notifications(animated=True) as panel:
        panel.view.render(transition(Session(identity="width-probe"), Said("Ready.", 1)))
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[MessageKey(0)]
        right = windows.outer_rect(card.handle).right
        for text in ("Ready to review.", "Ready to review the plan. " * 4, "**Done.**"):
            before = windows.outer_rect(card.handle)
            panel.view.render(transition(Session(identity="width-probe"), Said(text, 1)))
            panel.pump()
            assert windows.outer_rect(card.handle) == before
            widths: list[int] = []
            for _ in range(4):
                panel.now += .016
                panel.view.tick()
                panel.pump()
                bounds = windows.outer_rect(card.handle)
                # Qt maps integer logical pixels to physical pixels at 125%.
                assert abs(bounds.right - right) <= 1
                assert min(before.width, card.width) - 1 <= bounds.width <= max(before.width, card.width) + 1
                widths.append(bounds.width)
            assert len(set(widths)) > 1
        panel.advance(.3)
        assert windows.outer_rect(card.handle).width == card.width and card.motion is None


def test_narrow_work_area_keeps_complete_question_lines_above_voice_hint() -> None:
    with notifications() as panel, patch.object(AgentToasts, "area", return_value=Rect(40, 40, 290, 600)):
        state = transition(Session(identity="narrow-area"), Begin("Check the notes", 1))
        state = transition(state, Question("question", "Which workbook should I use?", "Please say the filename.", 2))
        panel.view.render(state)
        panel.pump()
        for card in panel.view.cards.values():
            assert abs(windows.outer_rect(card.handle).width - card.width) <= 1
            assert card.width <= 250 - 2 * round(16 * panel.view.scale)
        question = panel.view.cards[TurnKey(0)]
        assert question.text.geometry().bottom() < question.hint.y()
        assert question.hint.text() == "Hold N to reply by voice"
        assert question.hint.height() >= question.hint.heightForWidth(question.hint.width())
        assert tail_visible(question.text)


def test_streaming_never_resizes_a_visible_card_for_measurement() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Read the notes", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("stream", "A short answer.", 1))
        panel.pump()
        panel.advance(.3)
        handles = {card.handle for card in panel.view.cards.values()}
        before = {handle: windows.outer_rect(handle) for handle in handles}
        moves: list[tuple[int, Rect]] = []
        original = windows.move

        def move(handle: int, bounds: Rect) -> None:
            if handle in handles:
                moves.append((handle, bounds))
            original(handle, bounds)

        with patch.object(windows, "move", move):
            publish(Answer("stream", "A short answer. " * 70, 2))
            panel.pump()
        assert all(bounds == before[handle] for handle, bounds in moves)


def test_stream_growth_moves_neighbors_together_without_overlap() -> None:
    with notifications(animated=True) as panel:
        panel.agent.say("Earlier context. " * 25)
        panel.agent.begin("Summarize the notes", panel.stop)
        publish = panel.agent.updates()
        text = "The summary follows."
        publish(Answer("stream", text, 1))
        panel.pump()
        panel.advance(.3)
        for index in range(40):
            panel.now += .016
            panel.view.tick()
            panel.pump()
            if index in (0, 2, 5, 8, 12):
                text += "\n\nA **formatted** finding with more detail. " * 2
                publish(Answer("stream", text, 2))
                panel.pump()
            bounds = sorted((windows.outer_rect(card.handle) for card in panel.view.cards.values()), key=top)
            assert all(first.bottom < second.top for first, second in pairwise(bounds))
            assert panel.view.work is not None and bounds[0].top >= panel.view.work.top and bounds[-1].bottom < panel.view.work.bottom
        assert all(card.motion is None for card in panel.view.cards.values())


def test_streaming_unicode_and_closing_markup_preserve_the_unchanged_selection() -> None:
    with notifications() as panel:
        panel.agent.begin("Summarize the notes", panel.stop)
        publish = panel.agent.updates()
        prefix = "Åäö 👩🏽‍💻 e\u0301: "
        publish(Answer("stream", prefix + "**bold", 1))
        panel.pump()
        card = panel.view.cards[TurnKey(0)]
        select(card.text, "👩🏽‍💻")
        for suffix in ("**bold**", "**bold** and [docs](https://example.org", "**bold** and [docs](https://example.org).",
                       "**bold** and [docs](https://example.org).\n\n```python\nprint('ok')"):
            text = prefix + suffix
            publish(Answer("stream", text, 2))
            panel.pump()
            assert card.text.toPlainText() == "".join(span.text for span in formatted(text))
            assert card.text.textCursor().selectedText() == "👩🏽‍💻"
        assert card.text.document().find("bold").charFormat().fontWeight() >= QFont.Weight.DemiBold
        assert card.text.document().find("docs").charFormat().anchorHref() == "https://example.org"


def test_streaming_reveals_only_new_text_and_releases_animation_timing() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Summarize the notes", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("stream", "First.", 1))
        panel.pump()
        panel.advance(.3)
        reply = panel.view.cards[TurnKey(0)]
        for index in range(30):
            publish(Answer("stream", "First." + " a" * (index + 1), 2))
            panel.pump()
            assert all(selection.cursor.selectionStart() >= 6 for selection in reply.text.extraSelections())
            assert len(reply.text.extraSelections()) <= 12
        assert reply.text.extraSelections()
        final = "First." + " a" * 30
        publish(Answer("stream", final, 3, StreamState.COMPLETE))
        panel.agent.done(final)
        panel.agent.finish()
        panel.pump()
        panel.advance(.3)
        assert reply.text.toPlainText() == final and not reply.text.extraSelections()
        assert panel.view.animation_timer is None and not panel.view.timing.active


def test_motion_timing_is_balanced_on_reduced_motion_history_and_close() -> None:
    calls: list[str] = []

    def begin(period: int) -> int:
        assert period == 1
        calls.append("begin")
        return 0

    def end(period: int) -> int:
        assert period == 1
        calls.append("end")
        return 0

    with patch.object(appearance, "_TimeBeginPeriod", begin), patch.object(appearance, "_TimeEndPeriod", end):
        with notifications(animated=True) as panel:
            panel.agent.begin("Read the notes", panel.stop)
            panel.pump()
            assert (panel.view.timing.active, calls) == (True, ["begin"])
            panel.animated = False
            panel.advance(.016)
            assert (panel.view.timing.active, calls) == (False, ["begin", "end"])
            panel.animated = True
            panel.agent.say("A reply.")
            panel.pump()
            assert panel.view.timing.active
            panel.view.show(focus=True)
            assert not bool(panel.view.timing.active)
            panel.view.resume()
            panel.pump()
            assert panel.view.timing.active
        assert calls == ["begin", "end"] * 3


def test_measured_previews_end_on_complete_lines_with_mixed_markdown_fonts() -> None:
    with notifications() as panel:
        document = panel.view.measure.body
        for source in ("A wrapped paragraph with Åäö and 👩🏽‍💻. " * 12,
                       "# Heading\n\nA **bold** paragraph.\n\n- An item that wraps across several lines in a narrow preview.\n"
                       "- Another item.\n\n```python\nprint('hello')\n```\n\nEnd."):
            wanted = document.load(tuple(formatted(source)), 260)
            boundaries = [0, math.ceil(wanted)]
            block = document.qt.begin()
            while block.isValid():
                layout = block.layout()
                top = document.qt.documentLayout().blockBoundingRect(block).top()
                for index in range(layout.lineCount()):
                    line = layout.lineAt(index)
                    boundaries.append(math.ceil(top + line.y() + line.height()))
                block = block.next()
            for available in range(1, min(math.ceil(wanted) + 1, 500)):
                fitted = document.fit(available)
                assert fitted in boundaries and fitted <= available


def test_notifications_are_idle_until_needed_and_leave_focus_untouched() -> None:
    with notifications() as panel:
        view = panel.view
        assert not view.cards and view.history is None and not view.loop.timer.isActive()
        foreground = windows.foreground()
        panel.agent.begin("Find my workbook", panel.stop)
        panel.pump()
        work, request = view.cards[TurnKey(0)], view.cards[MessageKey(0)]
        assert windows.is_visible(work.handle) and work.stop.isVisible()
        assert request.hint.text() == "You" and request.text.toPlainText() == "Find my workbook"
        assert not request.text.textCursor().hasSelection()
        assert request.width < round(WIDTH * view.scale) and work.text.isHidden()
        publish = panel.agent.updates()
        with patch.object(Document, "load", side_effect=AssertionError("Reasoning must not measure notification text")):
            publish(ReasoningSummary("thought", "Checking **recent files**.", 1))
            panel.pump()
        assert view.cards[TurnKey(0)] is work and work.text.toPlainText() == ""
        panel.advance(3600)
        publish(Answer("reply", "Found **budget.xlsx**.", 2, StreamState.COMPLETE))
        panel.agent.done("Found **budget.xlsx**.")
        panel.agent.finish()
        panel.pump()
        assert view.cards[TurnKey(0)] is work and len(view.cards) == 2 and work.stop.isHidden()
        assert work.height <= work.maximum and tail_visible(work.text)
        assert not work.text.textCursor().hasSelection()
        panel.advance(31)
        assert not view.cards and view.expiry_timer is None and not view.loop.timer.isActive()
        panel.agent.show()
        panel.pump()
        assert not view.cards and view.history is None
        assert "Found **budget.xlsx**." in panel.agent.all_text() and windows.foreground() == foreground


def test_stack_is_bounded_during_bursts_and_closes_retired_native_layers() -> None:
    with notifications(animated=True) as panel:
        old_handles: list[int] = []
        for index in range(12):
            panel.agent.say(f"Reply {index} with **Markdown**.")
            panel.pump()
            old_handles.extend(card.layer.handle for card in panel.view.cards.values() if card.layer is not None)
            assert len(panel.view.cards) <= MAX_VISIBLE
        panel.advance(.5)
        assert all(card.motion is None for card in panel.view.cards.values())
        bounds = sorted((windows.outer_rect(card.handle) for card in panel.view.cards.values()), key=top)
        assert panel.view.work is not None and bounds[0].top >= panel.view.work.top and bounds[-1].bottom < panel.view.work.bottom
        assert all(first.bottom < second.top for first, second in pairwise(bounds))
        panel.advance(31)
        panel.advance(.3)
        assert not panel.view.cards and all(not windows.exists(handle) for handle in old_handles)


def test_repeating_a_frame_keeps_native_geometry_stable_after_dpi_rounding() -> None:
    with notifications(animated=True) as panel:
        panel.agent.say("Reply with **Markdown**.")
        panel.pump()
        card = panel.view.cards[MessageKey(0)]
        bounds = windows.outer_rect(card.handle)
        with patch.object(windows, "move", side_effect=AssertionError("An unchanged frame must not reposition native windows.")):
            for _ in range(3):
                card.position(card.right, card.frame)
                panel.view.loop.pump()
        assert windows.outer_rect(card.handle) == bounds


def test_spinner_reuses_widgets_and_does_not_measure_text_on_ticks() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Find **the workbook**", panel.stop)
        panel.pump()
        panel.advance(.3)
        work = panel.view.cards[TurnKey(0)]
        widgets, angle = work.window.findChildren(QWidget), work.spin_angle
        with patch.object(Document, "load", side_effect=AssertionError("Spinner must not lay out text")):
            panel.now += .1
            panel.view.tick()
            panel.pump()
        assert work.spin_angle != angle and work.window.findChildren(QWidget) == widgets
        panel.animated = False
        panel.view.tick()
        assert panel.view.animation_timer is None
        panel.animated = True
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            panel.advance(.3)
            assert panel.view.animation_timer is None
            assert panel.agent.reply_by_voice("The workbook on my desktop")
            assert pending.result() == "The workbook on my desktop"
        panel.agent.finish()
        panel.pump()
        panel.advance(.3)
        assert panel.view.animation_timer is None and panel.view.cards


def test_solid_and_high_contrast_materials_keep_all_content_readable() -> None:
    with notifications() as panel:
        panel.view.material_preference = lambda: appearance.Material.SOLID
        panel.agent.begin("Read the workbook", panel.stop)
        panel.pump()
        work = panel.view.cards[TurnKey(0)]
        assert not work.glass and work.colors.surface != panel.view.cards[MessageKey(0)].colors.surface
        publish = panel.agent.updates()
        publish(ToolActivity("tool", "read_file", "budget.xlsx", 1, Failed("Missing file")))
        panel.pump()
        work.toggle(animate=False)
        panel.pump()
        assert "Missing file" in work.details.toPlainText()
        panel.view.material_preference = lambda: appearance.Material.GLASS
        with patch("winapi.composition.DesktopComposition.glass", side_effect=OSError("Unavailable")):
            panel.view.refresh()
            panel.pump()
            assert not work.glass and work.details.isVisible()
        panel.view.material_preference = lambda: appearance.Contrast("#ffffff", "#000000")
        panel.view.refresh()
        panel.pump()
        assert not work.glass and work.colors.surface == "#ffffff"
        assert work.ink.foreground == work.ink.secondary == "#000000"
        assert work.details.document().find("Missing file").charFormat().foreground().color().name() == "#000000"


def test_hovering_glass_pauses_expiry_and_close_cancels_all_observation() -> None:
    with notifications() as panel:
        panel.agent.say("A reply to read.")
        panel.pump()
        card = next(iter(panel.view.cards.values()))
        bounds = windows.outer_rect(card.handle)
        assert panel.view.pointer_timer is not None
        panel.now += 5
        with patch("assistant.agent_toasts.inputs.cursor_position", return_value=(bounds.right - 12, bounds.bottom - 12)):
            panel.view.observe_pointer()
            assert panel.view.expiry_timer is None
            panel.advance(100)
            assert card.key in panel.view.cards
        panel.view.observe_pointer()
        panel.advance(6)
        assert card.key in panel.view.cards
        panel.advance(2)
        assert not panel.view.cards and not bool(panel.view.pointer_timer)
        panel.agent.say("Another reply.")
        panel.pump()
        panel.agent.close()
        panel.pump()
        assert not bool(panel.view.pointer_timer) and panel.view.loop.closed and not shiboken6.isValid(panel.view.loop.timer)


def test_questions_can_be_answered_or_stopped_by_voice_without_history() -> None:
    with notifications() as panel:
        panel.agent.begin("Open the workbook", panel.stop)
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            panel.advance(3600)
            question = panel.view.cards[TurnKey(0)]
            assert "Hold N" in question.hint.text() and question.hint.isVisible()
            assert "Open this?" in question.text.toPlainText()
            assert panel.agent.reply_by_voice("The revenue sheet.")
            assert pending.result() == "The revenue sheet."
        panel.pump()
        assert panel.view.history is None and panel.view.cards[TurnKey(0)] is question
        assert "The revenue sheet." in question.text.toPlainText() and "Open this?" in question.text.toPlainText()
        panel.requested.clear()
        with asking(panel.agent.inputs) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            approval = panel.view.cards[TurnKey(0)]
            assert "approve" in approval.hint.text() and approval.hint.isVisible()
            approval.stop.click()
            assert pending.result() is None
        panel.pump()
        assert not approval.stop.isEnabled() and panel.view.history is None


def test_history_keeps_expired_results_and_returns_to_current_conversation() -> None:
    with notifications() as panel, patch("assistant.agent_panel.windows.focus"):
        panel.agent.begin("Find it", panel.stop)
        panel.agent.say("## Result\n\n" + "A long response with **formatting**. " * 120)
        panel.agent.finish()
        panel.pump()
        reply = panel.view.cards[TurnKey(0)]
        assert reply.height <= reply.maximum and reply.text.toPlainText().count("A long response") == 120
        panel.advance(10000)
        panel.advance(31)
        assert not panel.view.cards
        panel.agent.show(focus=True)
        panel.pump()
        history = panel.view.history
        assert history is not None and windows.is_visible(history.handle)
        assert history.text.toPlainText().count("A long response") == 120
        panel.agent.begin("Continue with that result", panel.stop)
        panel.pump()
        assert not panel.view.cards
        history.hide()
        panel.pump()
        assert not windows.is_visible(history.handle) and TurnKey(2) in panel.view.cards
        assert MessageKey(2) in panel.view.cards and MessageKey(0) not in panel.view.cards
        panel.agent.say("The follow-up is done.")
        panel.agent.finish()
        panel.pump()
        follow_up = next(iter(panel.view.cards.values()))
        panel.view.set_hover(follow_up.key, True)
        panel.view.show(focus=True)
        panel.advance(31)
        assert not panel.view.stack.toasts
        history.hide()
        panel.pump()
        assert not panel.view.cards


def test_recording_stop_is_independent_and_shutdown_closes_every_native_layer() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Find the workbook", panel.stop)
        panel.agent.recording(RecordingState(phase="recording", identity="meeting", seconds=3723, preview="**00:00:00**  First sentence."))
        panel.pump()
        recording = panel.view.cards[Kind.RECORDING]
        assert "01:02:03" in recording.label.text()
        recording.stop.click()
        assert panel.recorded == ["stop"] and not panel.agent.state.stopping
        handles: list[int] = []
        for card in panel.view.cards.values():
            handles.append(card.handle)
            if card.layer is not None:
                handles.append(card.layer.handle)
        panel.agent.close()
        panel.pump()
        assert panel.agent.closed_ready.is_set() and not panel.view.cards
        assert panel.view.animation_timer is None and panel.view.expiry_timer is None and not shiboken6.isValid(panel.view.loop.timer)
        assert all(not windows.exists(handle) for handle in handles)


def test_recording_preview_keeps_the_latest_transcript_visible() -> None:
    with notifications() as panel:
        preview = "**00:00:00**  " + "An earlier sentence. " * 150 + "\n\n**00:15:00**  The newest sentence."
        panel.agent.recording(RecordingState(phase="recording", identity="meeting", seconds=900, preview=preview))
        panel.pump()
        card = panel.view.cards[Kind.RECORDING]
        assert "The newest sentence." in card.text.toPlainText() and tail_visible(card.text)


def test_new_recording_interrupts_dismissal_and_follows_work_area_changes() -> None:
    with notifications(animated=True) as panel:
        panel.agent.recording(RecordingState(phase="saved", identity="first", preview="Saved transcript."))
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[Kind.RECORDING]
        panel.advance(31)
        assert all(item.retiring for item in panel.view.cards.values())
        panel.now += .08
        assert card.motion is not None and 0 < card.motion.sample(panel.now).alpha < 1
        panel.agent.recording(RecordingState(phase="recording", identity="second", preview="New transcript."))
        panel.pump()
        assert panel.view.cards[Kind.RECORDING] is card and not card.retiring
        assert not bool(card.motion) and card.alpha == 1
        panel.advance(.3)
        assert panel.view.work is not None
        work = panel.view.work
        before = windows.outer_rect(card.handle)
        with patch.object(AgentToasts, "area", return_value=Rect(work.left + 30, work.top, work.right - 30, work.bottom)):
            panel.view.refresh()
            panel.pump()
        after = windows.outer_rect(card.handle)
        assert after.right == before.right - 30 and after.top == before.top
