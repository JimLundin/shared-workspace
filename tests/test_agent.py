"""The agent: its loop, what it may do without asking, and its tools. Against a
fake endpoint playing the model, and a fake machine."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast

from assistant.agent import Agent, allowed, key_strokes
from assistant.workspace import Workspace
from assistant.geometry import Shot
from assistant.llm import Json, Settings
from winapi.shapes import Rect, Window

from .fake_endpoint import FakeEndpoint, Reply, fake_endpoint
from .test_workspace import live_tree


def dumps(value: Json) -> str:
    return json.dumps(value)


def calls(*tools: tuple[str, dict[str, Json]]) -> Reply:
    """The model asking for tools."""
    tool_calls: list[Json] = [
        {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": dumps(args)}} for i, (name, args) in enumerate(tools)
    ]
    return Reply(dumps({"choices": [{"message": {"role": "assistant", "content": "On it.", "tool_calls": tool_calls}}]}).encode())


def says(text: str) -> Reply:
    return Reply(dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode())


class FakeMachine:
    __slots__ = ("answers", "approve", "commands", "confirms", "log", "reviewed")

    def __init__(self) -> None:
        self.log: list[str] = []
        self.approve = True
        self.reviewed: list[str] = []
        self.confirms: list[bool] = []
        self.answers: list[str | None] = []
        self.commands: dict[str, tuple[int, str]] = {}

    def run_powershell(self, command: str, timeout_s: float) -> tuple[int, str]:
        self.log.append(f"ran {command}")
        return self.commands.get(command, (0, "ok"))

    def screenshot(self) -> tuple[bytes, Shot]:
        self.log.append("screenshot")
        return b"\x89PNG", Shot(Rect(0, 0, 2560, 1440), 1280, 720)

    def click(self, x: int, y: int, button: str, count: int) -> None:
        self.log.append(f"click {button} x{count} at {x},{y}")

    def type_text(self, text: str) -> None:
        self.log.append(f"type {text}")

    def press_keys(self, strokes: list[tuple[int, bool]]) -> None:
        self.log.append(f"keys {strokes}")

    def scroll(self, x: int, y: int, clicks: int) -> None:
        self.log.append(f"scroll {clicks} at {x},{y}")

    def windows(self) -> list[Window]:
        return [Window(7, "Inbox - Outlook", "olk.exe", minimized=False)]

    def focus(self, handle: int) -> bool:
        self.log.append(f"focus {handle}")
        return True

    def get_clipboard(self) -> str | None:
        return "clipped"

    def set_clipboard(self, text: str) -> None:
        self.log.append(f"clipboard {text}")

    def open(self, target: str) -> None:
        self.log.append(f"open {target}")

    def ask_user(self, question: str) -> str | None:
        self.log.append(f"asked {question}")
        return self.answers.pop(0) if self.answers else None

    def confirm(self, title: str, detail: str) -> bool:
        self.log.append(f"confirm {title}: {detail}")
        return self.confirms.pop(0) if self.confirms else False

    def review(self, summary: str, diff: str) -> bool:
        self.log.append(f"review {summary}")
        self.reviewed.append(diff)
        return self.approve

    def install(self, summary: str) -> None:
        self.log.append(f"install {summary}")


class FakeWindow:
    __slots__ = ("log",)

    def __init__(self) -> None:
        self.log: list[str] = []

    def step(self, text: str) -> None:
        self.log.append(f"step {text}")

    def say(self, text: str) -> None:
        self.log.append(f"say {text}")

    def problem(self, text: str) -> None:
        self.log.append(f"problem {text}")

    def done(self, text: str) -> None:
        self.log.append(f"done {text}")


class Rig:
    __slots__ = ("endpoint", "journal", "machine", "stop", "window", "workspace")

    def __init__(self, endpoint: FakeEndpoint, *replies: Reply, workspace: Workspace | None = None) -> None:
        self.workspace = workspace
        endpoint.replies = list(replies)
        self.endpoint = endpoint
        self.machine = FakeMachine()
        self.window = FakeWindow()
        self.stop = threading.Event()
        self.journal: list[dict[str, Json]] = []

    def run(self, task: str = "do it", max_steps: int = 10, *, after_step: Callable[[], None] | None = None) -> None:
        settings = Settings(self.endpoint.url, "k", "transcribe", "chat", "agent", "", "")
        agent = Agent(settings, self.machine, self.window, self.stop, max_steps=max_steps, journal=self.journal.append, after_step=after_step, workspace=self.workspace)
        agent.run(task)

    def sent(self, index: int = -1) -> list[dict[str, object]]:
        data: object = json.loads(self.endpoint.received[index].body)
        assert isinstance(data, dict)
        return cast("list[dict[str, object]]", cast("dict[str, object]", data)["messages"])

    def results(self, index: int = -1) -> list[str]:
        return [str(m["content"]) for m in self.sent(index) if m["role"] == "tool"]


# ---- the loop --------------------------------------------------------------------------


def test_the_agent_uses_tools_then_answers(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("list_directory", {"path": str(tmp_path)})), says("There is one file: notes.txt."))
        rig.run("what's in my folder?")
        assert "notes.txt" in rig.results()[0]
        assert rig.window.log[-1] == "done There is one file: notes.txt."
        assert rig.sent(0)[1] == {"role": "user", "content": "what's in my folder?"}
        assert b'"model": "agent"' in endpoint.received[0].body


def test_every_call_gets_a_result_even_bad_ones() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("no_such_tool", {}), ("read_file", {})), says("Sorry."))
        rig.run()
        assert rig.results() == ["Error: there's no tool called no_such_tool.", "Error: read_file needs path (text)."]


def test_stop_ends_the_task_before_the_next_step() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("get_clipboard", {})), calls(("get_clipboard", {})), says("never"))
        rig.run(after_step=rig.stop.set)
        assert len(endpoint.received) == 1
        assert rig.window.log[-1] == "done Stopped."


def test_the_agent_stops_after_its_step_limit() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("get_clipboard", {})))
        rig.run(max_steps=3)
        assert len(endpoint.received) == 3
        assert rig.window.log[-1] == "done Stopped after 3 steps, not finished."


def test_model_failures_end_the_task() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, Reply(b'{"error": {"message": "model overloaded"}}', status=503))
        rig.run()
        assert rig.window.log[-1] == "problem HTTP 503: model overloaded"


def test_the_journal_records_each_action() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("set_clipboard", {"text": "hi"})), says("Done."))
        rig.run()
        assert [(e["tool"], e["ok"]) for e in rig.journal] == [("set_clipboard", True)]


# ---- what needs permission -------------------------------------------------------------


def test_commands_need_confirmation() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("run_powershell", {"command": "Remove-Item C:\\temp\\x"})), says("ok"))
        rig.machine.confirms = [False]
        rig.run()
        assert "ran Remove-Item C:\\temp\\x" not in rig.machine.log
        assert rig.results() == ["The user declined: don't run this. Ask them, or try something else."]


def test_confirmed_commands_run_and_report_their_output() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("run_powershell", {"command": "Stop-Process -Name notepad"})), says("ok"))
        rig.machine.confirms = [True]
        rig.machine.commands = {"Stop-Process -Name notepad": (1, "no such process")}
        rig.run()
        assert rig.results() == ["Exit code 1.\nno such process"]


def test_allowed_commands_run_without_asking() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("run_powershell", {"command": "git status"})), says("ok"))
        rig.run()
        assert rig.machine.log == ["ran git status"]


def test_allowed_means_nothing_can_be_chained_on() -> None:
    assert allowed("Get-ChildItem C:\\Users -Recurse")
    assert allowed("git log --oneline -5")
    for sneaky in ("git status; Remove-Item x", "Get-Content a | Set-Content b", "git log && del x", "Get-Date $(rm x)", "git log > out.txt"):
        assert not allowed(sneaky), sneaky
    assert not allowed("Get-ChildItemX")
    assert not allowed("Remove-Item x")


def test_mouse_and_keyboard_need_permission_once_per_task() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("screenshot", {})), calls(("click", {"x": 640, "y": 360}), ("type_text", {"text": "hi"})), says("ok"))
        rig.machine.confirms = [True]
        rig.run()
        assert [entry for entry in rig.machine.log if entry.startswith("confirm")] == [
            "confirm Let the agent use the mouse and keyboard?: Until this task ends. Stop it any time from its window."
        ]
        assert "click left x1 at 1280,720" in rig.machine.log  # the screenshot was half size
        assert "type hi" in rig.machine.log


def test_declining_the_mouse_and_keyboard_is_remembered_for_the_task() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("type_text", {"text": "a"})), calls(("type_text", {"text": "b"})), says("ok"))
        rig.machine.confirms = [False]
        rig.run()
        assert [entry for entry in rig.machine.log if entry.startswith(("confirm", "type"))] == [
            "confirm Let the agent use the mouse and keyboard?: Until this task ends. Stop it any time from its window."
        ]
        assert "declined" in rig.results()[0]


def test_clicks_before_any_screenshot_are_refused() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("click", {"x": 1, "y": 1})), says("ok"))
        rig.machine.confirms = [True]
        rig.run()
        assert rig.results() == ["Error: take a screenshot first; coordinates are in the latest screenshot."]


# ---- screenshots -----------------------------------------------------------------------


def test_screenshots_go_to_the_model_as_images_and_only_recent_ones_are_kept() -> None:
    with fake_endpoint() as endpoint:
        shots = [calls(("screenshot", {})) for _ in range(4)]
        rig = Rig(endpoint, *shots, says("ok"))
        rig.run()
        last = rig.sent()
        images = [m for m in last if m["role"] == "user" and "image_url" in json.dumps(m["content"])]
        assert len(images) == 2
        assert "[an earlier screenshot, removed to save space]" in json.dumps(last)
        assert "1280x720" in rig.results()[0]


# ---- files -----------------------------------------------------------------------------


def test_read_file_returns_text_and_says_when_it_was_cut(tmp_path: Path) -> None:
    big = tmp_path / "big.log"
    big.write_text("x" * 50_000, encoding="utf-8")
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("read_file", {"path": str(big)})), says("ok"))
        rig.run()
        result = rig.results()[0]
        assert result.startswith("x" * 100)
        assert result.endswith("[cut: showing 20000 of 50000 characters]")


def test_find_files_matches_names_under_a_folder(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "report.xlsx").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("find_files", {"folder": str(tmp_path), "pattern": "*.xlsx"})), says("ok"))
        rig.run()
        assert rig.results() == [str(tmp_path / "a" / "report.xlsx")]


def test_write_file_asks_first_and_then_writes(tmp_path: Path) -> None:
    target = tmp_path / "out" / "summary.md"
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("write_file", {"path": str(target), "content": "# Summary\n"})), says("ok"))
        rig.machine.confirms = [True]
        rig.run()
        assert target.read_text(encoding="utf-8") == "# Summary\n"
        assert rig.machine.log[0].startswith(f"confirm Write a file?: {target}")


def test_ask_user_passes_the_answer_back() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("ask_user", {"question": "Which folder?"})), says("ok"))
        rig.machine.answers = ["Downloads"]
        rig.run()
        assert rig.results() == ["Downloads"]


# ---- keys ------------------------------------------------------------------------------


def test_key_combinations() -> None:
    ctrl, shift, s = 0xA2, 0xA0, 0x53
    assert key_strokes("ctrl+shift+s") == [(ctrl, True), (shift, True), (s, True), (s, False), (shift, False), (ctrl, False)]
    assert key_strokes("Enter") == [(0x0D, True), (0x0D, False)]
    assert key_strokes("alt+f4") == [(0xA4, True), (0x73, True), (0x73, False), (0xA4, False)]
    assert key_strokes("ctrl+banana") is None


# ---- changing the toolkit itself -------------------------------------------------------


def source(tmp_path: Path) -> Workspace:
    return Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")


EDIT: Final = ("edit_source", {"path": "config.py", "old": "'hej'", "new": "'hello'"})


def test_the_agent_edits_checks_and_applies_a_change(tmp_path: Path) -> None:
    workspace = source(tmp_path)
    with fake_endpoint() as endpoint:
        apply = ("apply_changes", {"summary": "Say hello"})
        rig = Rig(endpoint, calls(EDIT), calls(("check_changes", {})), calls(apply), says("never asked"), workspace=workspace)
        rig.run("greet in English")
        assert rig.machine.log == ["review Say hello", "install Say hello"]
        assert "+GREETING = 'hello'" in rig.machine.reviewed[0]
        assert rig.results(1) == ["All checks pass."]
        assert rig.window.log[-1] == "done Installing: Say hello. The toolkit restarts in a moment."
        assert len(endpoint.received) == 3  # the task ends with the install


def test_apply_needs_a_passing_check_of_exactly_these_changes(tmp_path: Path) -> None:
    workspace = source(tmp_path)
    second = ("edit_source", {"path": "config.py", "old": "'hello'", "new": "'hi'"})
    apply = ("apply_changes", {"summary": "x"})
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(EDIT), calls(apply), calls(("check_changes", {})), calls(second), calls(apply), says("ok"), workspace=workspace)
        rig.run()
        assert rig.results(1) == ["Error: run check_changes first; only checked changes can be installed."]
        assert rig.results(4) == ["Error: the changes have changed since they were checked; run check_changes again."]
        assert rig.machine.log == []


def test_failing_checks_are_reported_and_block_apply(tmp_path: Path) -> None:
    workspace = source(tmp_path)
    breaking = ("write_source", {"path": "config.py", "content": "GREETING = ''\n"})
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(breaking), calls(("check_changes", {})), calls(("apply_changes", {"summary": "x"})), says("ok"), workspace=workspace)
        rig.run()
        assert "FAILED tests.test_config.test_greeting" in rig.results(1)[0]
        assert rig.results(2) == ["Error: the last check failed; fix the changes and check again."]


def test_a_declined_review_installs_nothing(tmp_path: Path) -> None:
    workspace = source(tmp_path)
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(EDIT), calls(("check_changes", {})), calls(("apply_changes", {"summary": "x"})), says("ok"), workspace=workspace)
        rig.machine.approve = False
        rig.run()
        assert rig.machine.log == ["review x"]
        assert rig.results(2) == ["The user didn't approve these changes. Ask what they'd like instead."]


def test_protected_files_cant_be_edited(tmp_path: Path) -> None:
    workspace = source(tmp_path)
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("write_source", {"path": "keeper.py", "content": "x"})), says("ok"), workspace=workspace)
        rig.run()
        assert rig.results(0)[0].startswith("Error: keeper.py is protected")


def test_each_task_starts_from_the_running_version(tmp_path: Path) -> None:
    workspace = source(tmp_path)
    workspace.reset()
    workspace.write("leftover.py", "x = 1\n")
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("show_changes", {})), says("ok"), workspace=workspace)
        rig.run()
        assert rig.results(0) == ["No changes yet."]


def test_reading_and_listing_the_source(tmp_path: Path) -> None:
    workspace = source(tmp_path)
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("list_source", {}), ("read_source", {"path": "config.py"})), says("ok"), workspace=workspace)
        rig.run()
        listing, text = rig.results(0)
        assert "config.py" in listing.splitlines()
        assert text == "GREETING = 'hej'\n"


def test_without_a_workspace_there_are_no_source_tools() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, says("ok"))
        rig.run()
        assert b"read_source" not in endpoint.last.body
