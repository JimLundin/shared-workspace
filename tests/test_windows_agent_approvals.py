"""Tool approvals belong to the session; they must not open foreground dialogs."""

import queue
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows tool UI")

from assistant.agent import Agent
from assistant.agent_input import Inputs
from assistant.agent_session import Approval, Event, InputCancelled
from assistant.app import WinMachine
from assistant.desktop import WinDesktop
from assistant.llm import Settings, ToolCall
from assistant.ui import Ui

from .test_agent import FakeWindow


class InlineUi(Ui):
    __slots__ = ("requests",)

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bool]] = []

    def agent_input(
        self, title: str, detail: str = "", *, question: bool = False, cancel: threading.Event | None = None,
    ) -> str | None:
        self.requests.append((title, detail, question))
        return "The budget workbook" if question else "approve"


class WaitingUi(Ui):
    __slots__ = ("events", "inputs")

    def __init__(self) -> None:
        self.events: queue.Queue[Event] = queue.Queue()
        self.inputs = Inputs(self.events.put)

    def agent_input(
        self, title: str, detail: str = "", *, question: bool = False, cancel: threading.Event | None = None,
    ) -> str | None:
        return self.inputs.ask(title, detail, question=question, cancel=cancel)


def test_open_tool_runs_without_approval_by_default() -> None:
    ui = InlineUi()
    machine = WinMachine(WinDesktop.__new__(WinDesktop), ui)
    agent = Agent(Settings("", "", "", "", "", "", ""), machine, FakeWindow(), threading.Event(), journal=lambda entry: None)
    with patch("assistant.app.system.ask_yes_no", return_value=False) as popup, patch("assistant.app.os.startfile") as opened:
        result = agent.call(ToolCall("open-1", "open", '{"target":"C:\\\\example\\\\budget.xlsx"}'))
        popup.assert_not_called()
        assert ui.requests == []
        opened.assert_called_once_with(r"C:\example\budget.xlsx")
        assert result.ok


def test_agent_question_uses_the_session_without_a_separate_text_prompt() -> None:
    ui = InlineUi()
    machine = WinMachine(WinDesktop.__new__(WinDesktop), ui)
    agent = Agent(Settings("", "", "", "", "", "", ""), machine, FakeWindow(), threading.Event(), journal=lambda entry: None)
    with patch.object(Ui, "prompt", return_value=None) as popup:
        result = agent.call(ToolCall("question-1", "ask_user", '{"question":"Which workbook?"}'))
        popup.assert_not_called()
        assert result.text == "The budget workbook"
        assert ui.requests == [("Which workbook?", "", True)]


def test_stop_cancels_an_open_tool_while_its_inline_approval_is_waiting() -> None:
    ui = WaitingUi()
    stopped = threading.Event()
    machine = WinMachine(WinDesktop.__new__(WinDesktop), ui)
    agent = Agent(
        Settings("", "", "", "", "", "", "", agent_approvals=True), machine, FakeWindow(), stopped, journal=lambda entry: None,
    )

    with ThreadPoolExecutor(max_workers=1) as worker, patch("assistant.app.os.startfile") as opened:
        result = worker.submit(agent.call, ToolCall("open-1", "open", '{"target":"budget.xlsx"}'))
        try:
            assert isinstance(ui.events.get(timeout=2), Approval)
            stopped.set()
            assert not result.result(timeout=2).ok, "Stop must unblock a waiting tool without a user click."
            opened.assert_not_called()
            assert isinstance(ui.events.get(timeout=2), InputCancelled)
        finally:
            stopped.set()
            ui.inputs.cancel_all()
