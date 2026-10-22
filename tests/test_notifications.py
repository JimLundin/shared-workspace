"""Conversation projection, lifetimes and sizing are independent of Tk and clocks."""

from dataclasses import replace

from assistant.agent_session import (
    Answer,
    Approval,
    ApprovalDecided,
    Begin,
    Failed,
    Finished,
    Progress,
    Question,
    QuestionAnswered,
    ReasoningSummary,
    RecordingUpdated,
    Said,
    Session,
    StopRequested,
    StreamState,
    Succeeded,
    ToolActivity,
    transition,
)
from assistant.notifications import (
    MAX_VISIBLE,
    Busy,
    Entry,
    Expires,
    Frame,
    Held,
    Kind,
    MessageKey,
    Motion,
    Paused,
    Recording,
    ResponseKey,
    Space,
    Stack,
    Tone,
    Working,
    advance,
    allocate,
    body,
    hint,
    hover,
    next_expiry,
    reading_hold,
    spinning,
    tone,
)
from assistant.recording_state import RecordingState


def test_conversation_keeps_the_request_and_actions_separate_from_the_spinner() -> None:
    session = transition(Session(identity="one"), Begin("Find the workbook", 1))
    stack = advance(Stack(), session, 100)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), Kind.WORK]
    request, work = stack.toasts
    assert body(request.content) == "Find the workbook" and tone(request.content) is Tone.USER
    assert isinstance(work.content, Working) and work.content.phase is Busy.THINKING
    assert body(work.content) == "" and spinning(work.content)
    session = transition(session, ReasoningSummary("summary", "Checking **recent files**.", 2))
    assert advance(stack, session, 2000) == stack
    session = transition(session, ToolActivity("tool", "read_file", "budget.xlsx", 3))
    stack = advance(stack, session, 8000)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), MessageKey(2), Kind.WORK]
    assert stack.toasts[0] == request and stack.toasts[-1] == work
    action = stack.toasts[1]
    assert body(action.content) == "budget.xlsx" and tone(action.content) is Tone.ACTION
    session = transition(session, ToolActivity("tool", "read_file", "budget.xlsx", 4, Succeeded("Read 24 rows")))
    stack = advance(stack, session, 8001)
    assert stack.toasts[1].key == action.key and stack.toasts[1].order == action.order
    assert "Read 24 rows" in body(stack.toasts[1].content)
    assert all(toast.lifetime is Held.ACTIVE for toast in stack.toasts)
    assert next_expiry(stack) is None and "Checking **recent files**." in session.all_text()


def test_new_task_adds_its_request_without_repurposing_the_previous_entry() -> None:
    session = transition(Session(identity="one"), Begin("Old request", 1))
    session = transition(session, ToolActivity("tool", "read_file", "old.xlsx", 2, Failed("Missing file")))
    stack = advance(Stack(), session, 10)
    original = stack.toasts[:2]
    session = transition(transition(session, Finished()), Begin("The new request", 3))
    stack = advance(stack, session, 11)
    assert stack.toasts[:2] == original
    assert [body(toast.content) for toast in stack.toasts] == ["Old request", "old.xlsx\nMissing file", "The new request", ""]
    assert tone(stack.toasts[-1].content) is Tone.THINKING


def test_reading_timers_start_when_work_finishes_and_never_revive_expired_entries() -> None:
    session = transition(Session(identity="one"), Begin("Read it", 1))
    session = transition(session, Answer("stream", "Reading", 2))
    stack = advance(Stack(), session, 10)
    stack = advance(stack, session, 1000)
    assert len(stack.toasts) == 3 and all(toast.lifetime is Held.ACTIVE for toast in stack.toasts)
    answer = next(toast for toast in stack.toasts if toast.key == MessageKey(1))
    session = transition(session, Answer("stream", "Here is the answer.", 3, StreamState.COMPLETE))
    stack = advance(stack, session, 1500)
    assert next(toast for toast in stack.toasts if toast.key == answer.key).order == answer.order
    assert all(toast.lifetime is Held.ACTIVE for toast in stack.toasts)
    session = transition(session, Finished())
    stack = advance(stack, session, 2000)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), MessageKey(1)]
    assert all(toast.lifetime == Expires(2012) for toast in stack.toasts)
    assert advance(stack, session, 2011).toasts
    history = session.all_text()
    stack = advance(stack, session, 2012)
    assert not stack.toasts and next_expiry(stack) is None
    session = transition(session, Progress("An unrelated status update"))
    assert not advance(stack, session, 9000).toasts and session.all_text() == history
    # Beginning another task must not bring old, already dismissed messages back.
    stack = advance(stack, transition(session, Begin("Continue", 4)), 9001)
    assert [body(toast.content) for toast in stack.toasts] == ["Continue", ""]


def test_bursts_keep_controls_and_the_latest_conversation_with_bounded_history() -> None:
    session = transition(Session(identity="one"), Begin("Find it", 1))
    session = transition(session, RecordingUpdated(RecordingState(phase="recording", identity="meeting"), 1))
    stack = advance(Stack(), session, 0)
    for number in range(100):
        session = transition(session, Said(f"Update {number}", 2))
        stack = advance(stack, session, number / 10)
        assert len(stack.toasts) <= MAX_VISIBLE
    assert [body(toast.content) for toast in stack.toasts[:3]] == ["Update 97", "Update 98", "Update 99"]
    assert [toast.key for toast in stack.toasts[-2:]] == [Kind.RECORDING, Kind.WORK]
    assert all(toast.lifetime is Held.ACTIVE for toast in advance(stack, session, 10000).toasts)
    session = transition(session, Finished())
    stack = advance(stack, session, 20000)
    stack = advance(stack, session, 20031)
    assert [toast.key for toast in stack.toasts] == [Kind.RECORDING]
    assert "Update 0" in session.all_text() and "Update 99" in session.all_text()


def test_pending_question_cannot_expire_or_be_crowded_out() -> None:
    session = transition(Session(identity="one"), Begin("Find it", 1))
    session = transition(session, Question("question", "Which workbook?", "Choose a filename.", 2))
    stack = advance(Stack(), session, 100)
    for index in range(8):
        session = transition(session, Said(f"Update {index}", 4))
    stack = advance(stack, session, 10000)
    question = next(toast for toast in stack.toasts if toast.key == MessageKey(1))
    assert question.lifetime is Held.ACTIVE
    assert body(question.content) == "Which workbook?\n\nChoose a filename."
    assert tone(question.content) is Tone.QUESTION and "Hold N" in hint(question.content)
    work = next(toast.content for toast in stack.toasts if toast.key is Kind.WORK)
    assert not spinning(work) and body(work) == ""
    stopping = advance(stack, transition(session, StopRequested()), 10001)
    assert spinning(next(toast.content for toast in stopping.toasts if toast.key is Kind.WORK))


def test_voice_response_is_a_new_entry_and_the_question_stays_intact() -> None:
    session = transition(Session(identity="one"), Begin("Find it", 1))
    session = transition(session, Question("question", "Which workbook?", "Choose a filename.", 2))
    stack = advance(Stack(), session, 100)
    question = stack.toasts[1]
    session = transition(session, QuestionAnswered("question", "The revenue sheet"))
    stack = advance(stack, session, 101)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), MessageKey(1), ResponseKey(1), Kind.WORK]
    assert body(stack.toasts[1].content) == body(question.content) and stack.toasts[1].order == question.order
    assert not hint(stack.toasts[1].content)
    assert body(stack.toasts[2].content) == "The revenue sheet" and tone(stack.toasts[2].content) is Tone.USER
    assert spinning(stack.toasts[-1].content)
    assert advance(stack, session, 102).toasts == stack.toasts


def test_approval_decision_is_separate_from_the_original_request() -> None:
    session = transition(Session(identity="one"), Begin("Open it", 1))
    session = transition(session, Approval("approval", "Open this?", "budget.xlsx", 2))
    stack = advance(Stack(), session, 100)
    original = body(stack.toasts[1].content)
    session = transition(session, ApprovalDecided("approval", "approved"))
    stack = advance(stack, session, 101)
    assert body(stack.toasts[1].content) == original
    assert stack.toasts[2].key == ResponseKey(1) and body(stack.toasts[2].content) == "Approved"


def test_meeting_stays_until_capture_and_transcription_end_then_clears() -> None:
    recording = RecordingState(phase="recording", identity="meeting", preview="First line.")
    session = transition(Session(identity="one"), RecordingUpdated(recording, 1))
    stack = advance(Stack(), session, 100)
    for phase in ("recording", "stopping", "finishing", "paused"):
        session = replace(session, recording=replace(recording, phase=phase, seconds=3600))
        stack = advance(stack, session, 10000)
        assert len(stack.toasts) == 1 and stack.toasts[0].lifetime is Held.ACTIVE
    session = replace(session, recording=replace(recording, phase="saved", seconds=3600))
    stack = advance(stack, session, 20000)
    assert isinstance(stack.toasts[0].content, Recording) and stack.toasts[0].lifetime == Expires(20012)
    stack = advance(stack, session, 20012)
    assert not stack.toasts and not advance(stack, session, 30000).toasts
    session = replace(session, recording=replace(recording, identity="next", seconds=0))
    assert advance(stack, session, 30001).toasts[0].lifetime is Held.ACTIVE


def test_hover_preserves_remaining_reading_time_and_reset_discards_old_timers() -> None:
    session = transition(Session(identity="one"), Said("A short reply", 1))
    stack = advance(Stack(), session, 100)
    key = stack.toasts[0].key
    stack = hover(stack, key, True, 105)
    assert stack.toasts[0].lifetime == Paused(7)
    stack = advance(stack, session, 5000)
    assert stack.toasts and next_expiry(stack) is None
    stack = hover(stack, key, False, 5000)
    assert next_expiry(stack) == 5007 and not advance(stack, session, 5007).toasts
    session = transition(Session(identity="two"), Said("The new conversation", 2))
    reset = advance(stack, session, 5008)
    assert len(reset.toasts) == 1 and isinstance(reset.toasts[0].content, Entry)
    assert body(reset.toasts[0].content) == "The new conversation" and next_expiry(reset) == 5020


def test_unread_content_stays_through_updates_then_gets_a_full_expiry_delay() -> None:
    session = transition(transition(Session(identity="one"), Begin("Read it", 1)), Answer("stream", "A long reply", 2))
    stack = advance(Stack(), session, 100)
    key = MessageKey(1)
    stack = reading_hold(stack, key, True, 100, busy=True, hovered=False)
    session = transition(session, Answer("stream", "A long reply with its ending.", 3, StreamState.COMPLETE))
    session = transition(session, Finished())
    stack = advance(stack, session, 101)
    stack = advance(stack, session, 10000)
    assert len(stack.toasts) == 1 and stack.toasts[0].lifetime is Held.READING
    assert next_expiry(stack) is None
    stack = reading_hold(stack, key, False, 10001, busy=False, hovered=True)
    assert stack.toasts[0].lifetime == Paused(12)
    stack = hover(stack, key, False, 20000)
    assert next_expiry(stack) == 20012
    assert not advance(stack, session, 20012).toasts


def test_space_allocation_reserves_previews_and_prioritizes_pending_input() -> None:
    spaces = (Space(80, 250, 0), Space(80, 300, 2), Space(80, 250, 0), Space(40, 40, 3))
    assert allocate(spaces, 650) == (80, 300, 230, 40)
    assert allocate(spaces, 1000) == (250, 300, 250, 40)
    assert allocate(spaces, 200) == (0, 80, 80, 40)
    assert allocate(spaces, 120) == (0, 80, 0, 40)
    for available in range(1001):
        heights = allocate(spaces, available)
        assert sum(heights) <= available
        assert all(height == 0 or space.minimum <= height <= space.preferred for height, space in zip(heights, spaces, strict=True))


def test_interrupted_motion_retargets_from_the_visible_position_without_overshoot() -> None:
    move = Motion(Frame(500, 120, 100, 0), Frame(300, 240, 300, 1), 10)
    current = move.sample(10.1)
    assert 300 < current.y < 500 and 0 < current.alpha < 1
    assert 120 < current.width < 240
    assert current.y + current.height == 600
    retargeted = Motion(current, Frame(200, 280, 400, 1), 10.1)
    assert retargeted.sample(10.1) == current
    positions = [retargeted.sample(10.1 + step / 100).y for step in range(30)]
    assert positions == sorted(positions, reverse=True)
    widths = [retargeted.sample(10.1 + step / 100).width for step in range(30)]
    assert widths == sorted(widths) and widths[-1] == 280
    assert retargeted.sample(20) == Frame(200, 280, 400, 1) and retargeted.finished(20)
