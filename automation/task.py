"""Immutable work values and pure decisions; storage and execution own effects."""

import math
import re
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Literal, assert_never, cast

CANVAS_EDIT_GUIDANCE = "Use the canvas composer or Refresh result to change this request."


def text(value: object, field: str, *, maximum: int = 64_000, empty: bool = False) -> str:
    """Validate text received from storage or a typed external interface."""
    if not isinstance(value, str) or "\0" in value or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f"Invalid {field}.")
    return value


def identifier(value: object, field: str = "identity") -> str:
    return text(value, field, maximum=128)


def integer(value: object, field: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {field}.")
    return value


def timestamp(value: object, field: str = "timestamp") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {field}.")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"Invalid {field}.") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Invalid {field}.")
    return number


def digest(value: object, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Invalid {field}.")
    return value


def variant(value: object, expected: type[object] | tuple[type[object], ...], field: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"Invalid {field}.")


def strings(value: object, field: str, *, maximum: int, count: int) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"Invalid {field}.")
    items = cast(tuple[object, ...], value)
    if len(items) > count:
        raise ValueError(f"Invalid {field}.")
    for item in items:
        text(item, field, maximum=maximum)


@dataclass(frozen=True, slots=True)
class RequestSource:
    conversation_id: str

    def __post_init__(self) -> None:
        identifier(self.conversation_id, "conversation identity")


@dataclass(frozen=True, slots=True)
class RoutineSource:
    routine_id: str
    revision: int
    occurrence_id: str

    def __post_init__(self) -> None:
        identifier(self.routine_id, "routine identity")
        integer(self.revision, "routine revision")
        text(self.occurrence_id, "routine occurrence", maximum=512)


@dataclass(frozen=True, slots=True)
class CanvasSource:
    fingerprint: str

    def __post_init__(self) -> None:
        digest(self.fingerprint, "canvas preparation fingerprint")


type Source = RequestSource | RoutineSource | CanvasSource


@dataclass(frozen=True, slots=True)
class RunRef:
    task_id: str
    run_id: str

    def __post_init__(self) -> None:
        identifier(self.task_id, "task identity")
        identifier(self.run_id, "run identity")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    summary: str
    at: float

    def __post_init__(self) -> None:
        text(self.summary, "checkpoint summary", maximum=16_000)
        timestamp(self.at)


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str
    detail: str

    def __post_init__(self) -> None:
        identifier(self.id, "question identity")
        text(self.text, "question", maximum=8_000)
        text(self.detail, "question detail", maximum=16_000, empty=True)


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    title: str
    detail: str

    def __post_init__(self) -> None:
        identifier(self.id, "approval identity")
        text(self.title, "approval title", maximum=240)
        text(self.detail, "approval detail", maximum=16_000, empty=True)


@dataclass(frozen=True, slots=True)
class Until:
    at: float
    reason: str

    def __post_init__(self) -> None:
        timestamp(self.at, "wake time")
        text(self.reason, "waiting reason", maximum=8_000)


@dataclass(frozen=True, slots=True)
class Blocked:
    reason: str

    def __post_init__(self) -> None:
        text(self.reason, "blocking reason", maximum=8_000)


type WaitReason = Question | Approval | Until | Blocked


@dataclass(frozen=True, slots=True)
class TextAnswer:
    text: str

    def __post_init__(self) -> None:
        text(self.text, "answer", maximum=16_000)


class ApprovalDecision(Enum):
    APPROVED = auto()
    DECLINED = auto()


type Reply = TextAnswer | ApprovalDecision


@dataclass(frozen=True, slots=True)
class Response:
    request: Question | Approval
    reply: Reply
    at: float

    def __post_init__(self) -> None:
        timestamp(self.at)
        match self.request, self.reply:
            case Question(), TextAnswer():
                pass
            case Approval(), ApprovalDecision():
                pass
            case _:
                raise ValueError("The reply does not match the input request.")


class TaskStatus(Enum):
    READY = auto()
    PAUSED = auto()
    CANCELLED = auto()


class Control(Enum):
    PAUSE = auto()
    RESUME = auto()
    CANCEL = auto()


@dataclass(frozen=True, slots=True)
class Running:
    run: RunRef
    owner: str
    started_at: float

    def __post_init__(self) -> None:
        variant(self.run, RunRef, "run identity")
        identifier(self.owner, "worker owner")
        timestamp(self.started_at, "run start")


@dataclass(frozen=True, slots=True)
class Waiting:
    reason: WaitReason

    def __post_init__(self) -> None:
        variant(self.reason, (Question, Approval, Until, Blocked), "waiting reason")


@dataclass(frozen=True, slots=True)
class Interrupted:
    run: RunRef
    reason: str

    def __post_init__(self) -> None:
        variant(self.run, RunRef, "run identity")
        text(self.reason, "interruption reason", maximum=8_000)


@dataclass(frozen=True, slots=True)
class Completed:
    result: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        text(self.result, "task result")
        strings(self.evidence, "completion evidence", maximum=8_000, count=64)


@dataclass(frozen=True, slots=True)
class Failed:
    reason: str

    def __post_init__(self) -> None:
        text(self.reason, "failure reason", maximum=8_000)


type State = TaskStatus | Running | Waiting | Interrupted | Completed | Failed
type Outcome = Completed | Failed | Waiting
type Phase = Literal["ready", "paused", "cancelled", "running", "waiting", "interrupted", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    title: str
    prompt: str
    source: Source
    priority: int
    created_at: float
    updated_at: float
    revision: int
    state: State
    checkpoint: Checkpoint | None
    goal_revision: int = 0

    def __post_init__(self) -> None:
        identifier(self.id, "task identity")
        text(self.title, "task title", maximum=240)
        text(self.prompt, "task prompt")
        integer(self.priority, "task priority", minimum=-10_000, maximum=10_000)
        timestamp(self.created_at, "creation time")
        timestamp(self.updated_at, "update time")
        integer(self.revision, "task revision")
        integer(self.goal_revision, "goal revision")
        if self.updated_at < self.created_at or self.goal_revision > self.revision:
            raise ValueError("Invalid task chronology.")
        variant(self.source, (RequestSource, RoutineSource, CanvasSource), "task source")
        variant(self.state, (TaskStatus, Running, Waiting, Interrupted, Completed, Failed), "task state")
        if isinstance(self.state, (Running, Interrupted)) and self.state.run.task_id != self.id:
            raise ValueError("The run belongs to another task.")
        if self.checkpoint is not None:
            variant(self.checkpoint, Checkpoint, "task checkpoint")
            if self.checkpoint.at > self.updated_at:
                raise ValueError("The checkpoint is newer than the task update.")


@dataclass(frozen=True, slots=True)
class Claim:
    task: Task
    run: RunRef
    previous: TaskStatus | Interrupted

    def __post_init__(self) -> None:
        if not isinstance(self.task.state, Running) or self.task.state.run != self.run:
            raise ValueError("The claim does not identify the task's running execution.")
        if self.previous is not TaskStatus.READY and not isinstance(self.previous, Interrupted):
            raise ValueError("The claim did not start from runnable work.")


@dataclass(frozen=True, slots=True)
class GoalRevision:
    revision: int
    prompt: str
    at: float

    def __post_init__(self) -> None:
        integer(self.revision, "goal revision")
        text(self.prompt, "goal prompt")
        timestamp(self.at)


class NoVerification(Enum):
    NONE = auto()


@dataclass(frozen=True, slots=True)
class FileDigest:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        text(self.path, "verification path", maximum=32_768)
        digest(self.sha256, "file digest")


type Verification = NoVerification | FileDigest


@dataclass(frozen=True, slots=True)
class ActionSpec:
    tool: str
    label: str
    fingerprint: str
    resources: tuple[str, ...] = ()
    verification: Verification = NoVerification.NONE

    def __post_init__(self) -> None:
        text(self.tool, "action tool", maximum=128)
        text(self.label, "action label", maximum=1_024)
        digest(self.fingerprint)
        strings(self.resources, "action resource", maximum=1_024, count=32)
        variant(self.verification, (NoVerification, FileDigest), "action verification")


class ActionPending(Enum):
    RUNNING = auto()


@dataclass(frozen=True, slots=True)
class ActionReturned:
    summary: str

    def __post_init__(self) -> None:
        text(self.summary, "action summary", maximum=8_000)


@dataclass(frozen=True, slots=True)
class ActionVerified:
    evidence: str

    def __post_init__(self) -> None:
        text(self.evidence, "action evidence", maximum=8_000)


@dataclass(frozen=True, slots=True)
class ActionNotApplied:
    reason: str

    def __post_init__(self) -> None:
        text(self.reason, "action rejection", maximum=8_000)


@dataclass(frozen=True, slots=True)
class ActionUncertain:
    reason: str

    def __post_init__(self) -> None:
        text(self.reason, "action uncertainty", maximum=8_000)


type ActionOutcome = ActionPending | ActionReturned | ActionVerified | ActionNotApplied | ActionUncertain


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    task_id: str
    run_id: str
    specification: ActionSpec
    started_at: float
    updated_at: float
    outcome: ActionOutcome

    def __post_init__(self) -> None:
        identifier(self.id, "action identity")
        identifier(self.task_id, "task identity")
        identifier(self.run_id, "run identity")
        timestamp(self.started_at, "action start")
        timestamp(self.updated_at, "action update")
        if self.updated_at < self.started_at:
            raise ValueError("Invalid action chronology.")
        variant(self.specification, ActionSpec, "action specification")
        variant(self.outcome, (ActionPending, ActionReturned, ActionVerified, ActionNotApplied, ActionUncertain), "action outcome")


def phase(state: State) -> Phase:
    match state:
        case TaskStatus.READY:
            return "ready"
        case TaskStatus.PAUSED:
            return "paused"
        case TaskStatus.CANCELLED:
            return "cancelled"
        case Running():
            return "running"
        case Waiting():
            return "waiting"
        case Interrupted():
            return "interrupted"
        case Completed():
            return "completed"
        case Failed():
            return "failed"
        case _:
            assert_never(state)


def terminal(state: State) -> bool:
    return state is TaskStatus.CANCELLED or isinstance(state, (Completed, Failed))


def owns(task: Task, run: RunRef) -> bool:
    return isinstance(task.state, Running) and task.state.run == run


def changed(task: Task, state: State, *, at: float) -> Task:
    timestamp(at)
    return replace(task, state=state, updated_at=max(task.updated_at, at), revision=task.revision + 1)


def started(task: Task, run: RunRef, owner: str, *, at: float) -> Task | None:
    if task.state is not TaskStatus.READY and not isinstance(task.state, Interrupted):
        return None
    return changed(task, Running(run, owner, at), at=at)


def progressed(task: Task, run: RunRef, summary: str, *, at: float) -> Task | None:
    if not owns(task, run):
        return None
    return replace(changed(task, task.state, at=at), checkpoint=Checkpoint(summary, at))


def finished(task: Task, run: RunRef, outcome: Outcome, *, at: float) -> Task | None:
    return changed(task, outcome, at=at) if owns(task, run) else None


def interrupted(task: Task, run: RunRef, reason: str, *, at: float) -> Task | None:
    return changed(task, Interrupted(run, reason), at=at) if owns(task, run) else None


def controlled(task: Task, command: Control, *, at: float) -> Task | None:
    if command not in allowed_controls(task.state):
        return None
    match command:
        case Control.CANCEL:
            return changed(task, TaskStatus.CANCELLED, at=at)
        case Control.PAUSE:
            return changed(task, TaskStatus.PAUSED, at=at)
        case Control.RESUME:
            return changed(task, TaskStatus.READY, at=at)
        case _:
            assert_never(command)


def allowed_controls(state: State) -> tuple[Control, ...]:
    match state:
        case TaskStatus.READY | Running():
            return Control.PAUSE, Control.CANCEL
        case TaskStatus.PAUSED:
            return Control.RESUME, Control.CANCEL
        case Interrupted():
            return Control.RESUME, Control.PAUSE, Control.CANCEL
        case Waiting(reason):
            match reason:
                case Until() | Blocked():
                    return Control.RESUME, Control.PAUSE, Control.CANCEL
                case Question() | Approval():
                    return (Control.CANCEL,)
                case _:
                    assert_never(reason)
        case Failed():
            return (Control.RESUME,)
        case TaskStatus.CANCELLED | Completed():
            return ()
        case _:
            assert_never(state)


def redirected(task: Task, prompt: str, *, at: float) -> Task:
    if isinstance(task.source, CanvasSource):
        raise ValueError(CANVAS_EDIT_GUIDANCE)
    return replace(changed(task, TaskStatus.READY, at=at), prompt=prompt, goal_revision=task.goal_revision + 1)


def answered(task: Task, request_id: str, reply: Reply, *, at: float) -> tuple[Task, Response] | None:
    match task.state:
        case Waiting(reason=Question(id=identity) as request) if identity == request_id and isinstance(reply, TextAnswer):
            response = Response(request, reply, at)
        case Waiting(reason=Approval(id=identity) as request) if identity == request_id and isinstance(reply, ApprovalDecision):
            response = Response(request, reply, at)
        case _:
            return None
    return changed(task, TaskStatus.READY, at=at), response


def approval_refreshed(task: Task, request_id: str, *, at: float) -> Task | None:
    match task.state:
        case Waiting(reason=Approval(id=identity)) if identity == request_id:
            return changed(task, TaskStatus.READY, at=at)
        case _:
            return None


def unresolved(outcome: ActionOutcome) -> bool:
    return outcome is ActionPending.RUNNING or isinstance(outcome, ActionUncertain)


def action_finished(action: Action, outcome: ActionOutcome, *, at: float) -> Action | None:
    """A verified fact cannot be replaced by a late, weaker observation."""
    timestamp(at)
    if at < action.updated_at or outcome is ActionPending.RUNNING or outcome == action.outcome:
        return None
    match action.outcome:
        case ActionPending.RUNNING | ActionUncertain():
            return replace(action, outcome=outcome, updated_at=max(action.updated_at, at))
        case ActionReturned():
            if isinstance(outcome, (ActionVerified, ActionUncertain)):
                return replace(action, outcome=outcome, updated_at=max(action.updated_at, at))
            return None
        case ActionVerified() | ActionNotApplied():
            return None
        case _:
            assert_never(action.outcome)
