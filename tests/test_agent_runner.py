"""Conversation lifecycle through its execution and session interfaces, on either OS."""

import importlib
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from assistant.activity import ContextSnapshot
from assistant.agent import Log
from assistant.agent_execution import AgentExecution
from assistant.agent_runner import Active, AgentRunner, Control, Execution, Idle, Stopping, Task, transition
from assistant.llm import Settings
from usage import Detail

from .fake_endpoint import Received, Reply, fake_endpoint
from .test_agent import FakeMachine, Rig, says
from .test_agent_session import asking
from .test_agent_window import RecordingWindow
from .test_computer_tools import context as request_context


class RecordingExecution:
    def __init__(self, run: Callable[[Task, threading.Event], None] | None = None) -> None:
        self.tasks: list[Task] = []
        self.resets = 0
        self.operation = run

    def run(self, task: Task, log: Log, stop: threading.Event) -> None:
        self.tasks.append(task)
        if self.operation is not None:
            self.operation(task, stop)
        log.done("Stopped." if stop.is_set() else "Done.")

    def reset(self) -> None:
        self.resets += 1


class RunnerRig:
    def __init__(
        self, execution: Execution | None = None, *, context: Callable[[], ContextSnapshot] | None = None,
        on_waiting: Callable[[bool], None] | None = None,
    ) -> None:
        self.windows: list[RecordingWindow] = []
        self.errors: list[str] = []
        self.completed: list[bool] = []
        self.waiting = on_waiting
        self.runner = AgentRunner(
            execution or RecordingExecution(), self.create_window, report_error=self.errors.append,
            on_done=lambda: self.completed.append(self.runner.running()), context=context,
        )

    def create_window(self, runner: AgentRunner) -> RecordingWindow:
        panel = RecordingWindow(runner.stop, on_submit=runner.start, on_reset=runner.new_conversation, on_waiting=self.waiting)
        self.windows.append(panel)
        return panel

    @property
    def panel(self) -> RecordingWindow:
        self.runner.prepare()
        return self.windows[0]


def test_task_transitions_preserve_context_and_stop_discards_the_queue() -> None:
    first, second = Task("First", request_context("First workbook")), Task("Second", request_context("Second workbook"))
    active = transition(Idle.READY, first)
    queued = transition(active, second)
    assert active == Active(first), "Queuing must leave the earlier snapshot unchanged."
    assert transition(queued, Control.COMPLETE) == Active(second)
    stopped = transition(queued, Control.STOP)
    assert stopped == Stopping(first)
    assert transition(stopped, Task("Too early")) == stopped
    assert transition(stopped, Control.COMPLETE) is Idle.READY
    assert transition(Idle.READY, Control.STOP) is Idle.READY


def test_prewarmed_panel_accepts_typed_follow_ups_without_waiting_for_painting() -> None:
    execution = RecordingExecution()
    rig = RunnerRig(execution)
    panel = rig.panel
    panel.paint()
    assert not panel.view.shown
    assert panel.submit("  Open my workbook  ")
    assert rig.runner.wait(5)
    assert execution.tasks == [Task("Open my workbook")]
    assert rig.runner.window is panel and len(rig.windows) == 1
    assert "Open my workbook" in panel.all_text()
    assert rig.completed == [False], "Completion must observe an idle runner."
    panel.paint()
    assert panel.view.shown == [False]


def test_voice_replies_reach_pending_input_instead_of_starting_a_task() -> None:
    requested = threading.Event()
    execution = RecordingExecution()
    rig = RunnerRig(execution, on_waiting=lambda waiting: requested.set() if waiting else None)
    with asking(rig.panel.inputs, question=True) as pending:
        assert requested.wait(2)
        rig.runner.voice("Use the revenue sheet.")
        assert pending.result() == "Use the revenue sheet."
        assert not execution.tasks and not rig.runner.running()
    rig.runner.voice("Now open it")
    assert rig.runner.wait(5)
    assert execution.tasks == [Task("Now open it")]


def test_queued_requests_keep_their_invocation_context_without_revealing_the_panel() -> None:
    entered, release = threading.Event(), threading.Event()
    first, second = request_context("First workbook"), request_context("Second workbook")
    current = first

    def run(task: Task, stop: threading.Event) -> None:
        if task.text == "Summarize this":
            entered.set()
            assert release.wait(5)

    execution = RecordingExecution(run)
    rig = RunnerRig(execution, context=lambda: current)
    try:
        rig.runner.start("Summarize this")
        assert entered.wait(5) and rig.runner.running()
        assert not rig.runner.new_conversation()
        current = second
        rig.runner.start("And this one")
        current = request_context("An unrelated window opened later")
        assert rig.panel.state.queued == ("And this one",)
    finally:
        release.set()
        assert rig.runner.wait(5)
    assert execution.tasks == [Task("Summarize this", first), Task("And this one", second)]
    rig.panel.paint()
    assert rig.panel.view.shown == [False], "A queued turn must not reopen or focus the panel."
    assert (rig.completed, execution.resets) == ([False], 0)
    assert rig.runner.new_conversation() and execution.resets == 1
    assert not rig.panel.state.messages


def test_stop_cancels_the_running_turn_and_discards_queued_work() -> None:
    entered = threading.Event()

    def run(task: Task, stop: threading.Event) -> None:
        if task.text == "First":
            entered.set()
            assert stop.wait(5)

    execution = RecordingExecution(run)
    rig = RunnerRig(execution)
    rig.runner.start("First")
    try:
        assert entered.wait(5)
        rig.runner.start("Discard this")
    finally:
        rig.runner.stop()
        assert rig.runner.wait(5)
    rig.runner.start("What happened?")
    assert rig.runner.wait(5)
    assert execution.tasks == [Task("First"), Task("What happened?")]
    assert not execution.resets and not rig.panel.state.queued


def test_execution_failure_finishes_the_session_and_allows_a_new_request() -> None:
    entered, release = threading.Event(), threading.Event()

    def run(task: Task, stop: threading.Event) -> None:
        if task.text == "Fail":
            entered.set()
            assert release.wait(5)
            raise OSError("fixture execution failed")

    execution = RecordingExecution(run)
    rig = RunnerRig(execution)
    try:
        rig.runner.start("Fail")
        assert entered.wait(5)
        rig.runner.start("Discard this")
    finally:
        release.set()
        assert rig.runner.wait(5)
    assert len(rig.errors) == 1 and "fixture execution failed" in rig.errors[0]
    assert "Couldn't continue" in rig.panel.all_text() and not rig.panel.state.busy
    rig.runner.start("Try again")
    assert rig.runner.wait(5)
    assert execution.tasks == [Task("Fail"), Task("Try again")]
    assert rig.completed == [False, False]


def test_requests_can_queue_while_the_first_window_is_being_created() -> None:
    entered, release = threading.Event(), threading.Event()
    rig = RunnerRig()
    execution = RecordingExecution()

    def create(runner: AgentRunner) -> RecordingWindow:
        entered.set()
        assert release.wait(5)
        return rig.create_window(runner)

    runner = AgentRunner(execution, create, on_done=lambda: None, report_error=rig.errors.append)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(runner.start, "First")
        try:
            assert entered.wait(5) and runner.running()
            runner.start("Second")
        finally:
            release.set()
            pending.result(5)
            assert runner.wait(5)
    assert execution.tasks == [Task("First"), Task("Second")]
    assert "Second" in rig.windows[0].all_text() and not rig.windows[0].state.queued


def test_stop_during_window_creation_prevents_execution() -> None:
    entered, release = threading.Event(), threading.Event()
    execution = RecordingExecution()

    def create(runner: AgentRunner) -> RecordingWindow:
        entered.set()
        assert release.wait(5)
        return RecordingWindow(runner.stop)

    errors: list[str] = []
    runner = AgentRunner(execution, create, on_done=lambda: None, report_error=errors.append)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(runner.start, "First")
        try:
            assert entered.wait(5)
            runner.start("Discard this")
            runner.stop()
        finally:
            release.set()
            pending.result(5)
            assert runner.wait(5)
    assert not execution.tasks and not errors
    assert runner.window is not None and not runner.window.state.busy


def test_wait_during_a_later_start_waits_for_that_run() -> None:
    entered, release = threading.Event(), threading.Event()
    execution = RecordingExecution()
    errors: list[str] = []

    def starting() -> None:
        if execution.tasks:
            entered.set()
            assert release.wait(5)

    runner = AgentRunner(
        execution, lambda runner: RecordingWindow(runner.stop), on_done=lambda: None,
        report_error=errors.append, on_start=starting,
    )
    runner.start("First")
    assert runner.wait(5)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(runner.start, "Second")
        try:
            assert entered.wait(5) and runner.running()
            assert not runner.wait(0), "The previous worker's completion must not finish a new request."
        finally:
            release.set()
            pending.result(5)
            assert runner.wait(5)
    assert execution.tasks == [Task("First"), Task("Second")] and not errors


def test_wait_includes_the_completion_callback() -> None:
    entered, release = threading.Event(), threading.Event()
    errors: list[str] = []

    def completed() -> None:
        entered.set()
        assert release.wait(5)

    runner = AgentRunner(
        RecordingExecution(), lambda runner: RecordingWindow(runner.stop), on_done=completed, report_error=errors.append,
    )
    runner.start("First")
    try:
        assert entered.wait(5) and not runner.running()
        assert not runner.wait(0)
    finally:
        release.set()
        assert runner.wait(5)
    assert not errors


def test_sdk_execution_keeps_history_until_a_new_conversation() -> None:
    importlib.import_module("assistant.sdk")
    metrics: list[Mapping[str, Detail]] = []
    errors: list[str] = []
    machine = FakeMachine()
    with fake_endpoint() as endpoint:
        wire = Rig(endpoint, says("I found budget.xlsx."))
        execution = AgentExecution(
            Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", ""), machine,
            journal=lambda entry: None, record=metrics.append, report_error=errors.append, close_background=machine.close_background,
        )
        rig = RunnerRig(execution)
        for task in ("Find my workbook", "Open that one"):
            rig.runner.start(task)
            assert rig.runner.wait(10)
        assert [m["content"] for m in wire.sent() if m["role"] == "user"] == ["Find my workbook", "Open that one"]
        assert rig.runner.new_conversation()
        rig.runner.start("Start fresh")
        assert rig.runner.wait(10)
        assert [m["content"] for m in wire.sent() if m["role"] == "user"] == ["Start fresh"]
    assert len(metrics) == 3 and all(entry["ok"] is True for entry in metrics) and not errors


def test_stopped_sdk_execution_closes_background_work_and_preserves_history() -> None:
    importlib.import_module("assistant.sdk")
    entered, release = threading.Event(), threading.Event()
    closed: list[bool] = []
    errors: list[str] = []
    with fake_endpoint() as endpoint:
        wire = Rig(endpoint, says("Done."))

        def respond(request: Received) -> Reply:
            if len(endpoint.received) == 1:
                entered.set()
                assert release.wait(5)
            return says("Done.")

        endpoint.respond = respond
        execution = AgentExecution(
            Settings(endpoint.url, "fixture", "transcribe", "chat", "agent", "", ""), FakeMachine(),
            journal=lambda entry: None, record=lambda details: None, report_error=errors.append, close_background=lambda: closed.append(True),
        )
        rig = RunnerRig(execution)
        try:
            rig.runner.start("Find my workbook")
            assert entered.wait(5)
            rig.runner.start("Discard this queued request")
            rig.runner.stop()
        finally:
            release.set()
            assert rig.runner.wait(10)
        assert len(endpoint.received) == 1 and closed == [True]
        rig.runner.start("What happened?")
        assert rig.runner.wait(10)
        assert [m["content"] for m in wire.sent() if m["role"] == "user"] == ["Find my workbook", "What happened?"]
    assert closed == [True] and not errors
