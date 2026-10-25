"""Conversation bubbles, lifetimes and sizing are independent of widgets and clocks."""

from dataclasses import replace

from assistant.agent_session import (
    Answer,
    Approval,
    ApprovalDecided,
    Begin,
    Enqueued,
    Failed,
    Finished,
    Progress,
    Question,
    QuestionAnswered,
    ReasoningSummary,
    RecordingUpdated,
    Result,
    Said,
    Session,
    StopRequested,
    StreamState,
    Succeeded,
    ToolActivity,
    transition,
)
from assistant.notification_text import details, spans
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
    Response,
    Settled,
    Space,
    Stack,
    Tone,
    Turn,
    TurnKey,
    Working,
    advance,
    allocate,
    body,
    caption,
    heading,
    hint,
    hover,
    next_expiry,
    reading_hold,
    results,
    spinning,
    steps,
    tone,
)
from assistant.recording_state import RecordingState


def bubble(stack: Stack, request: int = 0) -> Turn:
    content = next(toast.content for toast in stack.toasts if toast.key == TurnKey(request))
    assert isinstance(content, Turn)
    return content


def test_thinking_keeps_its_actions_in_one_stable_bubble_in_order() -> None:
    session = transition(Session(identity="one"), Begin("Find the workbook", 1))
    stack = advance(Stack(), session, 100)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), TurnKey(0)]
    request, work = stack.toasts
    assert body(request.content) == "Find the workbook" and tone(request.content) is Tone.USER
    turn = bubble(stack)
    assert isinstance(turn.state, Working) and turn.state.phase is Busy.THINKING
    assert body(turn) == "" and spinning(turn)
    session = transition(session, ReasoningSummary("summary", "Checking **recent files**.", 2))
    assert advance(stack, session, 2000) == stack
    session = transition(session, ToolActivity("tool", "read_file", "budget.xlsx", 3))
    stack = advance(stack, session, 8000)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), TurnKey(0)]
    assert stack.toasts[0] == request and stack.toasts[1].order == work.order
    turn = bubble(stack)
    assert [entry.index for entry in steps(turn)] == [2]
    assert body(turn) == "" and "budget.xlsx" in "".join(span.text for span in details(turn))
    session = transition(session, ToolActivity("next", "read_file", "forecast.xlsx", 4))
    session = transition(session, ToolActivity("tool", "read_file", "budget.xlsx", 5, Succeeded("Read 24 rows")))
    stack = advance(stack, session, 8001)
    assert [entry.index for entry in steps(bubble(stack))] == [2, 3]
    assert "Read 24 rows" in body(steps(bubble(stack))[0])
    assert caption(bubble(stack)) == "1 of 2 steps complete"
    assert all(toast.lifetime is Held.ACTIVE for toast in stack.toasts)
    assert next_expiry(stack) is None and "Checking **recent files**." in session.all_text()
    session = transition(transition(session, Enqueued("Check another sheet")), Enqueued("Compare last year"))
    turn = bubble(advance(stack, session, 8002))
    assert isinstance(turn.state, Working) and turn.state.generation == session.generation
    assert heading(turn) == "Thinking · 2 queued"


def test_collapsing_steps_keeps_the_finished_result_attached_and_formatted() -> None:
    session = transition(Session(identity="one"), Begin("Read it", 1))
    session = transition(session, ToolActivity("tool", "read_file", '{"path":"budget.xlsx"}', 2, Succeeded('{"rows":24}')))
    session = transition(session, Said("The totals **match**. Revenue is 124,500 kr.", 3))
    stack = advance(Stack(), session, 10)
    key, order = stack.toasts[1].key, stack.toasts[1].order
    session = transition(session, Finished())
    stack = advance(stack, session, 11)
    turn = bubble(stack)
    assert len(stack.toasts) == 2 and (stack.toasts[1].key, stack.toasts[1].order) == (key, order)
    assert turn.state is Settled.FINISHED and heading(turn) == "Finished" and not spinning(turn)
    assert body(turn) == "The totals **match**. Revenue is 124,500 kr."
    assert "".join(span.text for span in spans(turn)) == "The totals match. Revenue is 124,500 kr."
    assert any("bold" in span.tags for span in spans(turn))
    assert "Path: budget.xlsx" in "".join(span.text for span in details(turn))
    assert "Rows: 24" in "".join(span.text for span in details(turn))
    assert caption(turn) == "1 completed step"
    assert [entry.index for entry in results(turn)] == [2]


def test_follow_up_turns_keep_their_own_steps_and_results() -> None:
    session = transition(Session(identity="one"), Begin("Old request", 1))
    session = transition(session, ToolActivity("tool", "read_file", "old.xlsx", 2, Failed("Missing file")))
    session = transition(session, Said("The old workbook is missing.", 3))
    session = transition(session, Finished())
    stack = advance(Stack(), session, 10)
    original = bubble(stack)
    session = transition(session, Begin("The new request", 4))
    session = transition(session, ToolActivity("new", "read_file", "new.xlsx", 5))
    stack = advance(stack, session, 11)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), TurnKey(0), MessageKey(3), TurnKey(3)]
    assert bubble(stack) == original
    assert body(bubble(stack)) == "The old workbook is missing."
    assert [entry.index for entry in steps(bubble(stack, 3))] == [4]
    assert "old.xlsx" not in "".join(span.text for span in details(bubble(stack, 3)))
    assert not advance(stack, Session(identity="two"), 12).toasts


def test_expired_terminal_turns_without_output_do_not_revive_on_follow_up() -> None:
    cases: tuple[tuple[Result, Settled], ...] = (("Stopped", Settled.STOPPED), ("Needs attention", Settled.ATTENTION))
    for result, expected in cases:
        session = transition(Session(identity="one"), Begin("Read it", 1))
        stack = advance(Stack(), session, 100)
        session = replace(session, phase="idle", result=result)
        stack = advance(stack, session, 101)
        assert bubble(stack).state is expected
        stack = advance(stack, session, 132)
        assert not stack.toasts
        session = transition(session, Begin("Try another file", 2))
        stack = advance(stack, session, 133)
        assert [toast.key for toast in stack.toasts] == [MessageKey(1), TurnKey(1)]


def test_unprompted_work_keeps_its_reply_and_later_requests_separate() -> None:
    session = Session(identity="one", phase="running")
    stack = advance(Stack(), session, 100)
    turn = bubble(stack, -1)
    assert isinstance(turn.state, Working) and heading(turn) == "Thinking"
    session = transition(session, Said("The background check is complete.", 1))
    session = transition(session, Finished())
    stack = advance(stack, session, 101)
    # A standalone completed reply is still visible without a user request.
    assert any(body(toast.content) == "The background check is complete." for toast in stack.toasts)
    session = transition(session, Begin("Check the new workbook", 2))
    stack = advance(stack, session, 102)
    assert not bubble(stack, 1).entries


def test_earlier_replies_are_discoverable_as_steps_without_tool_calls() -> None:
    session = transition(Session(identity="one"), Begin("Compare the sheets", 1))
    session = transition(session, Said("The first sheet has 24 rows.", 2))
    stack = advance(Stack(), session, 100)
    assert caption(bubble(stack)) == "" and not steps(bubble(stack))
    session = transition(session, Said("Both sheets match.", 3))
    turn = bubble(advance(stack, session, 101))
    assert caption(turn) == "Show steps"
    assert body(turn) == "Both sheets match."
    assert "The first sheet has 24 rows." in "".join(span.text for span in details(turn))


def test_a_successful_retry_finishes_after_an_earlier_action_failed() -> None:
    session = transition(Session(identity="one"), Begin("Read the workbook", 1))
    session = transition(session, ToolActivity("missing", "read_file", "old.xlsx", 2, Failed("Missing file")))
    assert bubble(advance(Stack(), transition(session, Finished()), 100)).state is Settled.ATTENTION
    session = transition(session, ToolActivity("retry", "read_file", "new.xlsx", 3, Succeeded("Read 24 rows")))
    turn = bubble(advance(Stack(), transition(session, Finished()), 101))
    assert turn.state is Settled.FINISHED and not spinning(turn)
    assert "Missing file" in "".join(span.text for span in details(turn))


def test_reading_timers_start_when_work_finishes_and_never_revive_expired_entries() -> None:
    session = transition(Session(identity="one"), Begin("Read it", 1))
    session = transition(session, Answer("stream", "Reading", 2))
    stack = advance(Stack(), session, 10)
    stack = advance(stack, session, 1000)
    assert len(stack.toasts) == 2 and all(toast.lifetime is Held.ACTIVE for toast in stack.toasts)
    session = transition(session, Answer("stream", "Here is the answer.", 3, StreamState.COMPLETE))
    stack = advance(stack, session, 1500)
    assert all(toast.lifetime is Held.ACTIVE for toast in stack.toasts)
    session = transition(session, Finished())
    stack = advance(stack, session, 2000)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), TurnKey(0)]
    assert all(toast.lifetime == Expires(2012) for toast in stack.toasts)
    assert advance(stack, session, 2011).toasts
    history = session.all_text()
    stack = advance(stack, session, 2012)
    assert not stack.toasts and next_expiry(stack) is None
    session = transition(session, Progress("An unrelated status update"))
    assert not advance(stack, session, 9000).toasts and session.all_text() == history
    stack = advance(stack, transition(session, Begin("Continue", 4)), 9001)
    assert [body(toast.content) for toast in stack.toasts] == ["Continue", ""]


def test_bursts_keep_controls_and_all_steps_without_creating_more_windows() -> None:
    session = transition(Session(identity="one"), Begin("Find it", 1))
    session = transition(session, RecordingUpdated(RecordingState(phase="recording", identity="meeting"), 1))
    stack = advance(Stack(), session, 0)
    for number in range(100):
        session = transition(session, Said(f"Update {number}", 2))
        stack = advance(stack, session, number / 10)
        assert len(stack.toasts) == 3
    turn = bubble(stack)
    assert body(turn) == "Update 99" and len(steps(turn)) == 99
    assert stack.toasts[-1].key is Kind.RECORDING
    assert all(toast.lifetime is Held.ACTIVE for toast in advance(stack, session, 10000).toasts)
    session = transition(session, Finished())
    stack = advance(stack, session, 20000)
    stack = advance(stack, session, 20031)
    assert [toast.key for toast in stack.toasts] == [Kind.RECORDING]
    assert "Update 0" in session.all_text() and "Update 99" in session.all_text()
    for index in range(12):
        session = transition(session, Begin(f"Request {index}", index + 10))
        stack = advance(stack, session, 20100 + index)
        assert len(stack.toasts) <= MAX_VISIBLE
        assert isinstance(stack.toasts[-2].content, Turn) and stack.toasts[-1].key is Kind.RECORDING


def test_pending_question_stays_visible_when_the_steps_are_collapsed() -> None:
    session = transition(Session(identity="one"), Begin("Find it", 1))
    session = transition(session, Question("question", "Which workbook?", "Choose a filename.", 2))
    stack = advance(Stack(), session, 100)
    for index in range(8):
        session = transition(session, Said(f"Update {index}", 4))
    stack = advance(stack, session, 10000)
    turn = bubble(stack)
    assert stack.toasts[-1].lifetime is Held.ACTIVE
    assert "Which workbook?\n\nChoose a filename." in body(turn)
    assert "Which workbook?" in "".join(span.text for span in spans(turn))
    assert tone(turn) is Tone.QUESTION and "Hold N" in hint(turn)
    assert not spinning(turn)
    stopping = advance(stack, transition(session, StopRequested()), 10001)
    assert spinning(bubble(stopping)) and heading(bubble(stopping)) == "Stopping…"


def test_voice_response_stays_with_its_question_then_moves_into_steps() -> None:
    session = transition(Session(identity="one"), Begin("Find it", 1))
    session = transition(session, Question("question", "Which workbook?", "Choose a filename.", 2))
    stack = advance(Stack(), session, 100)
    session = transition(session, QuestionAnswered("question", "The revenue sheet"))
    stack = advance(stack, session, 101)
    turn = bubble(stack)
    assert [toast.key for toast in stack.toasts] == [MessageKey(0), TurnKey(0)]
    assert body(turn) == "Which workbook?\n\nChoose a filename.\n\nThe revenue sheet"
    assert isinstance(turn.entries[-1], Response) and not hint(turn)
    assert spinning(turn) and advance(stack, session, 102).toasts == stack.toasts
    session = transition(session, ToolActivity("read", "read_file", "revenue.xlsx", 3, Succeeded("Read it.")))
    session = transition(session, Said("The revenue sheet is ready.", 4))
    turn = bubble(advance(stack, session, 103))
    assert body(turn) == "The revenue sheet is ready."
    assert [entry.index for entry in steps(turn)] == [1, 1, 2]
    assert "The revenue sheet" in "".join(span.text for span in details(turn))


def test_standalone_question_keeps_its_answer_in_the_same_bubble() -> None:
    session = transition(Session(identity="one"), Question("question", "Open the workbook?", "budget.xlsx", 1))
    stack = advance(Stack(), session, 100)
    assert [toast.key for toast in stack.toasts] == [TurnKey(-1)]
    turn = bubble(stack, -1)
    assert isinstance(turn.state, Working) and turn.state.phase is Busy.WAITING and not spinning(turn)
    session = transition(session, QuestionAnswered("question", "Yes"))
    stack = advance(stack, session, 101)
    assert [toast.key for toast in stack.toasts] == [TurnKey(-1)]
    assert body(bubble(stack, -1)) == "Open the workbook?\n\nbudget.xlsx\n\nYes"


def test_approval_decision_stays_with_the_original_request() -> None:
    session = transition(Session(identity="one"), Begin("Open it", 1))
    session = transition(session, Approval("approval", "Open this?", "budget.xlsx", 2))
    stack = advance(Stack(), session, 100)
    original = body(bubble(stack))
    session = transition(session, ApprovalDecided("approval", "approved"))
    turn = bubble(advance(stack, session, 101))
    assert body(turn) == original + "\n\nApproved" and not hint(turn)
    assert isinstance(turn.entries[-1], Response) and turn.entries[-1].index == 1


def test_stopping_keeps_a_partial_reply_and_the_interrupted_steps() -> None:
    session = transition(Session(identity="one"), Begin("Read it", 1))
    session = transition(session, ToolActivity("read", "read_file", "budget.xlsx", 2))
    session = transition(session, Answer("reply", "Here is the partial answer.", 3))
    stack = advance(Stack(), session, 10)
    session = transition(transition(session, StopRequested()), Finished())
    turn = bubble(advance(stack, session, 11))
    assert turn.state is Settled.STOPPED and not spinning(turn)
    assert body(turn) == "Here is the partial answer."
    assert "budget.xlsx" in "".join(span.text for span in details(turn))
    assert heading(turn) == "Stopped"


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
    key = TurnKey(0)
    stack = reading_hold(stack, key, True, 100, busy=True, hovered=False)
    session = transition(session, Answer("stream", "A long reply with its ending.", 3, StreamState.COMPLETE))
    stack = advance(stack, session, 100.5)  # New output while busy must retain the unread hold.
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
