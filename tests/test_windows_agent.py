"""The desktop runner retains one conversation across queued voice and typed turns."""
# pyright: reportPrivateUsage=false

import sys
import threading
import unittest
from typing import cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows agent runner")

from assistant.agent import Agent
from assistant.app import AgentRunner, WinMachine, warm_agent
from assistant.llm import Settings
from assistant.ui import Ui

from .fake_endpoint import Received, Reply, fake_endpoint
from .test_agent import FakeMachine, FakeWindow, Rig, says
from .test_agent_session import asking
from .test_agent_window import RecordingWindow
from .test_computer_tools import context as request_context


def test_prewarmed_panel_accepts_typed_follow_ups_without_blocking_on_painting() -> None:
    ui = Ui.__new__(Ui)
    ui._agent = None
    ui._agent_lock = threading.Lock()
    ui._agent_waiting = None
    ui._recording = None
    ui._thread = threading.current_thread()
    runner = AgentRunner(
        Settings("", "", "", "", "", "", ""), WinMachine.__new__(WinMachine), ui,
        on_done=lambda: None, on_problem=lambda message: None,
    )
    with (
        patch("assistant.ui.AgentWindow", RecordingWindow),
        patch("assistant.app.Agent.run", return_value=False) as run,
        patch("assistant.app.usage.record"),
    ):
        runner.prepare()
        panel = cast(RecordingWindow, runner.window)
        assert not panel.view.shown, "Prewarming must not reveal the window."
        assert panel.submit("Open my workbook")
        assert runner.thread is not None
        runner.thread.join(timeout=5)
        assert not runner.thread.is_alive()
        run.assert_called_once_with("Open my workbook")
        assert runner.window is panel
        assert panel.state.messages[0].text == "Open my workbook"
        ui.close_agent()
        panel.paint()


def test_voice_replies_reach_pending_input_instead_of_queueing_a_task() -> None:
    requested = threading.Event()
    panel = RecordingWindow(lambda: None, on_waiting=lambda waiting: requested.set() if waiting else None)
    runner = AgentRunner(
        Settings("", "", "", "", "", "", ""), WinMachine.__new__(WinMachine), Ui.__new__(Ui),
        on_done=lambda: None, on_problem=lambda message: None,
    )
    runner.window = panel
    with patch.object(AgentRunner, "start") as start:
        with asking(panel.inputs, question=True) as pending:
            assert requested.wait(timeout=2)
            runner.voice("Use the revenue sheet.")
            assert pending.result() == "Use the revenue sheet."
            start.assert_not_called()
            assert not runner.pending
        runner.voice("Now open it")
        start.assert_called_once_with("Now open it")
    panel.close()
    panel.paint()


def test_agent_is_no_longer_running_when_its_finished_callback_updates_the_tray() -> None:
    states: list[bool] = []
    settings = Settings("", "", "", "", "", "", "")
    runner = AgentRunner(
        settings, WinMachine.__new__(WinMachine), Ui.__new__(Ui),
        on_done=lambda: states.append(runner.running()), on_problem=lambda message: None,
    )
    with (
        patch.object(Ui, "agent", return_value=FakeWindow()),
        patch("assistant.app.Agent.run", return_value=False),
        patch("assistant.app.usage.record"),
    ):
        runner.start("Open my workbook")
        assert runner.thread is not None
        runner.thread.join(timeout=5)
        assert not runner.thread.is_alive()
    assert states == [False]


def test_button_requests_share_context_and_new_conversation_clears_it() -> None:
    warm_agent()
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, says("I found budget.xlsx."))
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", "")
        runner = AgentRunner(settings, cast(WinMachine, FakeMachine()), Ui.__new__(Ui), on_done=lambda: None, on_problem=rig.window.problem)
        with patch.object(Ui, "agent", return_value=rig.window), patch("assistant.app.usage.record"):
            for task in ("Find my workbook", "Open that one"):
                runner.start(task)
                assert runner.thread is not None
                runner.thread.join(timeout=5)
                assert not runner.thread.is_alive()
            assert [m["content"] for m in rig.sent() if m["role"] == "user"] == ["Find my workbook", "Open that one"]
            assert runner.new_conversation()
            runner.start("Start fresh")
            assert runner.thread is not None
            runner.thread.join(timeout=5)
            assert not runner.thread.is_alive()
            assert [m["content"] for m in rig.sent() if m["role"] == "user"] == ["Start fresh"]


def test_another_button_press_queues_a_follow_up_while_the_agent_is_working() -> None:
    warm_agent()
    requested, release = threading.Event(), threading.Event()
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, says("Done."))
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", "")

        def respond(request: Received) -> Reply:
            if len(endpoint.received) == 1:
                requested.set()
                assert release.wait(timeout=5)
            return says("Done.")

        endpoint.respond = respond
        runner = AgentRunner(settings, cast(WinMachine, FakeMachine()), Ui.__new__(Ui), on_done=lambda: None, on_problem=rig.window.problem)
        with patch.object(Ui, "agent", return_value=rig.window) as open_panel, patch("assistant.app.usage.record"):
            try:
                runner.start("Find my workbook")
                assert requested.wait(timeout=5)
                assert not runner.new_conversation()
                runner.start("Open that one")
            finally:
                release.set()
                if runner.thread is not None:
                    runner.thread.join(timeout=5)
            assert not runner.running()
            assert len(endpoint.received) == 2
            options = cast("dict[str, object]", open_panel.call_args_list[1].kwargs)
            assert options["reveal"] is False
            assert options["queued"] is True
            assert [m["content"] for m in rig.sent() if m["role"] == "user"] == ["Find my workbook", "Open that one"]


def test_stop_clears_queued_follow_ups_but_keeps_context_for_the_next_request() -> None:
    warm_agent()
    requested, release = threading.Event(), threading.Event()
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, says("Done."))
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", "")

        def respond(request: Received) -> Reply:
            requested.set()
            assert release.wait(timeout=5)
            return says("Done.")

        endpoint.respond = respond
        runner = AgentRunner(settings, cast(WinMachine, FakeMachine()), Ui.__new__(Ui), on_done=lambda: None, on_problem=rig.window.problem)
        with patch.object(Ui, "agent", return_value=rig.window), patch("assistant.app.usage.record"):
            try:
                runner.start("Find my workbook")
                assert requested.wait(timeout=5)
                runner.start("Discard this queued request")
                runner.stop()
            finally:
                release.set()
                if runner.thread is not None:
                    runner.thread.join(timeout=5)
            assert not runner.running()
            assert len(endpoint.received) == 1
            runner.start("What happened?")
            assert runner.thread is not None
            runner.thread.join(timeout=5)
            assert not runner.thread.is_alive()
            assert [m["content"] for m in rig.sent() if m["role"] == "user"] == ["Find my workbook", "What happened?"]


def test_queued_requests_keep_their_invocation_window_context() -> None:
    first = request_context("First workbook")
    second = request_context("Second workbook")
    current = first
    requested, release = threading.Event(), threading.Event()
    seen: list[tuple[str, str]] = []

    def run(agent: Agent, task: str) -> bool:
        assert agent.context is not None and agent.context.window is not None
        seen.append((task, agent.context.window.title))
        if len(seen) == 1:
            requested.set()
            assert release.wait(timeout=5)
        return True

    runner = AgentRunner(
        Settings("", "", "", "", "", "", ""), cast(WinMachine, FakeMachine()), Ui.__new__(Ui),
        on_done=lambda: None, on_problem=lambda message: None, context=lambda: current,
    )
    with patch.object(Ui, "agent", return_value=FakeWindow()), patch.object(Agent, "run", run), patch("assistant.app.usage.record"):
        try:
            runner.start("Summarize this")
            assert requested.wait(timeout=5)
            current = second
            runner.start("And this one")
            current = request_context("An unrelated window opened later")
        finally:
            release.set()
            if runner.thread is not None:
                runner.thread.join(timeout=5)
        assert not runner.running()
    assert seen == [("Summarize this", "First workbook"), ("And this one", "Second workbook")]
