"""Immutable work projections and the thread-safe connection to the work view."""

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, assert_never

from automation.context import ContextRecord
from automation.execution import ApprovalReview, review_matches
from automation.routines import EventTrigger, Every, FileChanged, Once, RoutineSummary, Trigger
from automation.task import (
    CANVAS_EDIT_GUIDANCE,
    Approval,
    ApprovalDecision,
    Blocked,
    CanvasSource,
    Completed,
    Control,
    Failed,
    Interrupted,
    Question,
    RequestSource,
    RoutineSource,
    Running,
    Task,
    TaskStatus,
    TextAnswer,
    Until,
    Waiting,
    identifier,
    phase,
    text,
)

from .agent_window import log_view


@dataclass(frozen=True, slots=True)
class Submit:
    prompt: str


@dataclass(frozen=True, slots=True)
class FollowUp:
    task_id: str
    prompt: str


@dataclass(frozen=True, slots=True)
class Redirect:
    task_id: str
    prompt: str


@dataclass(frozen=True, slots=True)
class ControlTask:
    task_id: str
    control: Control


@dataclass(frozen=True, slots=True)
class AnswerTask:
    task_id: str
    request_id: str
    answer: TextAnswer | ApprovalDecision


@dataclass(frozen=True, slots=True)
class RefreshApproval:
    task_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class CreateRoutine:
    title: str
    instruction: str
    trigger: Trigger


@dataclass(frozen=True, slots=True)
class UpdateRoutine:
    routine_id: str
    revision: int
    title: str
    instruction: str
    trigger: Trigger


@dataclass(frozen=True, slots=True)
class EnableRoutine:
    routine_id: str


@dataclass(frozen=True, slots=True)
class DisableRoutine:
    routine_id: str


@dataclass(frozen=True, slots=True)
class AddPreference:
    name: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class UpdatePreference:
    identity: str
    name: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class AddProjectNote:
    project: str
    text: str
    source: str
    valid_for: int


@dataclass(frozen=True, slots=True)
class UpdateProjectNote:
    identity: str
    project: str
    text: str
    source: str
    valid_for: int


@dataclass(frozen=True, slots=True)
class RemoveContext:
    identity: str


type WorkCommand = (
    Submit | FollowUp | Redirect | ControlTask | AnswerTask | RefreshApproval
    | CreateRoutine | UpdateRoutine | EnableRoutine | DisableRoutine
    | AddPreference | UpdatePreference | AddProjectNote | UpdateProjectNote | RemoveContext
)


@dataclass(frozen=True, slots=True)
class SubmissionProblem:
    task_id: str
    reason: str

    def __post_init__(self) -> None:
        identifier(self.task_id, "submission identity")
        text(self.reason, "submission problem", maximum=8_000)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplyProblem:
    task_id: str
    request_id: str
    receipt_id: str
    reason: str

    def __post_init__(self) -> None:
        identifier(self.task_id, "reply task identity")
        identifier(self.request_id, "reply request identity")
        identifier(self.receipt_id, "reply receipt identity")
        text(self.reason, "reply problem", maximum=8_000)


@dataclass(frozen=True, slots=True)
class WorkSnapshot:
    tasks: tuple[Task, ...] = ()
    routines: tuple[RoutineSummary, ...] = ()
    context: tuple[ContextRecord, ...] = ()
    status: str = ""
    reviews: tuple[ApprovalReview, ...] = ()
    submission_problems: tuple[SubmissionProblem, ...] = ()
    reply_problems: tuple[ReplyProblem, ...] = ()


def when(at: float | None) -> str:
    if at is None:
        return "—"
    try:
        return datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "Outside the local calendar"


def task_label(task: Task) -> str:
    match task.state:
        case Waiting(Question()):
            return "Your reply"
        case Waiting(Approval()):
            return "Approval"
        case Waiting(Blocked()):
            return "Needs attention"
        case _:
            return phase(task.state).capitalize()


def current_review(task: Task, reviews: tuple[ApprovalReview, ...]) -> ApprovalReview | None:
    return next((review for review in reviews if review_matches(task, review)), None)


def task_detail(task: Task, reviews: tuple[ApprovalReview, ...] = ()) -> str:
    match task.source:
        case RequestSource():
            origin = "Your request"
        case RoutineSource(routine_id=identity):
            origin = f"Standing instruction · {identity}"
        case CanvasSource():
            origin = "Canvas · selected sources"
        case _:
            assert_never(task.source)
    sections = [
        task.title,
        f"{task_label(task)} · {origin}\nCreated {when(task.created_at)} · Updated {when(task.updated_at)}",
        f"Instructions\n{task.prompt}",
    ]
    if isinstance(task.source, CanvasSource):
        sections.insert(2, CANVAS_EDIT_GUIDANCE)
    if task.checkpoint is not None:
        sections.append(f"Progress · {when(task.checkpoint.at)}\n{task.checkpoint.summary}")
    match task.state:
        case Completed(result, evidence):
            sections.append(f"Result\n{result}")
            if evidence:
                sections.append("Evidence\n" + "\n".join(f"• {item}" for item in evidence))
        case Failed(reason):
            sections.append(f"Needs attention\n{reason}")
        case Interrupted(_, reason):
            sections.append(f"Recovery\n{reason}\n\nResume checks the interrupted work before continuing.")
        case Waiting(reason):
            match reason:
                case Question(_, question, detail):
                    sections.append(f"Your reply\n{question}" + (f"\n\n{detail}" if detail else ""))
                case Approval():
                    if review := current_review(task, reviews):
                        sections.append(f"Approval\n{review.title}\n\n{review.detail}")
                    else:
                        sections.append("Approval\nRequest a fresh preview of this action before deciding.")
                case Until(at, explanation):
                    sections.append(f"Waiting until {when(at)}\n{explanation}")
                case Blocked(explanation):
                    sections.append(f"Waiting\n{explanation}")
                case _:
                    assert_never(reason)
        case TaskStatus.PAUSED:
            sections.append("Paused\nResume continues this saved task.")
        case TaskStatus.CANCELLED:
            sections.append("Cancelled\nThe task and its saved progress remain available.")
        case TaskStatus.READY | Running():
            pass
        case _:
            assert_never(task.state)
    return "\n\n".join(sections)


def trigger_label(trigger: Trigger) -> str:
    match trigger:
        case EventTrigger.TRANSCRIPT_COMPLETED:
            return "After a transcript finishes"
        case Once(at):
            return f"Once · {when(at)}"
        case Every(seconds=seconds):
            return f"Every {seconds / 60:g} minutes"
        case FileChanged(path):
            return f"File changes · {path}"
        case _:
            assert_never(trigger)


class WorkView(Protocol):
    """Widgets are created and called only through the owning UI thread."""

    def render(self, snapshot: WorkSnapshot) -> None: ...
    def show(self, *, focus: bool = True) -> None: ...
    def close(self) -> None: ...


class WorkWindow:
    """Own immutable view state and enqueue rendering without waiting for it."""

    __slots__ = (
        "_create_view", "_lock", "_pending", "_post", "_presented", "_view", "closed", "closed_ready",
        "on_command", "on_problem", "state",
    )

    def __init__(
        self, on_command: Callable[[WorkCommand], None], *, post: Callable[[Callable[[], None]], None],
        create_view: Callable[[WorkWindow], WorkView], on_problem: Callable[[str], None] | None = None,
    ) -> None:
        self.on_command, self.on_problem = on_command, on_problem
        self._post, self._create_view = post, create_view
        self._lock = threading.Lock()
        self._pending = self.closed = False
        self._view: WorkView | None = None
        self._presented: ApprovalReview | None = None
        self.state = WorkSnapshot()
        self.closed_ready = threading.Event()

    def publish(self, snapshot: WorkSnapshot) -> None:
        with self._lock:
            if self.closed or snapshot == self.state:
                return
            self.state = snapshot
            if self._presented is not None and not any(
                current_review(task, snapshot.reviews) == self._presented for task in snapshot.tasks
            ):
                self._presented = None
            if self._pending or self._view is None:
                return
            self._pending = True
        self._post(self._render)

    def _render(self) -> None:
        with self._lock:
            self._pending = False
            snapshot, view, closed = self.state, self._view, self.closed
        if view is not None and not closed:
            view.render(snapshot)

    def show(self, *, focus: bool = True) -> None:
        def reveal() -> None:
            with self._lock:
                if self.closed:
                    return
                view = self._view
            if view is None:
                try:
                    view = self._create_view(self)
                except Exception:
                    log_view(traceback.format_exc())
                    self.problem("The work overview could not open. Try Work again; details are in agent-ui.log.")
                    return
                if not self._attach(view):
                    view.close()
                    return
            self._render()
            view.show(focus=focus)

        self._post(reveal)

    def _attach(self, view: WorkView) -> bool:
        with self._lock:
            if self.closed:
                return False
            self._view = view
            return True

    def approval_presented(self, task_id: str, request_id: str) -> bool:
        """The native view acknowledges the exact live preview in its viewport."""
        with self._lock:
            if self.closed:
                return False
            task = next((task for task in self.state.tasks if task.id == task_id), None)
            review = current_review(task, self.state.reviews) if task is not None else None
            if review is None or review.request.id != request_id:
                return False
            self._presented = review
            return True

    def problem(self, message: str) -> None:
        with self._lock:
            if self.closed:
                return
            self.state = replace(self.state, status=message)
            schedule = not self._pending and self._view is not None
            if schedule:
                self._pending = True
        if schedule:
            self._post(self._render)
        if self.on_problem is not None:
            self.on_problem(message)

    def send(self, command: WorkCommand) -> bool:
        with self._lock:
            if self.closed:
                return False
            if isinstance(command, AnswerTask):
                task = next((task for task in self.state.tasks if task.id == command.task_id), None)
                if task is None or not isinstance(task.state, Waiting) or not isinstance(task.state.reason, (Question, Approval)):
                    return False
                if task.state.reason.id != command.request_id:
                    return False
                if isinstance(task.state.reason, Question) != isinstance(command.answer, TextAnswer):
                    return False
                if command.answer is ApprovalDecision.APPROVED:
                    review = current_review(task, self.state.reviews)
                    if review is None or review != self._presented:
                        return False
        try:
            self.on_command(command)
        except Exception as error:
            log_view(traceback.format_exc())
            self.problem(str(error) or "The work request could not be submitted.")
            return False
        return True

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            self._presented = None

        def destroy() -> None:
            try:
                with self._lock:
                    view, self._view = self._view, None
                if view is not None:
                    view.close()
            finally:
                self.closed_ready.set()

        self._post(destroy)
