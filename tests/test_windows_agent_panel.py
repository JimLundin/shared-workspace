"""Exercise native Qt history, input, placement and painted approval previews."""

import sys
import threading
import unittest
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows native agent panel")

from assistant.agent_panel import AgentPanel
from assistant.agent_session import Answer, ReasoningSummary, StreamState, Succeeded, ToolActivity
from assistant.agent_window import AgentWindow
from assistant.panel import PanelPreferences, load_preferences, save_preferences
from assistant.qt import Qt, QtCore, QtGui, QtWidgets
from assistant.qt_ui import application
from assistant.recording_state import RecordingAction, RecordingState
from winapi import screen, windows
from winapi.shapes import Rect

from .test_agent_session import asking


class Panel:
    def __init__(self) -> None:
        self.root = application()
        self.jobs: deque[Callable[[], None]] = deque()
        self.submitted: list[str] = []
        self.recorded: list[RecordingAction] = []
        self.requested = threading.Event()
        self.agent = AgentWindow(
            self.stop, post=self.jobs.append, create_view=self.build, on_submit=self.submitted.append,
            on_reset=self.reset, on_waiting=lambda waiting: self.requested.set() if waiting else None,
            on_recording=self.recorded.append,
        )
        self.pump()
        assert self.agent.ready.is_set(), self.agent.all_text()

    def build(self, agent: AgentWindow) -> AgentPanel:
        self.view = AgentPanel(self.root, agent, preferences=PanelPreferences(Rect(30, 30, 530, 790)), remember=False)
        return self.view

    def reset(self) -> bool:
        self.agent.reset()
        return True

    def stop(self) -> None:
        self.agent.stopping()

    def pump(self) -> None:
        while self.jobs:
            self.jobs.popleft()()
        for _ in range(3):
            self.root.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def close(self) -> None:
        self.agent.close()
        self.pump()


@contextmanager
def native_panel() -> Generator[Panel]:
    panel = Panel()
    try:
        yield panel
    finally:
        panel.close()


def selected(widget: QtWidgets.QTextEdit, phrase: str, start: int = 0) -> QtGui.QTextCursor:
    cursor = widget.document().find(phrase, start)
    assert not cursor.isNull(), (phrase, widget.toPlainText())
    widget.setTextCursor(cursor)
    return cursor


def text_format(view: AgentPanel, phrase: str) -> QtGui.QTextCharFormat:
    cursor = view.text.document().find(phrase)
    assert not cursor.isNull(), phrase
    cursor.setPosition(cursor.selectionStart() + 1)
    return cursor.charFormat()


def links(view: AgentPanel) -> set[str]:
    found: set[str] = set()
    block = view.text.document().begin()
    while block.isValid():
        fragment = block.begin()
        while not fragment.atEnd():
            style = fragment.fragment().charFormat()
            if style.isAnchor():
                found.add(style.anchorHref())
            fragment += 1
        block = block.next()
    return found


def test_meeting_controls_share_the_timeline_without_a_composer_or_new_window() -> None:
    with native_panel() as panel:
        view = panel.view
        view.recording_menu.trigger()
        assert panel.recorded == ["start"]
        panel.agent.recording(RecordingState(phase="recording", identity="meeting", seconds=3723,
                                             preview="**00:00:00**  The first sentence."))
        panel.pump()
        assert not view.recording_bar.isHidden()
        assert "01:02:03" in view.recording_label.text()
        assert view.reply.isHidden() and view.stop.isHidden()
        assert "The first sentence." in view.text.toPlainText()
        assert not windows.is_visible(view.handle)
        view.recording_button.click()
        assert panel.recorded == ["start", "stop"]
        assert not panel.agent.state.stopping
        panel.agent.reset()
        panel.pump()
        assert not view.recording_bar.isHidden() and panel.agent.state.recording.capturing
        panel.agent.recording(RecordingState(phase="saved", identity="meeting", seconds=3723, preview="The last sentence."))
        panel.pump()
        view.recording_button.click()
        assert panel.recorded[-1] == "open"
        assert "The last sentence." in view.text.toPlainText()


def test_native_timeline_has_no_standing_composer_and_accepts_an_explicit_message() -> None:
    with native_panel() as panel:
        view = panel.view
        assert view.text.toPlainText() == ""
        assert all(widget.isHidden() for widget in (view.recording_bar, view.stop, view.details, view.reply))
        assert view.window.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert view.window.windowOpacity() == 1
        assert not windows.is_visible(view.handle)
        panel.agent.begin("Find the workbook", panel.stop)
        panel.agent.say('Found **budget.xlsx**.\n\n```python\nprint("**literal**")\n```')
        panel.agent.step("Reading the workbook")
        panel.agent.queued("Open it next")
        panel.pump()
        assert 'print("**literal**")' in view.text.toPlainText()
        assert text_format(view, "budget.xlsx").fontWeight() >= QtGui.QFont.Weight.DemiBold
        assert not view.stop.isHidden() and view.queued == ("Open it next",)
        assert view.details.isHidden() and view.reply.isHidden() and not view.new_action.isEnabled()
        view.compose_action.trigger()
        assert not view.reply.isHidden()
        text = "Continue with that workbook\nand preserve the complete ending. Åäö."
        view.input.insertPlainText(text)
        view.send.click()
        assert panel.submitted == [text]
        assert view.input.toPlainText() == "" and view.reply.isHidden()
        panel.agent.finish()
        panel.pump()
        assert view.stop.isHidden() and view.new_action.isEnabled()


def test_live_rows_render_in_place_with_markdown_and_no_additional_controls() -> None:
    with native_panel() as panel, patch("assistant.agent_panel.windows.focus") as focus:
        panel.agent.begin("Find my workbook", panel.stop)
        publish = panel.agent.updates()
        publish(ReasoningSummary("summary", "Checking **the files**.", 1))
        publish(Answer("answer", "Found **budget", 2))
        publish(ToolActivity("tool", "read_file", "budget.xlsx", 3))
        panel.pump()
        view = panel.view
        assert len(view.items) == 4
        assert "Read file · running" in view.text.toPlainText()
        assert text_format(view, "the files").fontWeight() >= QtGui.QFont.Weight.DemiBold
        for number in range(100):
            publish(ReasoningSummary("summary", f"Checking **the files**. Found {number}.", number + 4))
        assert len(panel.jobs) == 1
        publish(ReasoningSummary("summary", "Checked **the files**.", 104, StreamState.COMPLETE))
        publish(Answer("answer", 'Found **budget.xlsx**.\n\n```text\n**literal**\n```', 105, StreamState.COMPLETE))
        publish(ToolActivity("tool", "read_file", "budget.xlsx", 106, Succeeded("Read it.")))
        panel.agent.done('Found **budget.xlsx**.\n\n```text\n**literal**\n```')
        panel.agent.finish()
        panel.pump()
        text = view.text.toPlainText()
        assert text.count("Reasoning summary") == 1 and text.count("Found budget.xlsx.") == 1
        assert "Read file · done" in text and "Read it." in text and "**literal**" in text
        assert "running" not in text and "Checking" not in text
        assert len(view.items) == 4 and not view.controls
        assert all(widget.isHidden() for widget in (view.reply, view.details, view.stop))
        assert not windows.is_visible(view.handle)
        focus.assert_not_called()


def test_selected_text_in_a_streaming_reply_survives_utf16_updates() -> None:
    with native_panel() as panel:
        panel.agent.begin("Read the workbook", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("answer", "📄 The workbook is **ready**.", 1))
        panel.pump()
        view = panel.view
        selected(view.text, "workbook", view.starts[1])
        publish(Answer("answer", "📄 The workbook is **ready**.\n\n[Open the docs](https://example.com/docs)", 2))
        panel.pump()
        assert view.text.textCursor().selectedText() == "workbook"
        assert links(view) == {"https://example.com/docs"}
        publish(Answer("answer", "📄 The workbook is **ready**.\n\n[More docs](https://example.com/more)", 3, StreamState.COMPLETE))
        panel.pump()
        assert view.text.textCursor().selectedText() == "workbook"
        assert links(view) == {"https://example.com/more"}


def test_native_markdown_combines_emphasis_and_keeps_code_tables_and_user_text_selectable() -> None:
    with native_panel() as panel:
        view = panel.view
        panel.agent.begin('Keep **my input** literal.', panel.stop)
        panel.agent.say(
            '# A heading with ***both styles***\n\n'
            'A **bold and _italic_** reply with ~~old text~~.\n\n'
            '- [x] Complete\n- [ ] Next\n\n'
            '| File | State |\n| --- | --- |\n| `a.py` | **Ready** |\n\n'
            '```python\n  print("**literal**")\n```'
        )
        panel.pump()
        text = view.text.toPlainText()
        assert 'Keep **my input** literal.' in text
        assert 'A heading with both styles' in text and '# A heading' not in text
        assert '☑ Complete\n☐ Next' in text and 'File  a.py\nState  Ready' in text
        assert text_format(view, "old text").fontStrikeOut()
        for phrase, size in (("both styles", 15), ("italic", 10.5)):
            style = text_format(view, phrase)
            assert style.fontWeight() >= QtGui.QFont.Weight.DemiBold and style.fontItalic()
            assert style.fontPointSize() == size
        assert selected(view.text, '  print("**literal**")').selectedText() == '  print("**literal**")'
        assert view.reply.isHidden()


def test_native_markdown_wraps_list_text_and_opens_only_the_displayed_safe_link() -> None:
    with native_panel() as panel, patch("assistant.agent_panel.webbrowser.open") as browser:
        panel.agent.show()
        panel.agent.begin("Show formatted text", panel.stop)
        panel.agent.say(
            '- ' + 'A long list item that wraps across several lines while keeping its text aligned with the first word. ' * 4
            + '\n\n[**Docs** and `API`](https://example.com/a_(b))\n\n[Bad](javascript:alert(1))'
        )
        panel.pump()
        view = panel.view
        cursor = view.text.document().find("A long list")
        layout = cursor.block().layout()
        assert layout.lineCount() > 1
        assert cursor.blockFormat().leftMargin() > 0 and cursor.blockFormat().textIndent() < 0
        assert links(view) == {'https://example.com/a_(b)'}
        view.text.anchorClicked.emit(QtCore.QUrl(text_format(view, "Docs").anchorHref()))
        browser.assert_called_once_with('https://example.com/a_(b)')


def test_native_inline_questions_approvals_and_stop_share_one_composer() -> None:
    with native_panel() as panel:
        view = panel.view
        composer = view.input
        panel.agent.begin("Open the workbook", panel.stop)
        answer = "Use the revenue sheet\nwith all of the final line. Åäö."
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.agent.say("This message follows the pending question.")
            panel.pump()
            assert not view.reply.isHidden()
            view.input.insertPlainText(answer)
            view.submit()
            assert pending.result() == answer
        panel.pump()
        assert view.reply.isHidden() and answer in view.text.toPlainText()
        assert "This message follows the pending question." in view.text.toPlainText()
        assert not panel.submitted
        panel.requested.clear()
        with asking(panel.agent.inputs) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            index, frame = next(iter(view.controls.items()))
            approve = frame.findChild(QtWidgets.QToolButton, "approve")
            assert approve is not None and not approve.isEnabled()
            panel.agent.show()
            view.reveal_message(index)
            panel.pump()
            assert approve.isEnabled()
            approve.click()
            assert pending.result() == "approve"
        panel.pump()
        assert not view.controls and "Approved" in view.text.toPlainText()
        panel.requested.clear()
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            view.stop.click()
            assert pending.result() is None
        panel.agent.done("Stopped.")
        panel.agent.finish()
        panel.pump()
        assert view.input is composer
        view.compose()
        view.input.insertPlainText("Continue with the same workbook")
        view.submit()
        assert panel.submitted == ["Continue with the same workbook"]
        assert "Cancelled" in view.text.toPlainText()


def test_new_chat_clears_the_transcript_and_supports_another_inline_response() -> None:
    with native_panel() as panel:
        panel.agent.begin("Find documentation", panel.stop)
        panel.agent.say("[Docs](https://example.com/help)")
        panel.agent.step("Reading docs")
        panel.agent.finish()
        panel.pump()
        assert links(panel.view)
        panel.view.new_action.trigger()
        panel.pump()
        assert panel.view.text.toPlainText() == "" and panel.view.activity_text.toPlainText() == ""
        assert not links(panel.view)
        panel.agent.begin("Use the new conversation", panel.stop)
        panel.requested.clear()
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            panel.view.input.insertPlainText("A fresh answer")
            panel.view.submit()
            assert pending.result() == "A fresh answer"
        panel.pump()
        assert "A fresh answer" in panel.view.text.toPlainText() and "Docs" not in panel.view.text.toPlainText()


def test_background_updates_preserve_scroll_selection_visibility_and_draft() -> None:
    with native_panel() as panel, patch("assistant.agent_panel.windows.focus") as focus:
        panel.agent.show()
        panel.agent.begin("Read the reports", panel.stop)
        for number in range(40):
            panel.agent.say(f"Report {number}: " + "A paragraph with enough text to wrap. " * 5)
        panel.pump()
        view = panel.view
        view.compose()
        selected(view.text, "Report 0")
        view.scroll.setValue(0)
        view.input.insertPlainText("A draft I am still writing")
        panel.pump()
        top = view.scroll.value()
        panel.agent.say("A new reply while earlier messages are being read.")
        publish = panel.agent.updates()
        publish(Answer("live", "A live reply", 1))
        panel.pump()
        publish(Answer("live", "A live reply that keeps growing", 2))
        panel.pump()
        assert view.scroll.value() == top and view.text.textCursor().selectedText() == "Report 0"
        assert view.input.toPlainText() == "A draft I am still writing"
        assert not view.latest.isHidden()
        view.latest.click()
        panel.pump()
        assert view.scroll.value() == view.scroll.maximum()
        view.hide()
        panel.agent.say("Another reply while the panel is hidden.")
        panel.agent.queued("Do the next thing")
        panel.pump()
        assert not windows.is_visible(view.handle)
        focus.assert_not_called()


def test_native_pin_preference_is_saved_and_multiline_composer_stays_bounded(tmp_path: Path) -> None:
    with native_panel() as panel:
        view = panel.view
        preference = tmp_path / "agent-panel.json"
        view.remember_enabled = True
        def save(value: PanelPreferences) -> None:
            save_preferences(value, preference)
        with patch("assistant.agent_panel.save_preferences", side_effect=save):
            view.pinned.setChecked(False)
            view.pin()
            assert not load_preferences(preference).pinned
            view.translucent.setChecked(False)
            view.set_transparency()
            assert not load_preferences(preference).translucent
            assert not windows.is_visible(view.handle)
            panel.agent.show()
            panel.pump()
            view.compose()
            view.input.insertPlainText("Long lines of text " * 200 + "\n" * 20)
            panel.pump()
            assert view.input.height() <= 140 and view.input.height() < view.window.height() / 2
            view.remember_enabled = False


def test_dragging_between_edges_and_pulling_free_keeps_native_bounds_and_width() -> None:
    with native_panel() as panel:
        panel.agent.show()
        panel.pump()
        view = panel.view
        work = screen.monitor_of(view.handle).work
        bounds = windows.outer_rect(view.handle)
        assert str(view.preferences.edge) == "right" and bounds.right == work.right, (bounds, work)
        # Qt rounds integer logical positions at fractional display scales. A
        # taskbar's physical top offset can therefore differ by one pixel.
        assert abs(bounds.top - work.top) <= 1 and abs(bounds.bottom - work.bottom) <= 1, (bounds, work)
        view.start_drag((bounds.left + 24, bounds.top + 16))
        view.end_drag((work.left + 24, work.top + 16))
        panel.pump()
        left = windows.outer_rect(view.handle)
        assert str(view.preferences.edge) == "left" and left.left == work.left and abs(left.bottom - work.bottom) <= 1, (left, work)
        view.hide()
        panel.agent.show()
        panel.pump()
        assert windows.outer_rect(view.handle) == left
        view.start_drag((left.left + 24, left.top + 16))
        view.end_drag((work.left + (work.width - left.width) // 2 + 24, work.top + 80))
        panel.pump()
        free = windows.outer_rect(view.handle)
        assert str(view.preferences.edge) == "None" and free.width == left.width and free.height < work.height
        view.start_resize((free.right, free.top + 100))
        view.end_resize((free.right + 60, free.top + 100))
        panel.pump()
        wider = windows.outer_rect(view.handle)
        assert wider.width == free.width + 60
        view.start_drag((wider.left + 24, wider.top + 16))
        view.end_drag((work.right - wider.width + 24, work.top + 16))
        panel.pump()
        right = windows.outer_rect(view.handle)
        assert view.preferences.edge == "right" and right.right == work.right and right.width == wider.width
        view.start_resize((right.left, right.top + 100))
        view.end_resize((right.left - 40, right.top + 100))
        panel.pump()
        resized = windows.outer_rect(view.handle)
        assert resized.right == work.right and resized.width == right.width + 40, (resized, right, work)
        view.hide()
        panel.agent.show()
        panel.pump()
        assert windows.outer_rect(view.handle) == resized


def test_transparency_can_be_disabled_and_escape_cancels_a_drag() -> None:
    with native_panel() as panel:
        panel.agent.show()
        panel.pump()
        view = panel.view
        before = windows.outer_rect(view.handle)
        view.translucent.setChecked(False)
        view.set_transparency()
        assert view.window.windowOpacity() == 1 and not view.preferences.translucent
        view.start_drag((before.left + 24, before.top + 16))
        view.drag_to((before.left - 100, before.top + 60))
        assert windows.outer_rect(view.handle) != before
        view.escape()
        panel.pump()
        assert windows.outer_rect(view.handle) == before
        assert view.preferences.edge == "right" and windows.is_visible(view.handle)
        assert QtWidgets.QWidget.mouseGrabber() is None


def test_closing_the_panel_leaves_the_shared_qt_loop_running_without_delayed_errors() -> None:
    with native_panel() as panel:
        panel.agent.show()
        panel.pump()
        bounds = windows.outer_rect(panel.view.handle)
        panel.view.start_drag((bounds.left + 24, bounds.top + 16))
        panel.view.drag_to((bounds.left - 100, bounds.top + 60))
        panel.agent.close()
        panel.pump()
        QtCore.QTimer.singleShot(150, panel.root.quit)
        panel.root.exec()
        assert panel.agent.closed_ready.is_set()
        assert QtWidgets.QApplication.instance() is panel.root
