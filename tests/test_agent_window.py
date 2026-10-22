"""The session schedules painting without blocking model work or revealing a window."""
# pyright: reportPrivateUsage=false

import threading
from collections import deque
from collections.abc import Callable

from assistant.agent_session import Answer, Snapshot, StreamState
from assistant.agent_window import AgentWindow, View
from assistant.recording_state import RecordingAction

from .test_agent_session import asking


class RecordingView:
    __slots__ = ("closed", "shown", "states", "threads")

    def __init__(self) -> None:
        self.states: list[Snapshot] = []
        self.shown: list[bool] = []
        self.threads: list[int] = []
        self.closed = False

    def render(self, state: Snapshot) -> None:
        self.states.append(state)
        self.threads.append(threading.get_ident())

    def show(self, *, focus: bool = False) -> None:
        self.shown.append(focus)
        self.threads.append(threading.get_ident())

    def close(self) -> None:
        self.closed = True


class RecordingWindow(AgentWindow):
    """Keep the real session controller; replace only the UI thread and widgets."""

    __slots__ = ("jobs", "view")

    def __init__(
        self, stop: Callable[[], None], *, post: Callable[[Callable[[], None]], None] | None = None,
        create_view: Callable[[AgentWindow], View] | None = None, on_problem: Callable[[str], None] | None = None,
        on_reset: Callable[[], bool] | None = None, on_submit: Callable[[str], None] | None = None,
        on_waiting: Callable[[bool], None] | None = None,
        on_recording: Callable[[RecordingAction], None] | None = None,
    ) -> None:
        self.view = RecordingView()
        self.jobs: deque[Callable[[], None]] = deque()
        super().__init__(stop, post=self.jobs.append, create_view=lambda agent: self.view, on_problem=on_problem,
                         on_reset=on_reset, on_submit=on_submit, on_waiting=on_waiting, on_recording=on_recording)

    def paint(self) -> None:
        while self.jobs:
            self.jobs.popleft()()


def test_background_activity_and_queued_turns_do_not_show_or_focus_the_panel() -> None:
    panel = RecordingWindow(lambda: None)
    panel.begin("Find the workbook", lambda: None)
    panel.step("Looking for files")
    panel.say("I found it.")
    panel.queued("Open that one")
    panel.problem("The workbook is temporarily unavailable.")
    panel.done("Try again later.")
    panel.finish()
    assert not panel.view.states, "Painting must wait for the UI thread."
    panel.paint()
    assert panel.view.states and not panel.view.shown
    assert "Find the workbook" in panel.all_text()
    assert "Try again later." in panel.all_text()
    panel.show()
    panel.show(focus=True)
    panel.paint()
    assert panel.view.shown == [False, True]
    panel.close()
    panel.paint()
    assert panel.view.closed


def test_explicit_ui_actions_reach_the_existing_session_callbacks() -> None:
    submitted: list[str] = []
    commands: list[str] = []

    def reset() -> bool:
        commands.append("new")
        return True

    panel = RecordingWindow(lambda: commands.append("stop"), on_submit=submitted.append, on_reset=reset)
    assert panel.submit("Open my workbook\nwith the complete ending")
    assert not panel.submit("  ")
    panel.stop()
    assert panel.new()
    assert submitted == ["Open my workbook\nwith the complete ending"]
    assert commands == ["stop", "new"]
    panel.begin("Working", panel.stop)
    assert not panel.new(), "New chat is unavailable until the current turn finishes."
    panel.stopping()
    assert not panel.submit("Keep this draft until stopping finishes")
    panel.close()
    panel.paint()


def test_model_thread_never_calls_widgets_and_rapid_updates_are_coalesced() -> None:
    panel = RecordingWindow(lambda: None)
    panel.paint()
    ui_thread = threading.get_ident()

    def model() -> None:
        panel.begin("Read these files", lambda: None)
        publish = panel.updates()
        for number in range(100):
            panel.step(f"Read {number}")
            publish(Answer("stream", f"Reading {number}", number))
        publish(Answer("stream", "All done.", 100, StreamState.COMPLETE))
        panel.done("All done.")
        panel.finish()

    worker = threading.Thread(target=model)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive(), "Rendering must never hold up the model thread."
    assert len(panel.jobs) == 1
    panel.paint()
    assert set(panel.view.threads) == {ui_thread}
    assert len(panel.view.states[-1].activity) == 100
    assert len(panel.view.states[-1].messages) == 2
    assert panel.view.states[-1].messages[-1].text == "All done."
    panel.close()
    panel.paint()


def test_live_publishers_cannot_update_a_later_turn_or_a_reset_conversation() -> None:
    panel = RecordingWindow(lambda: None)
    panel.begin("First task", panel.stop)
    first = panel.updates()
    first(Answer("same-id", "First answer", 1, StreamState.COMPLETE))
    panel.finish()
    panel.begin("Next task", panel.stop)
    second = panel.updates()
    panel.paint()
    before = panel.state
    first(Answer("late", "This belongs to the first request", 2))
    assert panel.state is before and not panel.jobs
    second(Answer("second-answer", "Second answer", 3))
    assert panel.state.messages[-1].text == "Second answer"
    panel.reset()
    panel.begin("Fresh conversation", panel.stop)
    before = panel.state
    second(Answer("late", "This belongs to the old conversation", 4))
    assert panel.state is before and len(panel.state.messages) == 1
    panel.close()
    panel.paint()


def test_inline_answer_uses_the_same_composer_and_cancellation_restores_submission() -> None:
    requested = threading.Event()
    submitted: list[str] = []
    panel = RecordingWindow(lambda: None, on_submit=submitted.append, on_waiting=lambda waiting: requested.set() if waiting else None)
    answer = "The revenue sheet\nwith the entire ending. Åäö."
    with asking(panel.inputs, question=True) as pending:
        assert requested.wait(timeout=2)
        assert panel.submit(answer)
        assert pending.result() == answer
        assert not submitted
    requested.clear()
    with asking(panel.inputs, question=True) as pending:
        assert requested.wait(timeout=2)
        panel.stopping()
        assert pending.result() is None
    panel.finish()
    assert panel.submit("Continue with the same workbook")
    assert submitted == ["Continue with the same workbook"]
    panel.close()
    panel.paint()


def test_voice_answers_questions_and_only_explicit_complete_utterances_approve() -> None:
    requested = threading.Event()
    panel = RecordingWindow(lambda: None, on_waiting=lambda waiting: requested.set() if waiting else None)
    assert not panel.reply_by_voice("Start a task")
    with asking(panel.inputs, question=True) as pending:
        assert requested.wait(timeout=2)
        assert panel.reply_by_voice("The revenue sheet. Åäö.")
        assert pending.result() == "The revenue sheet. Åäö."
    requested.clear()
    with asking(panel.inputs) as pending:
        assert requested.wait(timeout=2)
        assert panel.reply_by_voice("Do not approve this")
        assert panel.waiting and pending.thread.is_alive()
        assert panel.reply_by_voice(" Approve. ")
        assert pending.result() == "approve"
    requested.clear()
    with asking(panel.inputs) as pending:
        assert requested.wait(timeout=2)
        assert panel.reply_by_voice("Decline!")
        assert pending.result() == "decline"
    assert not panel.reply_by_voice("A follow-up task")
    panel.close()
    assert panel.reply_by_voice("Late transcription after shutdown")
    panel.paint()


def test_quitting_during_ui_warmup_cancels_questions_and_never_creates_a_late_window() -> None:
    requested = threading.Event()
    panel = RecordingWindow(lambda: None, on_waiting=lambda waiting: requested.set() if waiting else None)
    panel.show()
    with asking(panel.inputs, question=True) as pending:
        assert requested.wait(timeout=2)
        panel.close()
        assert pending.result() is None
    panel.paint()
    assert not panel.view.states and not panel.view.shown and not panel.ready.is_set()
    assert panel.input("Too late", "", question=True, cancel=None) is None


def test_failed_widget_startup_reports_the_problem_and_can_be_retried() -> None:
    jobs: deque[Callable[[], None]] = deque()
    problems: list[str] = []
    view = RecordingView()
    attempts = 0

    def create(panel: AgentWindow) -> View:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Fixture: window initialization failed")
        return view

    panel = AgentWindow(lambda: None, post=jobs.append, create_view=create, on_problem=problems.append)
    while jobs:
        jobs.popleft()()
    assert len(problems) == 1 and not panel.ready.is_set()
    assert panel.input("Can't show this question", "", question=True, cancel=None) is None
    panel.show()
    while jobs:
        jobs.popleft()()
    assert panel.ready.is_set() and view.shown == [False]
    panel.close()
    while jobs:
        jobs.popleft()()
