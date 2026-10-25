"""Durable input uses the same exact-request seam for native, typed, and voice replies."""

import threading
from collections import deque
from collections.abc import Callable

from assistant.agent_session import Approval, Question, pending
from assistant.agent_window import AgentWindow, View

from .test_agent_window import RecordingWindow


def has_pending_input(panel: AgentWindow) -> bool:
    return panel.waiting


def test_durable_questions_share_typed_voice_and_button_routing_without_waiting_threads() -> None:
    replies: list[str] = []
    submitted: list[str] = []
    waiting: list[bool] = []

    def accept(answer: str) -> bool:
        replies.append(answer)
        return True

    panel = RecordingWindow(lambda: None, on_submit=submitted.append, on_waiting=waiting.append)
    first = Question("first", "Which workbook?", "Choose the source.", 1.0)
    panel.request_input(first, accept)
    panel.request_input(first, accept)
    assert sum(isinstance(message, Question) for message in panel.state.messages) == 1
    assert has_pending_input(panel) and panel.submit("Revenue\nwith the full ending. Åäö.")
    panel.request_input(first, accept)
    assert not has_pending_input(panel) and not panel.respond("first", "A duplicate answer")
    panel.request_input(Question("second", "Which sheet?", "", 2.0), accept)
    assert panel.reply_by_voice("The 2026 sheet.")
    panel.request_input(Question("third", "Which column?", "", 3.0), accept)
    assert panel.respond("third", "Actual revenue")
    assert replies == ["Revenue\nwith the full ending. Åäö.", "The 2026 sheet.", "Actual revenue"]
    assert not submitted and waiting == [True, False, True, False, True, False]
    panel.request_input(Question("fourth", "Still needed?", "", 4.0), accept)
    panel.clear_external_input("third")
    assert has_pending_input(panel)
    panel.clear_external_input("fourth")
    assert not has_pending_input(panel) and panel.submit("Start a follow-up") and submitted == ["Start a follow-up"]
    panel.close()
    panel.paint()


def test_a_new_request_during_reply_submission_remains_pending() -> None:
    replies: list[tuple[str, str]] = []
    panel = RecordingWindow(lambda: None)
    latest = Question("new", "Which year?", "", 2.0)

    def answer_new(answer: str) -> bool:
        replies.append(("new", answer))
        return True

    def answer_old(answer: str) -> bool:
        replies.append(("old", answer))
        panel.request_input(latest, answer_new)
        return True

    panel.request_input(Question("old", "Which workbook?", "", 1.0), answer_old)
    assert panel.respond("old", "Revenue")
    assert panel.state.question == latest and has_pending_input(panel)
    panel.clear_external_input("old")
    assert not panel.respond("old", "A late answer") and panel.state.question == latest
    assert panel.submit("2026")
    assert replies == [("old", "Revenue"), ("new", "2026")] and not has_pending_input(panel)
    panel.close()
    panel.paint()


def test_a_restored_request_stays_pending_when_the_prior_submission_returns() -> None:
    replies: list[str] = []
    panel = RecordingWindow(lambda: None)
    question = Question("retry", "Which workbook?", "The previous answer could not be saved.", 1.0)

    def retry(answer: str) -> bool:
        replies.append(answer)
        return True

    def failed_commit(answer: str) -> bool:
        replies.append(answer)
        panel.clear_external_input(question.request_id)
        panel.request_input(question, retry)
        return True

    panel.request_input(question, failed_commit)
    assert panel.respond(question.request_id, "Revenue")
    assert panel.state.question == question and has_pending_input(panel)
    assert panel.submit("The revenue workbook")
    assert replies == ["Revenue", "The revenue workbook"] and not has_pending_input(panel)
    panel.close()
    panel.paint()


def test_duplicate_polling_and_simultaneous_replies_cannot_submit_twice() -> None:
    entered, release = threading.Event(), threading.Event()
    replies: list[str] = []
    accepted: list[bool] = []
    panel = RecordingWindow(lambda: None)
    question = Question("one", "Which workbook?", "", 1.0)

    def accept(answer: str) -> bool:
        replies.append(answer)
        entered.set()
        assert release.wait(timeout=3)
        return True

    panel.request_input(question, accept)
    sender = threading.Thread(target=lambda: accepted.append(panel.respond("one", "Revenue")))
    sender.start()
    try:
        assert entered.wait(timeout=2)
        panel.request_input(question, accept)
        assert not panel.respond("one", "Second answer")
    finally:
        release.set()
        sender.join(timeout=2)
    assert not sender.is_alive() and accepted == [True]
    panel.request_input(question, accept)
    assert not has_pending_input(panel) and not panel.respond("one", "Third answer")
    assert replies == ["Revenue"]
    panel.close()
    panel.paint()


def test_rejected_replies_can_retry_without_resolving_or_reopening_the_request() -> None:
    replies: list[str] = []
    panel = RecordingWindow(lambda: None)
    request = Approval("approval", "Write the workbook", "The full reviewed content.", 1.0)

    def accept(answer: str) -> bool:
        replies.append(answer)
        return len(replies) > 1

    panel.request_input(request, accept)
    panel.input_presented(request.request_id)
    assert not panel.respond(request.request_id, "approve") and has_pending_input(panel)
    assert panel.respond(request.request_id, "approve") and not has_pending_input(panel)
    assert replies == ["approve", "approve"]
    panel.request_input(request, accept)
    assert not has_pending_input(panel)
    panel.close()
    panel.paint()


def test_approval_requires_its_exact_native_display_and_never_accepts_partial_voice_matches() -> None:
    replies: list[str] = []

    def accept(answer: str) -> bool:
        replies.append(answer)
        return True

    panel = RecordingWindow(lambda: None)
    first = Approval("first", "Write the workbook", "The first preview.", 1.0)
    panel.request_input(first, accept)
    assert panel.reply_by_voice("Approve!") and not replies
    panel.paint()
    assert not panel.respond("first", "approve"), "An enqueued render does not establish visible native content."
    panel.input_presented("stale")
    assert not panel.respond("first", "approve")
    panel.input_presented("first")
    assert panel.reply_by_voice("Do not approve this") and not replies
    assert panel.reply_by_voice("  APPROVE. ") and replies == ["approve"]
    panel.request_input(Approval("second", "Send the draft", "Another complete preview.", 2.0), accept)
    panel.input_presented("first")
    assert not panel.respond("second", "approve")
    assert panel.reply_by_voice("Decline!") and replies == ["approve", "decline"]
    panel.request_input(Approval("third", "Write again", "Fresh preview.", 3.0), accept)
    panel.reset()
    panel.input_presented("third")
    assert not panel.respond("third", "approve")
    panel.request_input(Approval("fourth", "Write again", "Fresh preview.", 4.0), accept)
    assert not panel.respond("fourth", "approve")
    panel.close()
    panel.input_presented("fourth")
    assert not panel.respond("fourth", "approve") and replies == ["approve", "decline"]
    panel.paint()


def test_failed_native_creation_never_authorizes_a_projected_approval() -> None:
    jobs: deque[Callable[[], None]] = deque()
    replies: list[str] = []
    errors: list[str] = []

    def create(_agent: AgentWindow) -> View:
        raise RuntimeError("Fixture native view unavailable")

    def accept(answer: str) -> bool:
        replies.append(answer)
        return True

    panel = AgentWindow(lambda: None, post=jobs.append, create_view=create, on_problem=errors.append)
    panel.request_input(Approval("approval", "Write the workbook", "Complete preview.", 1.0), accept)
    while jobs:
        jobs.popleft()()
    assert errors and not panel.ready.is_set()
    assert not panel.respond("approval", "approve") and not replies
    assert any(isinstance(message, Approval) and pending(message) for message in panel.state.messages)
    panel.close()
    while jobs:
        jobs.popleft()()
