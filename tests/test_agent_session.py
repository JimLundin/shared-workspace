"""Conversation continuity and explicit, cancellable inline human input."""

import queue
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace

from assistant.agent_input import Inputs
from assistant.agent_session import (
    Answer,
    Answered,
    Approval,
    ApprovalControls,
    ApprovalDecided,
    Begin,
    Cancelled,
    Done,
    Enqueued,
    Event,
    Failed,
    Finished,
    InputCancelled,
    Progress,
    Question,
    QuestionAnswered,
    ReasoningSummary,
    RecordingUpdated,
    ResponseText,
    Said,
    Session,
    StopRequested,
    StreamState,
    Succeeded,
    ToolActivity,
    presentation,
    transition,
)
from assistant.recording_state import RecordingState


def test_live_messages_replace_their_row_and_final_completion_is_not_duplicated() -> None:
    session = transition(Session(), Begin("Find the workbook", 1))
    session = transition(session, ReasoningSummary("summary", "Checking", 2))
    session = transition(session, Answer("answer", "Found", 3))
    before = session
    session = transition(session, ReasoningSummary("summary", "Checking the files.", 4, StreamState.COMPLETE))
    session = transition(session, Answer("answer", "Found budget.xlsx.", 5, StreamState.COMPLETE))
    assert [message.created_at for message in session.messages] == [1, 2, 3]
    assert before.messages[-1].text == "Found"
    assert len(session.messages) == 3 and session.last_said == "Found budget.xlsx."
    assert transition(session, Answer("answer", "A late chunk", 6)) is session
    session = transition(session, Done("Found budget.xlsx.", 6))
    session = transition(session, Finished())
    assert len(session.messages) == 3 and session.status == "Ready"
    assert presentation(session.messages[1]).muted and presentation(session.messages[1]).style == "markdown"
    assert not presentation(session.messages[2]).muted
    assert "Reasoning summary\nChecking the files." in session.all_text()


def test_stop_seals_partial_text_but_accepts_the_result_of_an_already_running_tool() -> None:
    session = transition(Session(), Begin("Read the file", 1))
    session = transition(session, Answer("answer", "Reading", 2))
    session = transition(session, ToolActivity("tool", "read_file", "budget.xlsx", 3))
    assert presentation(session.messages[-1]).role == "Read file · running"
    session = transition(session, StopRequested())
    assert transition(session, Answer("answer", "Late text", 4, StreamState.COMPLETE)) is session
    assert transition(session, ToolActivity("late", "open", "", 4)) is session
    assert transition(session, ToolActivity("late", "open", "", 4, Succeeded("Done"))) is session
    session = transition(session, ToolActivity("tool", "read_file", "budget.xlsx", 4, Succeeded("Read it.")))
    assert presentation(session.messages[-1]).role == "Read file · done"
    assert transition(session, ToolActivity("tool", "read_file", "", 5, Failed("Too late"))) is session
    session = transition(session, Done("Stopped.", 5))
    session = transition(session, Finished())
    assert isinstance(session.messages[1], Answer) and session.messages[1].state is StreamState.INTERRUPTED
    assert transition(session, Answer("new", "Too late", 6)) is session


def test_finishing_marks_unfinished_summaries_and_tools_and_preserves_completed_results() -> None:
    session = transition(Session(), Begin("Do the work", 1))
    session = transition(session, ReasoningSummary("summary", "Checking", 2))
    session = transition(session, ToolActivity("pending", "read_file", "a.txt", 3))
    session = transition(session, ToolActivity("failed", "read_file", "b.txt", 4, Failed("File not found")))
    session = transition(session, Finished())
    assert presentation(session.messages[1]).role == "Reasoning summary · interrupted"
    assert presentation(session.messages[2]).role == "Read file · stopped"
    assert presentation(session.messages[3]).role == "Read file · failed"
    assert "b.txt\nFile not found" in session.all_text()


def test_meeting_preview_updates_without_adding_messages_or_changing_the_agent_task() -> None:
    session = Session()
    session = transition(session, Begin('Find the workbook', 0, queued=False))
    state = RecordingState(phase="recording", identity="meeting-1", preview="First sentence.")
    session = transition(session, RecordingUpdated(state, 0))
    previous = session
    session = transition(session, Said('Found it.', 0))
    for index in range(500):
        session = transition(session, RecordingUpdated(replace(state, seconds=index, preview=f'Recent sentence {index}.'), 0))
    assert len(session.messages) == 3 and session.busy and session.last_said == "Found it."
    assert previous.messages[1].text.endswith("First sentence.")
    assert "Recent sentence 499" in session.messages[1].text
    assert session.recording.seconds == 499


def input_id(event: Event) -> str:
    assert isinstance(event, (Question, Approval))
    return event.request_id


class Pending:
    __slots__ = ("answers", "thread")

    def __init__(self, inputs: Inputs, *, question: bool, cancel: threading.Event | None) -> None:
        self.answers: list[str | None] = []
        self.thread = threading.Thread(
            target=lambda: self.answers.append(inputs.ask("Open this?", "budget.xlsx", question=question, cancel=cancel)),
            daemon=True,
        )
        self.thread.start()

    def result(self) -> str | None:
        self.thread.join(timeout=2)
        assert not self.thread.is_alive(), "An inline request stayed blocked after response or cancellation."
        assert len(self.answers) == 1
        return self.answers[0]


@contextmanager
def asking(inputs: Inputs, *, question: bool = False, cancel: threading.Event | None = None) -> Generator[Pending]:
    pending = Pending(inputs, question=question, cancel=cancel)
    try:
        yield pending
    finally:
        inputs.cancel_all()
        pending.thread.join(timeout=2)


def test_follow_ups_keep_messages_and_only_deduplicate_the_current_turns_final_answer() -> None:
    session = Session()
    for prompt in ("Find my workbook", "Look for it again"):
        session = transition(session, Begin(prompt, 0, queued=False))
        session = transition(session, Said('I found budget.xlsx.', 0))
        session = transition(session, Done('I found budget.xlsx.', 0))
        session = transition(session, Finished())
    assert [message.text for message in session.messages] == [
        "Find my workbook", "I found budget.xlsx.", "Look for it again", "I found budget.xlsx.",
    ]
    assert not session.busy
    assert session.status == "Ready"


def test_queued_messages_appear_once_when_their_turn_starts_and_activity_is_separate() -> None:
    session = Session()
    session = transition(session, Begin('Find my workbook', 0, queued=False))
    session = transition(session, Enqueued('Open that one'))
    assert session.queued == ("Open that one",)
    assert "Open that one" not in session.all_text()
    session = transition(session, Done('Found it.', 0))
    session = transition(session, Begin('Open that one', 0, queued=True))
    session = transition(session, Progress('open {"target":"budget.xlsx"}'))
    assert not session.queued
    assert session.all_text().count("Open that one") == 1
    assert '"target"' not in session.all_text()
    assert session.activity == ('open {"target":"budget.xlsx"}',)


def test_render_snapshots_remain_unchanged_when_the_model_continues() -> None:
    session = Session()
    session = transition(session, Begin('Read the workbook', 0, queued=False))
    before = session
    session = transition(session, Enqueued('Then summarize it'))
    session = transition(session, Said('Read it.', 0))
    session = transition(session, Finished())
    assert len(before.messages) == 1 and before.queued == () and before.busy


def test_session_events_can_be_replayed_and_branched_without_a_clock_or_shared_mutation() -> None:
    original = Session(identity="replay")
    events: tuple[Event, ...] = (
        Begin("Find the workbook", 10), Progress("Looking"), Question("q1", "Which workbook?", "", 12),
    )
    pending = original
    for event in events:
        pending = transition(pending, event)
    replay = original
    for event in events:
        replay = transition(replay, event)
    assert replay == pending and original.messages == ()
    answer = "  Budget.xlsx\nKeep the full answer.  "
    answered = transition(pending, QuestionAnswered("q1", answer))
    cancelled = transition(pending, InputCancelled("q1"))
    assert pending.question is not None and answered.question is None and cancelled.question is None
    assert isinstance(answered.messages[-1], Question) and answered.messages[-1].reply == Answered(answer)
    assert isinstance(cancelled.messages[-1], Question) and cancelled.messages[-1].reply == Cancelled()
    assert answered.messages[0] is pending.messages[0]
    assert [message.created_at for message in answered.messages] == [10, 12]


def test_mismatched_and_late_responses_cannot_change_another_input_or_its_controls() -> None:
    approval = transition(Session(), Approval("a1", "Open this?", "budget.xlsx", 10))
    assert transition(approval, QuestionAnswered("a1", "approve")) is approval
    assert transition(approval, ApprovalDecided("old", "approved")) is approval
    assert presentation(approval.messages[0]).response == ApprovalControls("a1")
    declined = transition(approval, ApprovalDecided("a1", "declined"))
    assert transition(declined, ApprovalDecided("a1", "approved")) is declined
    assert presentation(declined.messages[0]).response == ResponseText("Declined")
    question = transition(Session(), Question("q1", "Which one?", "", 11))
    assert transition(question, ApprovalDecided("q1", "approved")) is question
    assert presentation(question.messages[0]).response is None


def test_waiting_state_and_decision_remain_in_the_transcript() -> None:
    session = Session()
    session = transition(session, Begin('Open my workbook', 0, queued=False))
    session = transition(session, Approval('open-1', 'Open this?', 'budget.xlsx', 0))
    session = transition(session, Progress('Checking progress'))
    assert bool(session.waiting)
    assert session.status == "Needs your approval"
    session = transition(session, ApprovalDecided('open-1', 'declined'))
    assert not session.waiting
    assert isinstance(session.messages[-1], Approval) and session.messages[-1].decision == "declined"
    assert "budget.xlsx" in session.all_text()


def test_stopping_removes_the_queue_and_preserves_the_cancelled_request() -> None:
    session = Session()
    session = transition(session, Begin('Open my workbook', 0, queued=False))
    session = transition(session, Enqueued('Discard this'))
    session = transition(session, Approval('open-1', 'Open this?', '', 0))
    session = transition(session, StopRequested())
    session = transition(session, InputCancelled('open-1'))
    assert bool(session.stopping) and not session.queued
    assert str(session.status) == "Stopping…"
    session = transition(session, Done('Stopped by you.', 0))
    session = transition(session, Finished())
    assert not session.busy and not session.stopping
    assert session.status == "Stopped"
    assert isinstance(session.messages[1], Approval) and session.messages[1].decision == "cancelled"


def test_approval_requires_an_explicit_response_to_its_exact_request() -> None:
    events: queue.Queue[Event] = queue.Queue()
    inputs = Inputs(events.put)
    with asking(inputs) as pending:
        request = events.get(timeout=2)
        identity = input_id(request)
        assert isinstance(request, Approval) and identity
        assert not inputs.respond("unrelated", "approve")
        assert not inputs.respond(identity, "yes")
        assert not pending.answers
        assert inputs.respond(identity, "approve")
        assert not inputs.respond(identity, "approve")
        assert pending.result() == "approve"
        assert events.get(timeout=2) == ApprovalDecided(identity, "approved")
    assert not inputs.pending


def test_stop_unblocks_a_waiting_approval_and_late_approval_cannot_approve_the_next_tool() -> None:
    events: queue.Queue[Event] = queue.Queue()
    inputs = Inputs(events.put)
    stopped = threading.Event()
    with asking(inputs, cancel=stopped) as pending:
        previous = input_id(events.get(timeout=2))
        stopped.set()
        assert pending.result() is None
        assert isinstance(events.get(timeout=2), InputCancelled)
    with asking(inputs) as pending:
        current = input_id(events.get(timeout=2))
        assert current != previous
        assert not inputs.respond(previous, "approve")
        assert not pending.answers
        assert inputs.respond(current, "decline")
        assert pending.result() == "decline"
        assert events.get(timeout=2) == ApprovalDecided(current, "declined")


def test_stopped_request_does_not_appear_or_wait() -> None:
    events: queue.Queue[Event] = queue.Queue()
    inputs = Inputs(events.put)
    stopped = threading.Event()
    stopped.set()
    assert inputs.ask("Open this?", "budget.xlsx", question=False, cancel=stopped) is None
    assert events.empty()
    assert not inputs.pending


def test_stop_wins_over_an_approval_delivered_at_the_same_time() -> None:
    stopped = threading.Event()
    events: list[Event] = []

    def publish(event: Event) -> None:
        events.append(event)
        if isinstance(event, (Question, Approval)):
            assert inputs.respond(input_id(event), "approve")
            stopped.set()

    inputs = Inputs(publish)
    assert inputs.ask("Open this?", "budget.xlsx", question=False, cancel=stopped) is None
    assert isinstance(events[-1], InputCancelled)


def test_question_keeps_the_full_answer_and_rejects_an_empty_reply() -> None:
    events: queue.Queue[Event] = queue.Queue()
    inputs = Inputs(events.put)
    answer = "Budget for October\nUse the updated copy, please. Åäö."
    with asking(inputs, question=True) as pending:
        identity = input_id(events.get(timeout=2))
        assert not inputs.respond(identity, "  ")
        assert inputs.respond(identity, answer)
        assert pending.result() == answer
        resolved = events.get(timeout=2)
        assert resolved == QuestionAnswered(identity, answer)


def test_closing_the_view_cancels_every_waiting_input_without_approving_it() -> None:
    events: queue.Queue[Event] = queue.Queue()
    inputs = Inputs(events.put)
    with asking(inputs) as approval, asking(inputs, question=True) as question:
        identities = [input_id(events.get(timeout=2)) for _ in range(2)]
        inputs.cancel_all()
        assert approval.result() is None
        assert question.result() is None
        assert all(isinstance(events.get(timeout=2), InputCancelled) for _ in range(2))
        assert all(not inputs.respond(identity, "approve") for identity in identities)
    assert not inputs.pending
