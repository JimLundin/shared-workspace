"""Journal and volatile transport effects, owned by the frontend's observer."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, assert_never

from automation.context import ContextStore, Preference, ProjectNote
from automation.execution import ApprovalReview, review_matches
from automation.ipc import (
    BrowserAppCancelled,
    BrowserAppRequest,
    Envelope,
    LogEvent,
    MeetingFailed,
    MeetingRequest,
    MeetingSucceeded,
    Resync,
    ReviewEvent,
)
from automation.routines import RoutineStore
from automation.store import TaskStore
from automation.task import (
    CANVAS_EDIT_GUIDANCE,
    Approval,
    ApprovalDecision,
    CanvasSource,
    Completed,
    Control,
    Question,
    RequestSource,
    RoutineSource,
    Running,
    RunRef,
    Task,
    Waiting,
    owns,
    text,
)

from .activity import ContextSnapshot
from .browser_surface_operations import AppFailed, AppResult
from .canvas_model import Request as CanvasRequest
from .canvas_requests import CanvasRequests, PreparedCanvas, captured_request, context_prompt
from .work_window import (
    AddPreference,
    AddProjectNote,
    AnswerTask,
    ControlTask,
    CreateRoutine,
    DisableRoutine,
    EnableRoutine,
    FollowUp,
    Redirect,
    RefreshApproval,
    RemoveContext,
    ReplyProblem,
    SubmissionProblem,
    Submit,
    UpdatePreference,
    UpdateProjectNote,
    UpdateRoutine,
    WorkCommand,
    WorkSnapshot,
)


class WorkBridge(Protocol):
    """The UI process uses bounded, authenticated, non-persistent exchanges."""

    def connect(self) -> str: ...
    def poll(self, limit: int = 64) -> tuple[Envelope, ...]: ...
    def wake(self) -> None: ...
    def register(self, task_id: str, context: ContextSnapshot | None, *, assistant_pid: int) -> None: ...
    def publish_context(self, context: ContextSnapshot, *, assistant_pid: int) -> None: ...
    def respond(self, message: Envelope, result: MeetingSucceeded | MeetingFailed) -> bool: ...
    def respond_browser_app(self, message: Envelope, result: AppResult) -> bool: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Issued:
    command: WorkCommand
    identity: str
    source: RequestSource
    context: ContextSnapshot | None


@dataclass(frozen=True, slots=True)
class IdentifiedSubmission:
    identity: str
    prompt: str
    source: RequestSource
    context: ContextSnapshot | None


@dataclass(frozen=True, slots=True)
class CanvasSubmission:
    request: CanvasRequest

    def __post_init__(self) -> None:
        if captured_request(self.request) != self.request:
            raise ValueError("A queued canvas request must contain captured source observations.")
        context_prompt(self.request)

    @property
    def identity(self) -> str:
        return self.request.id


type WorkRequest = Issued | IdentifiedSubmission | CanvasSubmission


@dataclass(frozen=True, slots=True)
class UnconfirmedSubmission:
    request: WorkRequest
    problem: SubmissionProblem


type TaskCommand = Submit | FollowUp | Redirect | ControlTask | AnswerTask | RefreshApproval
type RoutineCommand = CreateRoutine | UpdateRoutine | EnableRoutine | DisableRoutine
type ContextCommand = AddPreference | UpdatePreference | AddProjectNote | UpdateProjectNote | RemoveContext


def submission_matches(task: Task, request: WorkRequest, *, prepared: PreparedCanvas | None = None) -> bool:
    match request:
        case CanvasSubmission(canvas):
            return (prepared is not None and prepared.request == canvas and task.id == prepared.task_id
                    and task.source == prepared.source and task.prompt == prepared.prompt)
        case IdentifiedSubmission(identity, prompt, _, _):
            return task.id == identity and task.prompt == prompt.strip() and isinstance(task.source, RequestSource)
        case Issued(Submit(prompt), identity, source, _):
            return task.id == identity and task.prompt == prompt.strip() and task.source == source
        case Issued():
            return False
        case _:
            assert_never(request)


def follow_up_prompt(task: Task, prompt: str) -> str:
    """Keep the referenced outcome useful even after volatile history is lost."""
    text(prompt, "follow-up request")
    previous = task.state.result if isinstance(task.state, Completed) else task.checkpoint.summary if task.checkpoint else task.prompt
    return text(f"Follow-up to {task.title} (task {task.id}).\nPrevious work:\n{previous[:8_000]}\n\nNew request:\n{prompt}", "task prompt")


def browser_app_source(task: Task | None, request: BrowserAppRequest, *, at: float) -> RequestSource | RoutineSource | None:
    if task is None or not owns(task, request.run) or at >= request.expires_at:
        return None
    match task.source:
        case RequestSource() | RoutineSource() as source:
            return source
        case CanvasSource():
            return None
        case _:
            assert_never(task.source)


@dataclass(frozen=True, slots=True)
class BrowserAppDelivery:
    message: Envelope
    source: RequestSource | RoutineSource


@dataclass(frozen=True, slots=True)
class BrowserAppHistory:
    run: RunRef
    requests: frozenset[str]


def remember_browser_app(history: BrowserAppHistory | None, run: RunRef, request_id: str) -> BrowserAppHistory | None:
    if history is None or history.run != run:
        return BrowserAppHistory(run, frozenset({request_id}))
    # The journal permits only one running claim. Keep that run's request IDs
    # across reconnects; refusing a full history is safer than replaying an
    # evicted identity. Retired runs can never pass the ownership check.
    if request_id in history.requests or len(history.requests) >= 512:
        return None
    return replace(history, requests=history.requests | {request_id})


class WorkService:
    __slots__ = (
        "_browser_history", "_browser_pending", "_epoch", "_pending_inputs", "_replies", "_reviews", "_sequence", "_submissions",
        "assistant_pid", "bridge", "canvas_requests",
        "connection_status", "context", "problem", "routines", "tasks",
    )

    def __init__(
        self, tasks: TaskStore, routines: RoutineStore, context: ContextStore, bridge: WorkBridge, *, assistant_pid: int,
        canvas_requests: CanvasRequests | None = None,
    ) -> None:
        self.tasks, self.routines, self.context, self.bridge = tasks, routines, context, bridge
        self.canvas_requests = canvas_requests if canvas_requests is not None else CanvasRequests(tasks.path.parent / "canvas-requests")
        self.assistant_pid = assistant_pid
        self._epoch: str | None = None
        self._browser_pending: BrowserAppDelivery | None = None
        self._browser_history: BrowserAppHistory | None = None
        self._sequence = 0
        self._reviews: dict[str, ReviewEvent] = {}
        self._submissions: dict[str, UnconfirmedSubmission] = {}
        self._replies: dict[tuple[str, str], ReplyProblem] = {}
        self._pending_inputs: frozenset[tuple[str, str]] = frozenset()
        self.problem = self.connection_status = ""

    def _transport(self, operation: Callable[[], None]) -> None:
        try:
            operation()
        except (OSError, ValueError):
            self._disconnected()

    def _disconnected(self) -> None:
        self._epoch = None
        self.invalidate_browser_app()
        self._reviews.clear()
        self.connection_status = "Work is saved. Waiting for the work process to reconnect."

    def register(self, identity: str, context: ContextSnapshot | None) -> None:
        # BridgeClient retains registration before attempting I/O. Registration
        # precedes eligibility in SQLite, so an online worker can claim safely.
        self._transport(lambda: self.bridge.register(identity, context, assistant_pid=self.assistant_pid))

    def publish_context(self, context: ContextSnapshot) -> None:
        self._transport(lambda: self.bridge.publish_context(context, assistant_pid=self.assistant_pid))

    def _submit(self, identity: str, prompt: str, source: RequestSource | CanvasSource, context: ContextSnapshot | None, *, at: float) -> Task:
        prompt = text(prompt, "task prompt").strip()
        existing = self.tasks.get(identity)
        if existing is not None:
            if existing.source != source or existing.prompt != prompt:
                raise ValueError("That task identity already belongs to a different request.")
            self._submissions.pop(identity, None)
            return existing
        self.register(identity, context)
        task = self.tasks.submit(identity, prompt.splitlines()[0][:240], prompt, source, at=at)
        if task.source != source or task.prompt != prompt:
            raise ValueError("That task identity already belongs to a different request.")
        self._submissions.pop(identity, None)
        return task

    def _limit_submissions(self, watched: frozenset[str]) -> None:
        # Retained canvas collisions must not disappear merely because other
        # requests failed. Keep their receipts plus a bounded recent history.
        unwatched = tuple(identity for identity in self._submissions if identity not in watched)
        for identity in unwatched[:max(0, len(unwatched) - 256)]:
            self._submissions.pop(identity)

    def submission_failed(self, issued: WorkRequest, reason: str, *, watched: frozenset[str]) -> None:
        if isinstance(issued, Issued) and not isinstance(issued.command, Submit):
            return
        problem = SubmissionProblem(issued.identity, f"Could not confirm this submission: {reason[:4_000]}".replace("\0", "�"))
        self._submissions.pop(issued.identity, None)
        saved = issued if isinstance(issued, CanvasSubmission) else replace(issued, context=None)
        self._submissions[issued.identity] = UnconfirmedSubmission(saved, problem)
        self._limit_submissions(watched)

    def _limit_replies(self, watched: frozenset[str]) -> None:
        recent = tuple(key for key in self._replies if key[0] not in watched or key not in self._pending_inputs)
        for key in recent[:max(0, len(recent) - 256)]:
            self._replies.pop(key)

    def reply_failed(self, issued: Issued, reason: str, *, watched: frozenset[str]) -> None:
        if not isinstance(issued.command, AnswerTask):
            return
        command = issued.command
        key = command.task_id, command.request_id
        problem = ReplyProblem(
            task_id=command.task_id, request_id=command.request_id, receipt_id=issued.identity,
            reason=f"This reply was not saved: {reason[:4_000]}".replace("\0", "�"),
        )
        self._replies.pop(key, None)
        self._replies[key] = problem
        self._limit_replies(watched)

    def apply(self, request: WorkRequest, *, at: float) -> None:
        """Commit a user command before notifying the work process."""
        if isinstance(request, CanvasSubmission):
            prepared = self.canvas_requests.prepare(request.request, at=at)
            self._submit(prepared.task_id, prepared.prompt, prepared.source, None, at=at)
            self.problem = ""
            self._transport(self.bridge.wake)
            return
        if isinstance(request, IdentifiedSubmission):
            existing = self.tasks.get(request.identity)
            source = request.source
            if existing is not None:
                if not submission_matches(existing, request) or not isinstance(existing.source, RequestSource):
                    raise ValueError("That task identity already belongs to a different request.")
                source = existing.source
            issued = Issued(Submit(request.prompt), request.identity, source, request.context)
        else:
            issued = request
        command = issued.command
        match command:
            case Submit() | FollowUp() | Redirect() | ControlTask() | AnswerTask() | RefreshApproval():
                self._task_command(issued, command, at=at)
            case CreateRoutine() | UpdateRoutine() | EnableRoutine() | DisableRoutine():
                self._routine_command(issued.identity, command, at=at)
            case AddPreference() | UpdatePreference() | AddProjectNote() | UpdateProjectNote() | RemoveContext():
                self._context_command(issued.identity, command, at=at)
            case _:
                assert_never(command)
        self.problem = ""
        self._transport(self.bridge.wake)

    def _task_command(self, issued: Issued, command: TaskCommand, *, at: float) -> None:
        match command:
            case Submit(prompt):
                self._submit(issued.identity, prompt, issued.source, issued.context, at=at)
            case FollowUp(task_id, prompt):
                original = self.tasks.get(task_id)
                if original is None:
                    raise ValueError("The task for this follow-up no longer exists.")
                if isinstance(original.source, CanvasSource):
                    raise ValueError(CANVAS_EDIT_GUIDANCE)
                source = original.source if isinstance(original.source, RequestSource) else issued.source
                self._submit(issued.identity, follow_up_prompt(original, prompt), source, issued.context, at=at)
            case Redirect(task_id, prompt):
                original = self.tasks.get(task_id)
                if original is None:
                    raise ValueError("The task to redirect no longer exists.")
                if isinstance(original.source, CanvasSource):
                    raise ValueError(CANVAS_EDIT_GUIDANCE)
                self.register(task_id, issued.context)
                if self.tasks.revise(task_id, prompt, at=at) is None:
                    raise ValueError("The task to redirect no longer exists.")
                self._reviews.pop(task_id, None)
            case ControlTask(task_id, control):
                if self.tasks.control(task_id, control, at=at) is None:
                    raise ValueError("That task control is no longer available.")
                self._reviews.pop(task_id, None)
            case AnswerTask(task_id, request_id, answer):
                if answer is ApprovalDecision.APPROVED:
                    # A previously painted preview becomes invalid as soon as
                    # the worker epoch changes, even before the next UI poll.
                    try:
                        if self.bridge.connect() != self._epoch:
                            self._disconnected()
                    except (OSError, ValueError):
                        self._disconnected()
                task = self.tasks.get(task_id)
                review = self._reviews.get(task_id)
                if answer is ApprovalDecision.APPROVED and (task is None or review is None or not review_matches(task, review.review)):
                    raise ValueError("Request a fresh preview before approving this operation.")
                if self.tasks.answer(task_id, request_id, answer, at=at) is None:
                    raise ValueError("This question or approval is no longer waiting for that reply.")
                self._replies.pop((task_id, request_id), None)
                self._reviews.pop(task_id, None)
            case RefreshApproval(task_id, request_id):
                if self.tasks.refresh_approval(task_id, request_id, at=at) is None:
                    raise ValueError("That approval has changed. Read its current request before refreshing.")
                self._reviews.pop(task_id, None)
            case _:
                assert_never(command)

    def _routine_command(self, identity: str, command: RoutineCommand, *, at: float) -> None:
        match command:
            case CreateRoutine(title, instruction, trigger):
                self.routines.create(identity, title, instruction, trigger, at=at)
            case UpdateRoutine(identity, revision, title, instruction, trigger):
                self.routines.update(identity, title, instruction, trigger, at=at, expected_revision=revision)
            case EnableRoutine(identity):
                self.routines.enable(identity, at=at)
            case DisableRoutine(identity):
                self.routines.disable(identity, at=at)
            case _:
                assert_never(command)

    def _context_command(self, identity: str, command: ContextCommand, *, at: float) -> None:
        match command:
            case AddPreference(name, value, source):
                self.context.save(Preference(identity, name, value, source, at))
            case UpdatePreference(identity, name, value, source):
                if not isinstance(self.context.get(identity), Preference):
                    raise ValueError("The preference being edited no longer exists.")
                self.context.save(Preference(identity, name, value, source, at))
            case AddProjectNote(project, content, source, valid_for):
                self.context.save(ProjectNote(identity, project, content, source, at, at + valid_for))
            case UpdateProjectNote(identity, project, content, source, valid_for):
                if not isinstance(self.context.get(identity), ProjectNote):
                    raise ValueError("The project note being edited no longer exists.")
                self.context.save(ProjectNote(identity, project, content, source, at, at + valid_for))
            case RemoveContext(identity):
                self.context.remove(identity)
            case _:
                assert_never(command)

    def stop_conversation(self, conversation_id: str, *, at: float) -> None:
        self.tasks.control_conversation(conversation_id, Control.CANCEL, at=at)
        self._transport(self.bridge.wake)

    def invalidate_browser_app(self) -> None:
        self._browser_pending = None

    def browser_app_pending(self, *, at: float) -> BrowserAppDelivery | None:
        pending = self._browser_pending
        if pending is not None:
            message = pending.message
            request = message.payload
            if (not isinstance(request, BrowserAppRequest) or message.epoch != self._epoch
                    or browser_app_source(self.tasks.get(request.run.task_id), request, at=at) != pending.source):
                self.invalidate_browser_app()
        return self._browser_pending

    def complete_browser_app(self, message: Envelope, result: AppResult, *, at: float) -> None:
        pending = self.browser_app_pending(at=at)
        if pending is None or pending.message != message:
            return
        # Delivery is attempted once. A missing acknowledgment never authorizes
        # another page effect or a replay of its callback.
        self.invalidate_browser_app()
        self._respond_browser_app(message, result)

    def _respond_browser_app(self, message: Envelope, result: AppResult) -> None:
        try:
            if self.bridge.connect() != message.epoch:
                self._disconnected()
                return
            if not self.bridge.respond_browser_app(message, result):
                self.problem = "The browser app result was not confirmed. Inspect the app before trying again."
        except (OSError, ValueError):
            self._disconnected()
            self.problem = "The work process disconnected before confirming the browser app result. Inspect the app before trying again."

    def events(self, *, at: float) -> tuple[Envelope, ...]:
        try:
            epoch = self.bridge.connect()
            if epoch != self._epoch:
                self._epoch, self._sequence = epoch, 0
                self.invalidate_browser_app()
                self._reviews.clear()
            messages = self.bridge.poll()
        except (OSError, ValueError):
            self._disconnected()
            return ()
        self.connection_status = ""
        accepted: list[Envelope] = []
        for message in messages:
            if message.epoch != self._epoch or message.sequence <= self._sequence:
                continue
            self._sequence = message.sequence
            match message.payload:
                case Resync():
                    self.invalidate_browser_app()
                    self._reviews.clear()
                case BrowserAppRequest() as request:
                    task = self.tasks.get(request.run.task_id)
                    if task is not None and owns(task, request.run):
                        history = remember_browser_app(self._browser_history, request.run, request.request_id)
                        if history is not None:
                            self._browser_history = history
                            source = browser_app_source(task, request, at=at)
                            if source is not None:
                                if self.browser_app_pending(at=at) is None:
                                    self._browser_pending = BrowserAppDelivery(message, source)
                                else:
                                    self._respond_browser_app(message, AppFailed(
                                        "Another browser app request is still pending. Wait for its result before trying again.",
                                    ))
                case BrowserAppCancelled(run, request_id):
                    task = self.tasks.get(run.task_id)
                    if task is not None and owns(task, run):
                        history = remember_browser_app(self._browser_history, run, request_id)
                        if history is not None:
                            self._browser_history = history
                    pending = None if self._browser_pending is None else self._browser_pending.message
                    if (pending is not None and isinstance(pending.payload, BrowserAppRequest)
                            and pending.epoch == message.epoch and pending.payload.run == run and pending.payload.request_id == request_id):
                        self.invalidate_browser_app()
                case ReviewEvent(run, review):
                    task = self.tasks.get(run.task_id)
                    if task is not None and task.id == review.task_id and task.goal_revision == review.goal_revision and (
                        owns(task, run) or (review_matches(task, review) and self.tasks.request_run(task.id, review.request.id) == run)
                    ):
                        if len(self._reviews) >= 1_000:
                            self._reviews.pop(next(iter(self._reviews)))
                        self._reviews[task.id] = message.payload
                case LogEvent() | MeetingRequest():
                    accepted.append(message)
                case _:
                    assert_never(message.payload)
        return tuple(accepted)

    def _submission_problems(
        self, current: dict[str, Task], watched: frozenset[str], pending: frozenset[str], *, confirmed: bool,
    ) -> tuple[SubmissionProblem, ...]:
        for identity, submission in tuple(self._submissions.items()):
            task = current.get(identity)
            if confirmed and task is not None:
                prepared = self.canvas_requests.get(identity) if isinstance(submission.request, CanvasSubmission) else None
                if submission_matches(task, submission.request, prepared=prepared):
                    self._submissions.pop(identity)
        problems = {identity: submission.problem for identity, submission in self._submissions.items()}
        missing_reason = (
            "No saved task was found for this request. Submit it again if you want to retry."
            if confirmed else "Could not confirm this request while the work journal is unavailable."
        )
        for identity in sorted(watched - current.keys() - pending):
            if identity not in problems:
                problems[identity] = SubmissionProblem(identity, missing_reason)
        return tuple(problems.values())

    def unavailable(self, previous: WorkSnapshot, watched: frozenset[str], pending: frozenset[str]) -> WorkSnapshot:
        """Keep known state; failed reads cannot prove that a commit did not happen."""
        self._reviews.clear()
        self._limit_submissions(watched)
        self._limit_replies(watched)
        current = {task.id: task for task in previous.tasks}
        return replace(previous, status=self.problem or "The work journal is unavailable.", reviews=(),
                       submission_problems=self._submission_problems(current, watched, pending, confirmed=False), reply_problems=())

    def _confirmed_reply_problems(self, current: dict[str, Task], watched: frozenset[str]) -> tuple[ReplyProblem, ...]:
        self._pending_inputs = frozenset(
            (task.id, task.state.reason.id) for task in current.values()
            if isinstance(task.state, Waiting) and isinstance(task.state.reason, (Question, Approval))
        )
        for key in tuple(self._replies):
            if (key[0] in current or key[0] in watched) and key not in self._pending_inputs:
                self._replies.pop(key)
        self._limit_replies(watched)
        return tuple(problem for key, problem in self._replies.items() if key in self._pending_inputs)

    def snapshot(self, *, at: float, watched: frozenset[str] = frozenset(), pending: frozenset[str] = frozenset()) -> WorkSnapshot:
        self._limit_submissions(watched)
        tasks = self.tasks.list_tasks(limit=1_000)
        current = {task.id: task for task in tasks}
        # Watched rows and unconfirmed writes share one bounded read snapshot.
        for retained in self.tasks.get_many(tuple(sorted(watched | self._submissions.keys()))):
            current[retained.id] = retained
        tasks = tuple(current.values())
        reviews: list[ApprovalReview] = []
        for identity, event in tuple(self._reviews.items()):
            task = current.get(identity)
            if task is not None and review_matches(task, event.review):
                reviews.append(event.review)
            elif task is None or not isinstance(task.state, Running) or not owns(task, event.run) or task.goal_revision != event.review.goal_revision:
                self._reviews.pop(identity)
        routines, context = self.routines.summaries(at), self.context.list()
        problems = self._submission_problems(current, watched, pending, confirmed=True)
        reply_problems = self._confirmed_reply_problems(current, watched)
        return WorkSnapshot(tasks, routines, context, self.problem or self.connection_status, tuple(reviews), problems, reply_problems)
