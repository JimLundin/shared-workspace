"""Exercise real Tk widgets without a model, browser, or foreground input."""
# pyright: reportPrivateUsage=false

import sys
import threading
import unittest
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Protocol, cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows native agent panel")

import tkinter as tk

from assistant.agent_panel import TRANSPARENT, AgentPanel
from assistant.agent_session import Answer, ReasoningSummary, StreamState, Succeeded, ToolActivity
from assistant.agent_window import AgentWindow
from assistant.panel import PanelPreferences, load_preferences, save_preferences
from assistant.recording_state import RecordingAction, RecordingState
from winapi import screen, windows
from winapi.shapes import Rect

from .test_agent_session import asking


class TextScrolling(Protocol):
    """The no-argument Text.yview return type missing from Tk's overloads."""

    def yview(self) -> tuple[float, float]: ...


class TkCommands(Protocol):
    def call(self, *arguments: str) -> object: ...


class WindowOptions(Protocol):
    def attributes(self, option: Literal["-alpha"], /) -> float: ...
    def grab_current(self) -> object: ...


class Panel:
    __slots__ = ("agent", "jobs", "recorded", "requested", "root", "submitted", "view")

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
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
        self.root.update_idletasks()
        self.root.update()

    def close(self) -> None:
        self.agent.close()
        self.pump()
        self.root.destroy()


@contextmanager
def native_panel() -> Generator[Panel]:
    panel = Panel()
    try:
        yield panel
    finally:
        panel.close()


def test_meeting_controls_share_the_timeline_without_a_composer_or_new_window() -> None:
    with native_panel() as panel:
        view = panel.view
        assert view.recording_menu is not None
        view.menu.invoke(view.recording_menu)
        assert panel.recorded == ["start"]
        panel.agent.recording(RecordingState(phase="recording", identity="meeting", seconds=3723,
                                             preview="**00:00:00**  The first sentence."))
        panel.pump()
        assert view.recording_bar.winfo_manager() == "grid"
        assert "01:02:03" in cast("str", view.recording_label.cget("text"))
        assert view.reply.winfo_manager() == "" and view.stop.winfo_manager() == ""
        assert "The first sentence." in view.text.get("1.0", "end-1c")
        assert not windows.is_visible(view.handle)  # painting progress must not reveal the panel
        view.recording_button.invoke()
        assert panel.recorded == ["start", "stop"]
        assert not panel.agent.state.stopping
        panel.agent.reset()
        panel.pump()
        assert view.recording_bar.winfo_manager() == "grid"
        assert panel.agent.state.recording.capturing
        panel.agent.recording(RecordingState(phase="saved", identity="meeting", seconds=3723, preview="The last sentence."))
        panel.pump()
        view.recording_button.invoke()
        assert panel.recorded[-1] == "open"
        assert "The last sentence." in view.text.get("1.0", "end-1c")


def pointer(x: int, y: int) -> tk.Event[tk.Misc]:
    return cast("tk.Event[tk.Misc]", SimpleNamespace(x_root=x, y_root=y))


def pointer_event(widget: tk.Misc, name: str, event: tk.Event[tk.Misc]) -> None:
    cast("TkCommands", widget.tk).call("event", "generate", str(widget), name, "-rootx", str(event.x_root), "-rooty", str(event.y_root))


def test_native_timeline_has_no_standing_composer_and_accepts_an_explicit_message() -> None:
    with native_panel() as panel:
        view = panel.view
        assert view.text.get("1.0", "end-1c") == ""
        assert view.recording_bar.winfo_manager() == ""
        assert view.stop.winfo_manager() == ""
        assert view.details.winfo_manager() == ""
        assert view.reply.winfo_manager() == ""
        assert cast("WindowOptions", view.window).attributes("-alpha") == 1
        assert str(cast("TkCommands", view.window.tk).call("wm", "attributes", str(view.window), "-transparentcolor")) == TRANSPARENT
        assert view.window.overrideredirect()
        assert not windows.is_visible(view.handle)
        panel.agent.begin("Find the workbook", panel.stop)
        panel.agent.say('Found **budget.xlsx**.\n\n```python\nprint("**literal**")\n```')
        panel.agent.step("Reading the workbook")
        panel.agent.queued("Open it next")
        panel.pump()
        assert 'print("**literal**")' in view.text.get("1.0", "end-1c")
        assert view.text.tag_ranges("bold")
        assert "●  Agent" in view.text.get("1.0", "end-1c") and view.text.tag_ranges("stamp")
        assert view.stop.winfo_manager() == "grid"
        assert view.queued == ("Open it next",)
        assert view.details.winfo_manager() == ""
        assert cast(str, view.menu.entrycget(0, "state")) == "disabled"
        assert view.reply.winfo_manager() == ""
        view.menu.invoke(1)
        assert view.reply.winfo_manager() == "grid"
        text = "Continue with that workbook\nand preserve the complete ending. Åäö."
        view.input.insert("1.0", text)
        panel.pump()
        view.send.invoke()
        assert panel.submitted == [text]
        assert view.input.get("1.0", "end-1c") == ""
        assert view.reply.winfo_manager() == ""
        panel.agent.finish()
        panel.pump()
        assert view.stop.winfo_manager() == ""
        assert cast(str, view.menu.entrycget(0, "state")) == "normal"


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
        assert "Read file · running" in view.text.get("1.0", "end-1c")
        assert view.text.tag_ranges("muted") and view.text.tag_ranges("bold")
        for number in range(100):
            publish(ReasoningSummary("summary", f"Checking **the files**. Found {number}.", number + 4))
        assert len(panel.jobs) == 1
        publish(ReasoningSummary("summary", "Checked **the files**.", 104, StreamState.COMPLETE))
        publish(Answer("answer", 'Found **budget.xlsx**.\n\n```text\n**literal**\n```', 105, StreamState.COMPLETE))
        publish(ToolActivity("tool", "read_file", "budget.xlsx", 106, Succeeded("Read it.")))
        panel.agent.done('Found **budget.xlsx**.\n\n```text\n**literal**\n```')
        panel.agent.finish()
        panel.pump()
        text = view.text.get("1.0", "end-1c")
        assert text.count("Reasoning summary") == 1 and text.count("Found budget.xlsx.") == 1
        assert "Read file · done" in text and "Read it." in text and "**literal**" in text
        assert "running" not in text and "Checking" not in text
        assert len(view.items) == 4 and not view.controls
        assert view.reply.winfo_manager() == "" and view.details.winfo_manager() == "" and view.stop.winfo_manager() == ""
        assert not windows.is_visible(view.handle)
        focus.assert_not_called()


def test_text_selected_in_a_streaming_reply_stays_selected_as_it_grows() -> None:
    with native_panel() as panel:
        panel.agent.begin("Read the workbook", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("answer", "The workbook is **ready**.", 1))
        panel.pump()
        view = panel.view
        start = view.text.search("workbook", "message-1", stopindex="message-1-end")
        assert start
        view.text.tag_add("sel", start, f"{start}+8c")
        publish(Answer("answer", "The workbook is **ready**.\n\n[Open the docs](https://example.com/docs)", 2))
        panel.pump()
        assert view.text.get("sel.first", "sel.last") == "workbook"
        assert len(view.links) == 1
        publish(Answer("answer", "The workbook is **ready**.\n\n[More docs](https://example.com/more)", 3, StreamState.COMPLETE))
        panel.pump()
        assert view.text.get("sel.first", "sel.last") == "workbook"
        assert list(view.links.values()) == ["https://example.com/more"]


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
        text = view.text.get("1.0", "end-1c")
        assert 'Keep **my input** literal.' in text
        assert 'A heading with both styles' in text and '# A heading' not in text
        assert '☑ Complete\n☐ Next' in text
        assert 'File  a.py\nState  Ready' in text
        assert view.text.tag_ranges("strike") and view.text.tag_ranges("table_label")
        interpreter = cast("TkCommands", panel.root.tk)
        for phrase, size in (("both styles", "15"), ("italic", "11")):
            index = view.text.search(phrase, "message-1")
            tags = view.text.tag_names(index)
            assert "bold" in tags and "italic" in tags
            fonts = [tag for tag in tags if tag.startswith("font-")]
            assert len(fonts) == 1
            descriptor = cast(str, interpreter.call(str(view.text), "tag", "cget", fonts[0], "-font"))
            assert interpreter.call("font", "actual", descriptor, "-weight") == "bold"
            assert interpreter.call("font", "actual", descriptor, "-slant") == "italic"
            assert str(interpreter.call("font", "actual", descriptor, "-size")) == size
        view.text.tag_add("sel", "codeblock.first", "codeblock.last")
        assert view.text.get("sel.first", "sel.last") == '  print("**literal**")\n'
        assert view.reply.winfo_manager() == ""


def test_native_markdown_wraps_list_text_and_opens_the_displayed_link() -> None:
    with native_panel() as panel, patch("assistant.agent_panel.webbrowser.open") as browser, patch("assistant.agent_panel.windows.focus"):
        panel.agent.show()
        panel.agent.begin("Show formatted text", panel.stop)
        panel.agent.say(
            '- ' + 'A long list item that wraps across several lines while keeping its text aligned with the first word. ' * 4
            + '\n\n[**Docs** and `API`](https://example.com/a_(b))\n\n[Bad](javascript:alert(1))'
        )
        panel.pump()
        view = panel.view
        first = view.text.search("A long list", "message-1")
        wrapped = view.text.index(f"{first} + 1 display lines display linestart")
        view.text.see(first)
        panel.pump()
        first_box, wrapped_box = view.text.bbox(first), view.text.bbox(wrapped)
        assert first_box is not None and wrapped_box is not None
        assert first_box[0] == wrapped_box[0]
        link = view.text.search("Docs", "message-1")
        view.text.see(link)
        panel.pump()
        bounds = view.text.bbox(link)
        assert bounds is not None
        view.open_link(cast("tk.Event[tk.Misc]", SimpleNamespace(x=bounds[0] + 1, y=bounds[1] + 1)))
        browser.assert_called_once_with('https://example.com/a_(b)')
        assert set(view.links.values()) == {'https://example.com/a_(b)'}


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
            assert view.reply.winfo_manager() == "grid"
            view.input.insert("1.0", answer)
            view.submit()
            assert pending.result() == answer
        panel.pump()
        assert view.reply.winfo_manager() == ""
        assert answer in view.text.get("1.0", "end-1c")
        assert "This message follows the pending question." in view.text.get("1.0", "end-1c")
        assert not panel.submitted
        panel.requested.clear()
        with asking(panel.agent.inputs) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            frame = next(iter(view.controls.values()))
            approve = cast("tk.Button", next(child for child in frame.winfo_children() if child.winfo_class() == "Button"))
            approve.invoke()
            assert pending.result() == "approve"
        panel.pump()
        assert not view.controls and "Approved" in view.text.get("1.0", "end-1c")
        panel.requested.clear()
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            view.stop.invoke()
            assert pending.result() is None
        panel.agent.done("Stopped.")
        panel.agent.finish()
        panel.pump()
        assert view.input is composer
        view.compose()
        view.input.insert("1.0", "Continue with the same workbook")
        view.submit()
        assert panel.submitted == ["Continue with the same workbook"]
        assert "Cancelled" in view.text.get("1.0", "end-1c")


def test_new_chat_clears_the_transcript_and_supports_another_inline_response() -> None:
    with native_panel() as panel:
        panel.agent.begin("Find documentation", panel.stop)
        panel.agent.say("[Docs](https://example.com/help)")
        panel.agent.step("Reading docs")
        panel.agent.finish()
        panel.pump()
        assert panel.view.links
        panel.view.menu.invoke(0)
        panel.pump()
        assert panel.view.text.get("1.0", "end-1c") == ""
        assert panel.view.activity_text.get("1.0", "end-1c") == ""
        assert not panel.view.links
        panel.agent.begin("Use the new conversation", panel.stop)
        panel.requested.clear()
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            panel.view.input.insert("1.0", "A fresh answer")
            panel.view.submit()
            assert pending.result() == "A fresh answer"
        panel.pump()
        assert "A fresh answer" in panel.view.text.get("1.0", "end-1c")
        assert "Docs" not in panel.view.text.get("1.0", "end-1c")


def test_background_updates_preserve_scroll_selection_visibility_and_draft() -> None:
    with native_panel() as panel, patch("assistant.agent_panel.windows.focus") as focus:
        panel.agent.show()
        panel.agent.begin("Read the reports", panel.stop)
        for number in range(40):
            panel.agent.say(f"Report {number}: " + "A paragraph with enough text to wrap. " * 5)
        panel.pump()
        view = panel.view
        view.compose()
        view.text.yview_moveto(0)
        start = view.text.index("message-1")
        view.text.tag_add("sel", start, f"{start}+6c")
        selected = view.text.get("sel.first", "sel.last")
        view.input.insert("1.0", "A draft I am still writing")
        panel.pump()
        top = view.text.index("@0,0")
        panel.agent.say("A new reply while earlier messages are being read.")
        publish = panel.agent.updates()
        publish(Answer("live", "A live reply", 1))
        panel.pump()
        publish(Answer("live", "A live reply that keeps growing", 2))
        panel.pump()
        assert view.text.index("@0,0") == top
        assert view.text.get("sel.first", "sel.last") == selected
        assert view.input.get("1.0", "end-1c") == "A draft I am still writing"
        assert view.latest.winfo_manager() == "place"
        view.latest.invoke()
        panel.pump()
        assert cast("TextScrolling", view.text).yview()[1] >= .995
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
            view.pinned.set(False)
            view.pin()
            assert not load_preferences(preference).pinned
            view.translucent.set(False)
            view.set_transparency()
            assert not load_preferences(preference).translucent
            assert not windows.is_visible(view.handle)
            panel.agent.show()
            panel.pump()
            view.compose()
            view.input.insert("1.0", "Long lines of text " * 200 + "\n" * 20)
            panel.pump()
            assert int(cast(int | str, view.input.cget("height"))) <= 6
            assert view.input.winfo_height() < view.window.winfo_height() / 2
            view.remember_enabled = False


def test_dragging_between_edges_and_pulling_free_keeps_native_bounds_and_width() -> None:
    with native_panel() as panel:
        panel.agent.show()
        panel.pump()
        view = panel.view
        work = screen.monitor_of(view.handle).work
        bounds = windows.outer_rect(view.handle)
        assert str(view.preferences.edge) == "right"
        assert (bounds.right, bounds.top, bounds.bottom) == (work.right, work.top, work.bottom)
        pointer_event(view.drag_handle, "<ButtonPress-1>", pointer(bounds.left + 24, bounds.top + 16))
        released = pointer(work.left + 24, work.top + 16)
        pointer_event(view.window, "<B1-Motion>", released)
        assert windows.outer_rect(view.handle).left == work.left
        pointer_event(view.window, "<ButtonRelease-1>", released)
        panel.pump()
        left = windows.outer_rect(view.handle)
        assert str(view.preferences.edge) == "left" and left.left == work.left and left.bottom == work.bottom
        view.hide()
        panel.agent.show()
        panel.pump()
        assert windows.outer_rect(view.handle) == left
        view.start_drag(pointer(left.left + 24, left.top + 16))
        released = pointer(work.left + (work.width - left.width) // 2 + 24, work.top + 80)
        view.end_drag(released)
        panel.pump()
        free = windows.outer_rect(view.handle)
        assert view.preferences.edge is None
        assert free.width == left.width and free.height < work.height
        view.start_resize(pointer(free.right, free.top + 100))
        view.end_resize(pointer(free.right + 60, free.top + 100))
        panel.pump()
        wider = windows.outer_rect(view.handle)
        assert wider.width == free.width + 60
        view.start_drag(pointer(wider.left + 24, wider.top + 16))
        view.end_drag(pointer(work.right - wider.width + 24, work.top + 16))
        panel.pump()
        right = windows.outer_rect(view.handle)
        assert str(view.preferences.edge) == "right" and right.right == work.right
        assert right.width == wider.width
        pointer_event(view.resize_handle, "<ButtonPress-1>", pointer(right.left, right.top + 100))
        pointer_event(view.window, "<B1-Motion>", pointer(right.left - 40, right.top + 100))
        pointer_event(view.window, "<ButtonRelease-1>", pointer(right.left - 40, right.top + 100))
        panel.pump()
        resized = windows.outer_rect(view.handle)
        assert resized.right == work.right and resized.width == right.width + 40
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
        view.translucent.set(False)
        view.set_transparency()
        assert cast("WindowOptions", view.window).attributes("-alpha") == 1
        assert cast("TkCommands", view.window.tk).call("wm", "attributes", str(view.window), "-transparentcolor") == ""
        assert not view.preferences.translucent
        view.start_drag(pointer(before.left + 24, before.top + 16))
        moved = pointer(before.left - 100, before.top + 60)
        view.drag_to(moved)
        assert windows.outer_rect(view.handle) != before
        view.escape(moved)
        panel.pump()
        assert windows.outer_rect(view.handle) == before
        assert view.preferences.edge == "right" and windows.is_visible(view.handle)
        assert cast("WindowOptions", view.window).grab_current() is None


def test_closing_the_panel_leaves_the_shared_tk_loop_running_without_delayed_errors() -> None:
    with native_panel() as panel:
        interpreter = cast("TkCommands", panel.root.tk)
        interpreter.call("set", "::panel_errors", "")
        interpreter.call("proc", "bgerror", "message", "lappend ::panel_errors $message")
        panel.agent.show()
        panel.pump()
        panel.view.hide()
        panel.agent.show()
        panel.pump()
        bounds = windows.outer_rect(panel.view.handle)
        panel.view.start_drag(pointer(bounds.left + 24, bounds.top + 16))
        panel.view.drag_to(pointer(bounds.left - 100, bounds.top + 60))
        panel.agent.close()
        panel.pump()
        panel.root.after(450, panel.root.quit)
        panel.root.mainloop()
        assert panel.agent.closed_ready.is_set()
        assert interpreter.call("set", "::panel_errors") == ""
