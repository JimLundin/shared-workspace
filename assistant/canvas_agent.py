"""Map durable tasks to canvas results. Task identities survive UI restarts."""

from collections.abc import Callable
from typing import assert_never

from automation import task as work
from automation.execution import ApprovalReview, review_matches

from . import canvas_model as m
from .agent_session import Session
from .canvas_requests import context_prompt, matches_task
from .work_window import AnswerTask, ControlTask, WorkCommand, WorkSnapshot

__all__ = ("WorkBackend", "context_prompt")


class WorkBackend:
    __slots__ = (
        "_answered", "_command", "_last", "_presented", "_problems", "_reply_receipts", "_requests", "_session", "_snapshot",
        "_submit", "_watch", "publish",
    )

    def __init__(
        self, submit: Callable[[m.Request], None], command: Callable[[WorkCommand], None],
        snapshot: Callable[[], WorkSnapshot], session: Callable[[], Session | None], *, watch: Callable[[tuple[str, ...]], None] = lambda _: None,
    ) -> None:
        self._submit, self._command, self._snapshot, self._session = submit, command, snapshot, session
        self._watch = watch
        self._requests: dict[str, m.Request] = {}
        self._last: dict[str, tuple[work.Task, str]] = {}
        self._answered: set[tuple[str, str]] = set()
        self._presented: dict[str, ApprovalReview] = {}
        self._problems: dict[str, str] = {}
        self._reply_receipts: dict[tuple[str, str], str] = {}
        self.publish: Callable[[m.Event], None] = lambda _: None

    def connect(self, publish: Callable[[m.Event], None]) -> None:
        self.publish = publish

    def work_snapshot(self) -> WorkSnapshot:
        """Read the work owner's cached immutable value without queueing work."""
        return self._snapshot()

    def attach(self, request: m.Request) -> None:
        if request.id in self._requests and self._requests[request.id] != request:
            raise ValueError("This request identity already belongs to different canvas work.")
        self._requests[request.id] = request
        self._watch(tuple(self._requests))

    def submit(self, request: m.Request) -> None:
        context_prompt(request)
        fresh = request.id not in self._requests
        try:
            self.attach(request)
            self._submit(request)
        except (OSError, ValueError):
            if fresh:
                self._forget(request.id)
            raise
        self._last.pop(request.id, None)
        self._problems.pop(request.id, None)

    def cancel(self, request_id: str) -> None:
        if self._confirmed_task(request_id, self._snapshot()) is None:
            raise ValueError("Reconnect this request before stopping it; its saved task could not be confirmed.")
        self._command(ControlTask(request_id, work.Control.CANCEL))
        self._forget(request_id)

    def _forget(self, request_id: str) -> None:
        self._requests.pop(request_id, None)
        self._last.pop(request_id, None)
        self._presented.pop(request_id, None)
        self._problems.pop(request_id, None)
        self._answered = {key for key in self._answered if key[0] != request_id}
        self._reply_receipts = {key: receipt for key, receipt in self._reply_receipts.items() if key[0] != request_id}
        self._watch(tuple(self._requests))

    def _confirmed_task(self, request_id: str, snapshot: WorkSnapshot) -> work.Task | None:
        request = self._requests.get(request_id)
        if request is None or any(problem.task_id == request_id for problem in snapshot.submission_problems):
            return None
        task = next((task for task in snapshot.tasks if task.id == request_id), None)
        return task if task is not None and matches_task(request, task) else None

    def answer(self, request_id: str, question_id: str, text: str) -> bool:
        snapshot = self._snapshot()
        task = self._confirmed_task(request_id, snapshot)
        key = request_id, question_id
        if task is None or request_id not in self._requests or key in self._answered or not isinstance(task.state, work.Waiting):
            return False
        reason = task.state.reason
        if not isinstance(reason, (work.Question, work.Approval)) or reason.id != question_id:
            return False
        answer: work.TextAnswer | work.ApprovalDecision
        if isinstance(reason, work.Question):
            answer = work.TextAnswer(text)
        elif text in ("approve", "decline"):
            review = next((review for review in snapshot.reviews if review_matches(task, review)), None)
            if text == "approve" and (review is None or self._presented.get(request_id) != review):
                return False
            answer = work.ApprovalDecision.APPROVED if text == "approve" else work.ApprovalDecision.DECLINED
        else:
            return False
        self._command(AnswerTask(request_id, question_id, answer))
        self._answered.add(key)
        for problem in snapshot.reply_problems:
            if (problem.task_id, problem.request_id) == key:
                self._reply_receipts[key] = problem.receipt_id
        return True

    def presented(self, request_id: str, preview: m.Approval) -> bool:
        snapshot = self._snapshot()
        task = self._confirmed_task(request_id, snapshot)
        if task is None or request_id not in self._requests:
            return False
        review = next((review for review in snapshot.reviews if review_matches(task, review)), None)
        if review is None or (preview.id, preview.title, preview.detail) != (review.request.id, review.title, review.detail):
            return False
        self._presented[request_id] = review
        return True

    def _reply_failures(self, snapshot: WorkSnapshot) -> dict[str, str]:
        reply_notices: dict[str, str] = {}
        for failure in snapshot.reply_problems:
            key = failure.task_id, failure.request_id
            current = next((task for task in snapshot.tasks if task.id == failure.task_id), None)
            if current is None or not isinstance(current.state, work.Waiting) or not isinstance(current.state.reason, (work.Question, work.Approval)):
                continue
            if current.state.reason.id != failure.request_id:
                continue
            if failure.task_id in self._requests and failure.task_id not in self._problems and self._reply_receipts.get(key) != failure.receipt_id:
                self._reply_receipts[key] = failure.receipt_id
                self._answered.discard(key)
                self._last.pop(failure.task_id, None)
                self._presented.pop(failure.task_id, None)
                reply_notices[failure.task_id] = ("Reply could not be saved · " + failure.reason)[:240]
        return reply_notices

    def poll(self) -> Session | None:
        snapshot = self._snapshot()
        problems = {problem.task_id: problem.reason for problem in snapshot.submission_problems if problem.task_id in self._requests}
        for task in snapshot.tasks:
            request = self._requests.get(task.id)
            if request is not None and not matches_task(request, task):
                problems.setdefault(task.id, "This saved task could not be confirmed as the canvas request. "
                                    "Its original request and source snapshots have been kept.")
        for identity, problem in problems.items():
            if self._problems.get(identity) != problem:
                self.publish(m.NeedsAttention(identity, problem))
            self._last.pop(identity, None)
            self._presented.pop(identity, None)
        self._problems = problems
        reply_notices = self._reply_failures(snapshot)
        for task in snapshot.tasks:
            if task.id not in self._requests or task.id in problems:
                continue
            review = next((review for review in snapshot.reviews if review_matches(task, review)), None)
            if review != self._presented.get(task.id):
                self._presented.pop(task.id, None)
            review_id = repr(review)
            if self._last.get(task.id) == (task, review_id):
                continue
            self._last[task.id] = task, review_id
            steps = (task.checkpoint.summary[:4_000],) if task.checkpoint is not None else ()
            match task.state:
                case work.Completed(text, _):
                    self.publish(m.Progress(task.id, "", steps))
                    self.publish(m.Completed(task.id, text, task.title))
                    self._forget(task.id)
                case work.Failed(reason):
                    self.publish(m.Failed(task.id, reason))
                    self._forget(task.id)
                case work.TaskStatus.CANCELLED:
                    self.publish(m.Stopped(task.id))
                    self._forget(task.id)
                case work.Waiting(reason):
                    self._waiting(task.id, reason, review, steps, reply_notices.get(task.id, ""))
                case work.TaskStatus.PAUSED:
                    self.publish(m.Progress(task.id, "", steps, "Paused · open Work to resume"))
                case work.TaskStatus.READY:
                    self.publish(m.Progress(task.id, "", steps, "Queued for the work process"))
                case work.Interrupted():
                    self.publish(m.Progress(task.id, "", steps, "Recovering saved work…"))
                case work.Running():
                    self.publish(m.Progress(task.id, "", steps, "Working…"))
                case _:
                    assert_never(task.state)
        return self._session()

    def _waiting(self, task_id: str, reason: work.WaitReason, review: ApprovalReview | None, steps: tuple[str, ...], problem: str) -> None:
        match reason:
            case work.Question(identity, title, detail):
                if (task_id, identity) not in self._answered:
                    self.publish(m.NeedsInput(task_id, m.Question(identity, title, detail), problem))
            case work.Approval(id=identity):
                if review is not None and (task_id, identity) not in self._answered:
                    self.publish(m.NeedsInput(task_id, m.Approval(identity, review.title, review.detail), problem))
                elif review is None:
                    self.publish(m.Progress(task_id, "", steps, "Approval needs a fresh preview · request it in Work"))
            case work.Blocked(explanation) | work.Until(_, explanation):
                self.publish(m.Progress(task_id, "", steps, explanation[:240]))
            case _:
                assert_never(reason)
