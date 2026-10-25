"""Conversation controls backed by durable work in the supervised process.

UI callbacks enqueue immutable requests. This controller's observer owns all
journal and transport calls; the existing AgentWindow owns UI scheduling.
"""

import os
import queue
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from automation.context import ContextStore
from automation.execution import ApprovalReview, review_matches
from automation.ipc import (
    BrowserAppRequest,
    Envelope,
    LogEvent,
    MeetingCommand,
    MeetingFailed,
    MeetingRequest,
    MeetingSucceeded,
    StartMeeting,
)
from automation.routines import RoutineStore
from automation.store import TaskStore
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
    Reply,
    RequestSource,
    Running,
    RunRef,
    Task,
    TaskStatus,
    TextAnswer,
    Until,
    Waiting,
    WaitReason,
    identifier,
    owns,
    terminal,
    text,
)

from . import agent_session as session
from .activity import ContextSnapshot
from .agent_window import AgentWindow
from .browser_surface_operations import AppFailed, AppOperation, AppResult
from .canvas_model import Request as CanvasRequest
from .canvas_requests import CanvasRequests, captured_request
from .work_service import BrowserAppDelivery, CanvasSubmission, IdentifiedSubmission, Issued, WorkBridge, WorkService
from .work_window import AnswerTask, ControlTask, FollowUp, Redirect, Submit, WorkCommand, WorkSnapshot

type MeetingControl = Callable[[StartMeeting | MeetingCommand], MeetingSucceeded | MeetingFailed]
type BrowserAppControl = Callable[[AppOperation, threading.Event, Callable[[AppResult], None]], None]


class BrowserAppCancellation(threading.Event):
    """Let a deferred Qt action observe its deadline without journal access."""

    __slots__ = ("clock", "expires_at")

    def __init__(self, expires_at: float, clock: Callable[[], float]) -> None:
        super().__init__()
        self.expires_at, self.clock = expires_at, clock

    def is_set(self) -> bool:
        if not super().is_set() and self.clock() >= self.expires_at:
            self.set()
        return super().is_set()

    def wait(self, timeout: float | None = None) -> bool:
        remaining = max(0.0, self.expires_at - self.clock())
        return super().wait(remaining if timeout is None else min(timeout, remaining)) or self.is_set()


@dataclass(frozen=True, slots=True)
class PendingBrowserApp:
    delivery: BrowserAppDelivery
    cancel: threading.Event
    results: queue.Queue[AppResult]


@dataclass(frozen=True, slots=True)
class StopConversation:
    conversation_id: str
    after: int


@dataclass(frozen=True, slots=True)
class Observe:
    completed: threading.Event


type Job = Issued | IdentifiedSubmission | CanvasSubmission | StopConversation | Observe


def in_conversation(task: Task, conversation_id: str | None) -> bool:
    return isinstance(task.source, RequestSource) and task.source.conversation_id == conversation_id


def active(task: Task) -> bool:
    return not terminal(task.state) and task.state is not TaskStatus.PAUSED and not (
        isinstance(task.state, Waiting) and isinstance(task.state.reason, (Blocked, Until))
    )


def chronological(task: Task) -> tuple[float, str]:
    return task.created_at, task.id


def retires_browser_app(command: WorkCommand, task_id: str) -> bool:
    match command:
        case ControlTask(identity, Control.PAUSE | Control.CANCEL) | Redirect(identity):
            return identity == task_id
        case _:
            return False


def input_message(task: Task, review: ApprovalReview | None) -> session.Question | session.Approval | None:
    """A redacted journal record cannot become an approvable conversation row."""
    match task.state:
        case Waiting(Question(identity, question, detail)):
            return session.Question(identity, question, detail, task.updated_at)
        case Waiting(Approval()):
            if review is not None and review_matches(task, review):
                return session.Approval(review.request.id, review.title, review.detail, task.updated_at)
            return None
        case _:
            return None


class WorkAgent:
    __slots__ = (
        "_browser_pending", "_closed", "_closing", "_conversation", "_create_window", "_displayed", "_displayed_review", "_input", "_jobs",
        "_last_stop", "_last_submission_source", "_live", "_lock", "_meeting_results", "_ordinary_pending", "_pending", "_queued", "_rejected_input",
        "_reported_running", "_sequence", "_service", "_settled", "_snapshot", "_thread", "_wake", "_watch_groups", "_watched",
        "browser_app", "clock", "context", "identity", "meeting", "on_done", "on_snapshot", "on_start", "report_error", "window",
    )

    def __init__(
        self, tasks: TaskStore, routines: RoutineStore, saved_context: ContextStore, bridge: WorkBridge,
        create_window: Callable[[WorkAgent], AgentWindow], *, on_snapshot: Callable[[WorkSnapshot], None],
        on_done: Callable[[], None], report_error: Callable[[str], None], on_start: Callable[[], None] | None = None,
        context: Callable[[], ContextSnapshot] | None = None, meeting: MeetingControl | None = None,
        browser_app: BrowserAppControl | None = None,
        canvas_requests: CanvasRequests | None = None,
        clock: Callable[[], float] = time.time, identity: Callable[[], str] = lambda: uuid.uuid4().hex,
        assistant_pid: int | None = None,
    ) -> None:
        self._service = WorkService(tasks, routines, saved_context, bridge,
                                    assistant_pid=os.getpid() if assistant_pid is None else assistant_pid, canvas_requests=canvas_requests)
        self._create_window = create_window
        self.on_snapshot, self.on_done, self.report_error, self.on_start = on_snapshot, on_done, report_error, on_start
        self.context, self.meeting, self.clock, self.identity = context, meeting, clock, identity
        self.browser_app = browser_app
        self._lock = threading.RLock()
        # A Stop marker follows its accepted work in FIFO order. Between two
        # distinct Stop markers there is at least one ordinary command, so
        # 256 ordinary slots require at most 257 reserved control slots.
        self._jobs: queue.Queue[Job] = queue.Queue(maxsize=513)
        self._ordinary_pending = self._sequence = 0
        self._last_stop: StopConversation | None = None
        self._last_submission_source: str | None = None
        self._wake, self._closing, self._closed = threading.Event(), threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._conversation: str | None = None
        self._snapshot = WorkSnapshot()
        self._reported_running = False
        self._pending: dict[str, str | None] = {}
        self._queued: set[str] = set()
        self._settled: dict[str, int] = {}
        self._displayed: Task | None = None
        self._displayed_review: ApprovalReview | None = None
        self._input: tuple[str, str] | None = None
        self._rejected_input: tuple[str, str] | None = None
        self._watch_groups: dict[str, frozenset[str]] = {}
        self._watched: frozenset[str] = frozenset()
        self._live: Callable[[session.LiveMessage], None] | None = None
        self._meeting_results: dict[tuple[str, RunRef, str], MeetingSucceeded | MeetingFailed] = {}
        self._browser_pending: PendingBrowserApp | None = None
        self.window: AgentWindow | None = None

    def start_worker(self) -> None:
        """Begin observation after the application has composed its callbacks."""
        with self._lock:
            if self._thread is None and not self._closing.is_set():
                self._thread = threading.Thread(target=self._observe, name="work frontend", daemon=True)
                self._thread.start()

    def _source(self) -> RequestSource:
        if self._conversation is None:
            self._conversation = self.identity()
        return RequestSource(self._conversation)

    def _enqueue(self, job: Issued | IdentifiedSubmission | CanvasSubmission | Observe) -> bool:
        with self._lock:
            if self._closing.is_set() or self._ordinary_pending >= 256:
                return False
            try:
                self._jobs.put_nowait(job)
            except queue.Full:
                return False
            self._ordinary_pending += 1
            self._sequence += 1
            if isinstance(job, CanvasSubmission):
                self._pending[job.identity] = None
            elif isinstance(job, IdentifiedSubmission) or (isinstance(job, Issued) and isinstance(job.command, (Submit, FollowUp))):
                self._pending[job.identity] = job.source.conversation_id
                self._last_submission_source = job.source.conversation_id
            pending = self._browser_pending
            if (isinstance(job, Issued) and pending is not None and isinstance(pending.delivery.message.payload, BrowserAppRequest)
                    and retires_browser_app(job.command, pending.delivery.message.payload.run.task_id)):
                pending.cancel.set()
            self._wake.set()
        return True

    def command(self, command: WorkCommand) -> None:
        if isinstance(command, AnswerTask):
            identifier(command.task_id, "reply task identity")
            identifier(command.request_id, "reply request identity")
        capture = isinstance(command, Submit)
        if isinstance(command, (FollowUp, Redirect)):
            with self._lock:
                original = next((task for task in self._snapshot.tasks if task.id == command.task_id), None)
            if original is not None and isinstance(original.source, CanvasSource):
                raise ValueError(CANVAS_EDIT_GUIDANCE)
            # An uncached task is checked by the observer without capturing
            # context first; it may be immutable selected-source work.
            capture = original is not None
        captured = self.context() if self.context is not None and capture else None
        with self._lock:
            issued = Issued(command, self.identity(), self._source(), captured)
            if not self._enqueue(issued):
                raise ValueError("Work is closing or its input queue is full. Try the request again.")

    def start(self, text: str) -> None:
        if not (text := text.strip()):
            return
        reveal = not self.running()
        try:
            self.command(Submit(text))
        except ValueError as error:
            self._panel().problem(str(error))
            return
        if reveal:
            self._panel().show()

    def submit_identified(self, identity: str, prompt: str) -> None:
        """Accept a frontend-owned task ID so canvas results can reconnect exactly.

        The observer retains journal and transport ownership, just as it does
        for conversation commands. Returning means queued, not yet committed.
        """
        identity = identifier(identity, "task identity")
        prompt = text(prompt, "task prompt").strip()
        captured = self.context() if self.context is not None else None
        with self._lock:
            if not self._enqueue(IdentifiedSubmission(identity, prompt, self._source(), captured)):
                raise ValueError("Work is closing or its input queue is full. Try the request again.")

    def submit_canvas(self, request: CanvasRequest) -> None:
        """Queue an exact source snapshot without capturing the current desktop or conversation."""
        submission = CanvasSubmission(captured_request(request))
        if not self._enqueue(submission):
            raise ValueError("Work is closing or its input queue is full. Try the request again.")

    def watch(self, task_ids: tuple[str, ...], *, owner: str = "default") -> None:
        """Replace one view's interests while the observer retains their union."""
        owner = identifier(owner, "watch owner")
        watched = frozenset(identifier(identity, "watched task identity") for identity in task_ids)
        if len(watched) > 512:
            raise ValueError("At most 512 saved tasks can be watched at once.")
        with self._lock:
            if self._closing.is_set():
                raise ValueError("Work is closing.")
            groups = self._watch_groups.copy()
            if watched:
                groups[owner] = watched
            else:
                groups.pop(owner, None)
            if len(groups) > 16:
                raise ValueError("At most 16 work views can retain tasks at once.")
            combined = frozenset(identity for group in groups.values() for identity in group)
            if len(combined) > 512:
                raise ValueError("At most 512 saved tasks can be watched across all work views.")
            self._watch_groups, self._watched = groups, combined
            self._wake.set()

    def snapshot(self) -> WorkSnapshot:
        """Read the latest immutable committed projection without journal I/O."""
        with self._lock:
            return self._snapshot

    def voice(self, text: str) -> None:
        if self.window is None or not self.window.reply_by_voice(text):
            self.start(text)

    def running(self) -> bool:
        with self._lock:
            return (self._conversation is not None and self._conversation in self._pending.values()) or any(
                active(task) and in_conversation(task, self._conversation) for task in self._snapshot.tasks
            )

    def _panel(self) -> AgentWindow:
        with self._lock:
            if self.window is None:
                self.window = self._create_window(self)
            return self.window

    def prepare(self) -> None:
        self._panel()

    def show(self) -> None:
        self._panel().show(focus=True)

    def refresh(self) -> threading.Event:
        """Observe accepted commands and current journals without blocking the UI."""
        completed = threading.Event()
        if not self._enqueue(Observe(completed)):
            completed.set()
        return completed

    def stop(self) -> None:
        with self._lock:
            if self._closing.is_set() or self._conversation is None:
                return
            if self._last_submission_source != self._conversation and not any(
                in_conversation(task, self._conversation) and not terminal(task.state) for task in self._snapshot.tasks
            ):
                return
            stop = StopConversation(self._conversation, self._sequence)
            if stop == self._last_stop:
                return
            self._jobs.put_nowait(stop)
            self._last_stop = stop
            pending = self._browser_pending
            if (pending is not None and isinstance(pending.delivery.source, RequestSource)
                    and pending.delivery.source.conversation_id == self._conversation):
                pending.cancel.set()
            self._wake.set()
            if self.window is not None:
                self.window.stopping()

    def new_conversation(self) -> bool:
        with self._lock:
            if self._closing.is_set():
                return False
            was_running, self._reported_running = self._reported_running, False
            self._conversation = self.identity()
            self._displayed = self._displayed_review = self._input = self._live = None
            self._rejected_input = None
            self._settled.clear()
            self._queued.clear()
            if self.window is not None:
                self.window.reset()
            self._wake.set()
        if was_running:
            self.on_done()
        return True

    def close(self) -> None:
        """Drain accepted mutations and disconnect; user cancellation is separate."""
        with self._lock:
            if self._closing.is_set():
                return
            self._closing.set()
            self._cancel_browser_app()
            self._wake.set()
            if self.window is not None:
                self.window.close()
            if self._thread is None:
                # Commands may have arrived during composition, before startup.
                self._thread = threading.Thread(target=self._observe, name="work frontend shutdown", daemon=True)
                self._thread.start()

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def _reply(self, task_id: str, request_id: str, reply: Reply) -> bool:
        with self._lock:
            task = next((task for task in self._snapshot.tasks if task.id == task_id), None)
            if self._input != (task_id, request_id) or task is None or not isinstance(task.state, Waiting):
                return False
            reason = task.state.reason
            if not isinstance(reason, (Question, Approval)) or reason.id != request_id:
                return False
            if isinstance(reason, Question) != isinstance(reply, TextAnswer):
                return False
            if reply is ApprovalDecision.APPROVED and not any(review_matches(task, review) for review in self._snapshot.reviews):
                return False
            return self._enqueue(Issued(AnswerTask(task_id, request_id, reply), self.identity(), self._source(), None))

    def _request(self, task: Task, message: session.Question | session.Approval) -> None:
        def respond(answer: str) -> bool:
            if isinstance(message, session.Question):
                try:
                    return self._reply(task.id, message.request_id, TextAnswer(answer))
                except ValueError:
                    return False
            match answer.strip().casefold():
                case "approve":
                    return self._reply(task.id, message.request_id, ApprovalDecision.APPROVED)
                case "decline":
                    return self._reply(task.id, message.request_id, ApprovalDecision.DECLINED)
                case _:
                    return False

        self._clear_input((task.id, message.request_id))
        self._input = task.id, message.request_id
        self._panel().request_input(message, respond)

    def _clear_input(self, keep: tuple[str, str] | None = None) -> None:
        if self._input is not None and self._input != keep:
            if self.window is not None:
                self.window.clear_external_input(self._input[1])
            self._input = None

    def _display(self, task: Task, review: ApprovalReview | None) -> None:
        previous = self._displayed
        if task == previous and review == self._displayed_review:
            return
        window = self._panel()
        if previous is None or previous.id != task.id:
            self._clear_input()
            window.begin(task.prompt, self.stop, queued=task.id in self._queued)
            self._queued.discard(task.id)
            self._live = window.updates()
        elif (
            task.goal_revision != previous.goal_revision
            or (isinstance(previous.state, Waiting) and not isinstance(task.state, Waiting))
            or (isinstance(previous.state, (Running, Interrupted)) and isinstance(task.state, Running) and previous.state.run != task.state.run)
        ):
            self._clear_input()
            if active(task):
                window.finish()
                window.begin(f"Continue: {task.title}", self.stop)
                self._live = window.updates()
        self._displayed, self._displayed_review = task, review
        if task.checkpoint is not None and (previous is None or previous.checkpoint != task.checkpoint):
            window.step(task.checkpoint.summary)
        self._display_status(task, review)

    def _display_status(self, task: Task, review: ApprovalReview | None) -> None:
        window = self._panel()
        if not (isinstance(task.state, Waiting) and isinstance(task.state.reason, (Question, Approval))):
            self._clear_input()
        match task.state:
            case TaskStatus.READY:
                window.step("Queued for the work process.")
            case Running():
                pass
            case Interrupted(_, reason):
                window.step(f"Recovering interrupted work: {reason}")
            case Waiting(reason):
                self._display_waiting(task, reason, review)
            case Completed(result, _):
                window.done(result)
            case Failed(reason):
                window.problem(reason)
            case TaskStatus.CANCELLED:
                window.done("Stopped.")
            case TaskStatus.PAUSED:
                window.say("Paused. Resume this task from Work.")
            case _:
                assert_never(task.state)
        if isinstance(task.state, (Waiting, Completed, Failed)) or task.state in (TaskStatus.PAUSED, TaskStatus.CANCELLED):
            window.finish()
            self._queued.clear()

    def _display_waiting(self, task: Task, reason: WaitReason, review: ApprovalReview | None) -> None:
        match reason:
            case Question() | Approval():
                message = input_message(task, review)
                if message is not None:
                    self._request(task, message)
                else:
                    self._clear_input()
                    self._panel().say("An approval needs a fresh preview. Open Work to request it.")
            case Blocked(explanation):
                self._panel().problem(explanation)
            case Until(_, explanation):
                self._panel().say(f"Waiting: {explanation}")
            case _:
                assert_never(reason)

    def _publish(self, snapshot: WorkSnapshot) -> None:
        with self._lock:
            if self._closing.is_set():
                return
            was_running = self._reported_running
            self._snapshot = snapshot
            self._restore_reply(snapshot)
            if self._conversation is None:
                self._conversation = next((task.source.conversation_id for task in snapshot.tasks
                                           if isinstance(task.source, RequestSource) and not terminal(task.state)), None)
            tasks = sorted((task for task in snapshot.tasks if in_conversation(task, self._conversation)), key=chronological)
            task_ids = {task.id for task in tasks}
            self._settled = {identity: revision for identity, revision in self._settled.items() if identity in task_ids}
            if self._displayed is not None and self._displayed.id not in task_ids:
                self._displayed = self._displayed_review = self._live = None
                self._clear_input()
                if self.window is not None:
                    self.window.finish()
                self._queued.clear()
            selected = next((task for task in tasks if self._displayed is not None and task.id == self._displayed.id), None)
            candidates = [task for task in tasks if self._settled.get(task.id) != task.revision]
            while selected is not None or candidates:
                selected = selected if selected is not None else candidates[0]
                review = next((review for review in snapshot.reviews if review_matches(selected, review)), None)
                self._display(selected, review)
                if active(selected):
                    break
                self._settled[selected.id] = selected.revision
                candidates = [task for task in candidates if task.id != selected.id]
                selected = None
                self._displayed = None
            for task in tasks:
                if (
                    active(task) and (self._displayed is None or task.id != self._displayed.id)
                    and task.id not in self._queued and len(self._queued) < 100
                ):
                    self._panel().queued(task.prompt)
                    self._queued.add(task.id)
            is_running = self._reported_running = self.running()
        self.on_snapshot(snapshot)
        if not was_running and is_running and self.on_start is not None:
            self.on_start()
        elif was_running and not is_running:
            self.on_done()

    def _restore_reply(self, snapshot: WorkSnapshot) -> None:
        if self._rejected_input is None:
            return
        task_id, request_id = self._rejected_input
        task = next((task for task in snapshot.tasks if task.id == task_id), None)
        self._rejected_input = None
        if self._input != (task_id, request_id) or self.window is None:
            return
        if (
            task is not None and isinstance(task.state, Waiting) and isinstance(task.state.reason, (Question, Approval))
            and task.state.reason.id == request_id
        ):
            review = next((review for review in snapshot.reviews if review_matches(task, review)), None)
            self.window.clear_external_input(request_id)
            message = input_message(task, review)
            if message is not None:
                self.window.problem("That reply could not be saved. Please answer the current request again.")
                self._request(task, message)

    def _observed_ids(self) -> tuple[frozenset[str], frozenset[str]]:
        with self._lock:
            watched = self._watched
            if self._input is not None:
                watched = watched | {self._input[0]}
            return watched, frozenset(self._pending)

    def _publish_unavailable(self) -> None:
        watched, pending = self._observed_ids()
        with self._lock:
            if self._closing.is_set():
                return
            snapshot = self._service.unavailable(self._snapshot, watched, pending)
            self._snapshot = snapshot
        self.on_snapshot(snapshot)

    def _log(self, event: LogEvent) -> None:
        task = self._service.tasks.get(event.run.task_id)
        with self._lock:
            if task is None or not owns(task, event.run) or not in_conversation(task, self._conversation):
                return
            if self._displayed is None or self._displayed.id != task.id or self.window is None or self._closing.is_set():
                return
            match event.event:
                case session.Begin():
                    pass  # The persisted task already introduced this turn.
                case session.Progress(text):
                    self.window.step(text)
                case session.Said(text, _):
                    self.window.say(text)
                case session.Done():
                    pass  # A durable outcome, not a late live frame, completes work.
                case session.Problem(text, _):
                    self.window.problem(text)
                case session.Finished():
                    pass
                case session.Answer() | session.ReasoningSummary() | session.ToolActivity():
                    if self._live is not None:
                        self._live(event.event)
                case _:
                    assert_never(event.event)

    def _meeting(self, message: Envelope, request: MeetingRequest) -> None:
        task = self._service.tasks.get(request.run.task_id)
        if task is None or not owns(task, request.run) or self._closing.is_set():
            return
        key = message.epoch, request.run, request.request_id
        result = self._meeting_results.get(key)
        if result is None:
            try:
                result = self.meeting(request.operation) if self.meeting is not None else MeetingFailed("Meeting controls are unavailable.")
            except Exception:
                self.report_error(traceback.format_exc())
                result = MeetingFailed("The meeting operation failed; see the assistant log.")
            if len(self._meeting_results) >= 128:
                self._meeting_results.pop(next(iter(self._meeting_results)))
            self._meeting_results[key] = result
        try:
            if not self._service.bridge.respond(message, result):
                self._service.problem = "The meeting request expired before its result was delivered. Check Recordings for its current state."
        except (OSError, ValueError):
            self._service.problem = "The work process disconnected before confirming the meeting result. Check Recordings for its current state."

    def _cancel_browser_app(self) -> None:
        with self._lock:
            if self._browser_pending is not None:
                self._browser_pending.cancel.set()
                self._browser_pending = None

    def _browser_apps(self) -> None:
        delivery = self._service.browser_app_pending(at=self.clock())
        with self._lock:
            pending = self._browser_pending
            if pending is not None and pending.delivery != delivery:
                self._cancel_browser_app()
                pending = None
            # Commands accepted during transport must commit before a new
            # page action can start. This lock also makes later controls see
            # the new pending token and cancel it before queued Qt work.
            if delivery is None or self._closing.is_set() or not self._jobs.empty():
                return
            dispatch = pending is None
            if pending is None:
                request = delivery.message.payload
                assert isinstance(request, BrowserAppRequest)
                pending = PendingBrowserApp(delivery, BrowserAppCancellation(request.expires_at, self.clock), queue.Queue(maxsize=1))
                self._browser_pending = pending
        if dispatch:
            request = delivery.message.payload
            assert isinstance(request, BrowserAppRequest)
            results, wake = pending.results, self._wake

            def complete(result: AppResult) -> None:
                # Qt only deposits a value. Expiry, ownership, and all journal
                # and transport work stay on this observer.
                try:
                    results.put_nowait(result)
                except queue.Full:
                    return
                wake.set()

            try:
                if self.browser_app is None:
                    complete(AppFailed("Browser app controls are unavailable."))
                else:
                    self.browser_app(request.operation, pending.cancel, complete)
            except Exception:
                self.report_error(traceback.format_exc())
                complete(AppFailed("The browser app operation failed; see the assistant log."))
        try:
            result = pending.results.get_nowait()
        except queue.Empty:
            return
        self._cancel_browser_app()
        if not self._closing.is_set():
            self._service.complete_browser_app(delivery.message, result, at=self.clock())

    def _jobs_ready(self) -> tuple[Observe, ...]:
        observed: list[Observe] = []
        for _ in range(64):
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            if not isinstance(job, StopConversation):
                with self._lock:
                    self._ordinary_pending -= 1
            try:
                match job:
                    case Issued() | IdentifiedSubmission() | CanvasSubmission():
                        self._service.apply(job, at=self.clock())
                    case StopConversation(conversation_id):
                        self._service.stop_conversation(conversation_id, at=self.clock())
                    case Observe():
                        observed.append(job)
                    case _:
                        assert_never(job)
            except Exception as error:
                self._service.problem = str(error) or "The work request could not be saved."
                if isinstance(job, (Issued, IdentifiedSubmission, CanvasSubmission)):
                    watched, _ = self._observed_ids()
                    self._service.submission_failed(job, self._service.problem, watched=watched)
                    if isinstance(job, Issued) and isinstance(job.command, AnswerTask):
                        self._service.reply_failed(job, self._service.problem, watched=watched)
                        with self._lock:
                            if self._input == (job.command.task_id, job.command.request_id):
                                self._rejected_input = self._input
                self.report_error(traceback.format_exc())
            finally:
                if isinstance(job, (Issued, IdentifiedSubmission, CanvasSubmission)):
                    with self._lock:
                        self._pending.pop(job.identity, None)
                elif isinstance(job, StopConversation):
                    with self._lock:
                        if self._last_stop == job:
                            self._last_stop = None
        return tuple(observed)

    def _observe(self) -> None:
        previous_context: ContextSnapshot | None = None
        try:
            while True:
                observed = self._jobs_ready()
                try:
                    if not self._closing.is_set():
                        current_context = self.context() if self.context is not None else None
                        if current_context is not None and current_context != previous_context:
                            self._service.publish_context(current_context)
                            previous_context = current_context
                        events = self._service.events(at=self.clock())
                        self._browser_apps()
                        watched, pending = self._observed_ids()
                        self._publish(self._service.snapshot(at=self.clock(), watched=watched, pending=pending))
                        for event in events:
                            match event.payload:
                                case LogEvent() as log:
                                    self._log(log)
                                case MeetingRequest() as request:
                                    self._meeting(event, request)
                                case _:
                                    raise ValueError("Unexpected frontend event.")
                except Exception as error:
                    self._service.invalidate_browser_app()
                    self._cancel_browser_app()
                    self._service.problem = str(error) or "Work could not be refreshed."
                    self._publish_unavailable()
                    self.report_error(traceback.format_exc())
                finally:
                    for observation in observed:
                        observation.completed.set()
                if self._closing.is_set() and self._jobs.empty():
                    break
                if self._jobs.empty():
                    self._wake.wait(0.25)
                    self._wake.clear()
        finally:
            self._cancel_browser_app()
            try:
                self._service.bridge.close()
            finally:
                self._closed.set()
