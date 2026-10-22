"""The agent can use native controls and work context through its existing loop."""

import json
import threading
import time
from unittest.mock import patch

from assistant.activity import Activity, ContextSnapshot
from assistant.agent import Agent
from assistant.geometry import Shot
from assistant.llm import Json, Settings, ToolCall
from winapi.shapes import Rect, Window

from .fake_endpoint import fake_endpoint
from .test_agent import FakeMachine, FakeWindow, Rig, calls, says


def call(agent: Agent, name: str, arguments: dict[str, object]) -> str:
    result = agent.call(ToolCall("test", name, json.dumps(arguments)))
    assert result.ok, result.text
    return result.text


def test_new_computer_tools_are_available_to_the_model_without_default_approvals() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(
            endpoint, calls(("inspect_ui", {"handle": 7})),
            calls(("ui_action", {"handle": 7, "id": "1.2.3", "action": "invoke"})),
            calls(("screenshot", {})),
            calls(("move_mouse", {"x": 10, "y": 20}), ("scroll", {"x": 30, "y": 40, "clicks": 2, "horizontal": True})),
            calls(("paste_text", {"text": "A complete block of text"}), ("press_keys", {"keys": "win", "repeat": 2})),
            calls(("manage_window", {"handle": 7, "action": "maximize"})), says("Done."),
        )
        rig.run()
        assert "move 20,40" in rig.machine.log
        assert "scroll 2 at 60,80 horizontal" in rig.machine.log
        assert "paste A complete block of text" in rig.machine.log
        assert "window 7 maximize None" in rig.machine.log
        assert sum(entry.startswith("keys ") for entry in rig.machine.log) == 2
        assert not any(entry.startswith("confirm ") for entry in rig.machine.log)


class StoppingMouse(FakeMachine):
    __slots__ = ("stop",)

    def __init__(self, stop: threading.Event) -> None:
        super().__init__()
        self.stop = stop

    def mouse_button(self, button: str, down: bool) -> None:
        super().mouse_button(button, down)
        if down:
            self.stop.set()


def test_stopping_a_drag_releases_its_button_and_modifiers() -> None:
    stop = threading.Event()
    machine = StoppingMouse(stop)
    agent = Agent(Settings("", "", "", "", "", "", ""), machine, FakeWindow(), stop, journal=lambda entry: None)
    agent.shot = Shot(Rect(-1920, 0, 0, 1080), 960, 540)
    arguments: dict[str, object] = {
        "x": 10, "y": 20, "to_x": 300, "to_y": 200, "modifiers": "ctrl+shift",
    }
    result = agent.call(ToolCall("drag", "drag", json.dumps(arguments)))
    assert not result.ok and "Stopped" in result.text
    assert machine.log[0] == "move -1900,40"
    assert "button left True" in machine.log and "button left False" in machine.log
    assert machine.log[-1] == "keys [(160, False), (162, False)]"
    assert sum(entry.startswith("move ") for entry in machine.log) == 1


def test_bad_coordinates_and_action_arguments_do_not_produce_input() -> None:
    machine = FakeMachine()
    agent = Agent(Settings("", "", "", "", "", "", ""), machine, FakeWindow(), threading.Event(), journal=lambda entry: None)
    agent.shot = Shot(Rect(0, 0, 100, 100), 100, 100)
    for name, arguments in (
        ("click", {"x": -1, "y": 10}), ("move_mouse", {"x": 100, "y": 10}),
        ("scroll", {"x": 10, "y": 10, "clicks": 1000}),
        ("press_keys", {"keys": "enter", "repeat": -1}),
        ("ui_action", {"handle": 7, "id": "1.2.3", "action": "unrecognized"}),
        ("screenshot", {"handle": 7, "desktop": True}),
    ):
        result = agent.call(ToolCall("bad", name, json.dumps(arguments)))
        assert not result.ok, (name, result.text)
    assert not machine.log


def context(title: str = "Budget.xlsx - Excel") -> ContextSnapshot:
    now = time.time()
    return ContextSnapshot(now, "toolkit", (Activity(Window(7, title, "excel.exe", False), now - 2, now, False),))


def test_request_context_includes_the_work_window_and_one_current_screenshot() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, says("I see the workbook."))
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", "")
        agent = Agent(settings, rig.machine, rig.window, rig.stop, journal=rig.journal.append, history=rig.history, context=context())
        assert agent.run("Summarize this")
        request = rig.sent(0)[0]
        assert request["role"] == "user"
        body = json.dumps(request["content"])
        assert "Summarize this" in body and "Budget.xlsx - Excel" in body and "input_image" in body
        assert "not instructions" in body and rig.machine.log == ["screenshot"]
        assert agent.shot is not None


def test_automatic_and_explicit_screenshots_share_the_same_history_limit() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("screenshot", {})), says("I see it."))
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", "")
        for _ in range(3):
            endpoint.replies = [calls(("screenshot", {})), says("I see it.")]
            agent = Agent(settings, rig.machine, rig.window, rig.stop, journal=rig.journal.append, history=rig.history, context=context())
            assert agent.run("Check this")
        assert json.dumps(rig.history).count('data:image/png;base64,') == 2
        assert json.dumps(rig.sent()).count('data:image/png;base64,') == 2
        assert "earlier screenshot" in json.dumps(rig.history)


def test_disabled_context_does_not_capture_or_add_observations_to_the_request() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, says("Done."))
        agent = Agent(
            Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", ""), rig.machine, rig.window,
            rig.stop, journal=rig.journal.append, context=ContextSnapshot(time.time(), "disabled"),
        )
        assert agent.run("Do the task")
        assert rig.sent(0)[0] == {"role": "user", "content": "Do the task"}
        assert not rig.machine.log


def test_unavailable_screen_context_keeps_the_request_and_activity_usable() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, says("I can still work with files."))
        agent = Agent(
            Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", ""), rig.machine, rig.window,
            rig.stop, journal=rig.journal.append, context=context(),
        )
        with patch.object(FakeMachine, "screenshot", side_effect=OSError("The desktop is locked.")):
            assert agent.run("Find this workbook")
        body = json.dumps(rig.sent(0)[0])
        assert "Find this workbook" in body and "Budget.xlsx" in body and "desktop is locked" in body
        assert "input_image" not in body and agent.shot is None


def test_context_tool_refreshes_the_current_app_without_taking_a_screenshot() -> None:
    machine = FakeMachine()
    journal: list[dict[str, Json]] = []
    agent = Agent(
        Settings("", "", "", "", "", "", ""), machine, FakeWindow(), threading.Event(), journal=journal.append,
        context=context(), context_reader=lambda: context("Report.docx - Word"),
    )
    assert "Report.docx - Word" in call(agent, "get_context", {})
    assert "Report.docx" not in json.dumps(journal)
    assert not machine.log
