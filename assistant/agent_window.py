"""The agent session's thread-safe connection to the shared assistant UI thread.

Only immutable snapshots cross into the view. Model work and waiting for user
input stay on worker threads; notifications and history stay on the UI thread.
"""

import contextlib
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from usage import DIRECTORY

from .agent_input import Inputs, acceptable, resolution
from .agent_session import (
    Approval,
    Begin,
    Done,
    Enqueued,
    Event,
    Finished,
    InputCancelled,
    LiveMessage,
    Problem,
    Progress,
    Question,
    RecordingUpdated,
    Said,
    Session,
    Snapshot,
    StopRequested,
    pending,
    transition,
)
from .recording_state import RecordingAction, RecordingState

VIEW_LOG: Final = DIRECTORY / "agent-ui.log"


def log_view(text: str) -> None:
    with contextlib.suppress(OSError):
        VIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with VIEW_LOG.open("a", encoding="utf-8") as output:
            output.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


class View(Protocol):
    """Used only on the UI thread."""

    def render(self, state: Snapshot) -> None: ...
    def show(self, *, focus: bool = False) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _ExternalRequest:
    message: Question | Approval
    respond: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class _ExternalAnswer:
    request_id: str


class AgentWindow:
    __slots__ = (
        "_create_view", "_external", "_failed", "_pending", "_post", "_presented", "_view", "closed", "closed_ready", "inputs", "lock", "on_problem",
        "on_recording", "on_reset", "on_submit", "on_waiting", "on_work", "ready", "started", "state", "stop",
    )

    def __init__(
        self, stop: Callable[[], None], *, post: Callable[[Callable[[], None]], None], create_view: Callable[[AgentWindow], View],
        on_problem: Callable[[str], None] | None = None, on_reset: Callable[[], bool] | None = None,
        on_submit: Callable[[str], None] | None = None, on_waiting: Callable[[bool], None] | None = None,
        on_recording: Callable[[RecordingAction], None] | None = None,
        on_work: Callable[[], None] | None = None,
    ) -> None:
        self.stop, self.on_problem, self.on_reset, self.on_submit = stop, on_problem, on_reset, on_submit
        self.on_waiting = on_waiting
        self.on_recording = on_recording
        self.on_work = on_work
        self._external: _ExternalRequest | _ExternalAnswer | None = None
        self._presented: str | None = None
        self.state = Session(identity=uuid.uuid4().hex)
        self.inputs = Inputs(self._event)
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.closed_ready = threading.Event()
        self.started = time.monotonic()
        self.closed = self._failed = self._pending = False
        self._post, self._create_view = post, create_view
        self._view: View | None = None
        self._post(self._create)

    @property
    def waiting(self) -> bool:
        with self.lock:
            return self.state.waiting

    def _create(self) -> None:
        if self.closed or self._view is not None:
            return
        try:
            self._view = self._create_view(self)
            self._failed = False
            self._render()
            self.ready.set()
            log_view(f"Agent conversation view ready in {time.monotonic() - self.started:.2f}s.")
        except Exception:
            self._failed = True
            if self._view is not None:
                with contextlib.suppress(Exception):
                    self._view.close()
                self._view = None
            self.inputs.cancel_all()
            log_view(traceback.format_exc())
            self.problem("The agent panel couldn't open. Reopen Agent session to retry; details are in agent-ui.log.")

    def _render(self) -> None:
        with self.lock:
            self._pending = False
            state = self.state
        if not self.closed and self._view is not None:
            self._view.render(state)

    def _changed(self) -> None:
        if not self._pending:
            self._pending = True
            self._post(self._render)

    def _event(self, event: Event) -> None:
        with self.lock:
            if self.closed:
                self.inputs.cancel_all()
                return
            was_waiting = self.state.waiting
            self.state = transition(self.state, event)
            waiting = self.state.waiting
            self._changed()
        if was_waiting != waiting and self.on_waiting is not None:
            self.on_waiting(waiting)

    def show(self, *, focus: bool = False) -> None:
        def reveal() -> None:
            if not self.closed:
                self._create()
                if self._view is not None:
                    self._view.show(focus=focus)

        self._post(reveal)

    def submit(self, text: str) -> bool:
        if not (text := text.strip()):
            return False
        with self.lock:
            if self.closed or self.state.stopping:
                return False
            question = self.state.question
        if question is not None:
            return self.respond(question.request_id, text)
        if self.on_submit is None:
            return False
        self.on_submit(text)
        return True

    def reply_by_voice(self, text: str) -> bool:
        """Consume a spoken reply to the pending request, or leave it as a task.

        Approvals accept only the complete utterance "approve" or "decline".
        Unrecognized or racing replies stay out of the task queue.
        """
        with self.lock:
            if self.closed or self.state.stopping:
                return True
            request = next((message for message in self.state.messages if isinstance(message, (Question, Approval)) and pending(message)), None)
        match request:
            case None:
                return False
            case Question(request_id=identity):
                self.respond(identity, text.strip())
            case Approval(request_id=identity):
                answer = text.strip().casefold().rstrip(".!?").strip()
                if answer in ("approve", "decline"):
                    self.respond(identity, answer)
                else:
                    self.say("Approval is still waiting. Hold N and say just “approve” or “decline”.")
        return True

    def request_input(self, message: Question | Approval, respond: Callable[[str], bool]) -> None:
        """Project an exact durable request without starting a waiting thread."""
        with self.lock:
            if self.closed:
                return
            old = self._external
            if isinstance(old, _ExternalAnswer) and old.request_id == message.request_id:
                return
            if isinstance(old, _ExternalRequest) and old.message.request_id == message.request_id:
                self._external = _ExternalRequest(old.message, respond)
                return
            self._external = _ExternalRequest(message, respond)
            self._presented = None
            was_waiting = self.state.waiting
            if old is not None:
                old_id = old.message.request_id if isinstance(old, _ExternalRequest) else old.request_id
                self.state = transition(self.state, InputCancelled(old_id))
            self.state = transition(self.state, message)
            waiting = self.state.waiting
            self._changed()
        if was_waiting != waiting and self.on_waiting is not None:
            self.on_waiting(waiting)

    def clear_external_input(self, request_id: str) -> None:
        """Remove only this view's matching request; durable state stays owned by work."""
        with self.lock:
            request = self._external
            identity = request.message.request_id if isinstance(request, _ExternalRequest) else request.request_id if request is not None else None
            if identity != request_id:
                return
            self._external = None
            self._presented = None
            was_waiting = self.state.waiting
            self.state = transition(self.state, InputCancelled(request_id))
            waiting = self.state.waiting
            self._changed()
        if was_waiting != waiting and self.on_waiting is not None:
            self.on_waiting(waiting)

    def input_presented(self, request_id: str) -> None:
        """A native view acknowledges that this exact approval preview is visible."""
        with self.lock:
            request = self._external
            if isinstance(request, _ExternalRequest) and isinstance(request.message, Approval) and request.message.request_id == request_id:
                self._presented = request_id

    def respond(self, identity: str, answer: str) -> bool:
        """Route a reply to an exact external request or the existing local broker."""
        submitted = _ExternalAnswer(identity)
        with self.lock:
            if self.closed:
                return False
            request = self._external
            presented = self._presented == identity
            if isinstance(request, _ExternalRequest) and request.message.request_id == identity:
                if not acceptable(request.message, answer):
                    return False
                if isinstance(request.message, Approval) and answer == "approve" and not presented:
                    self.say("Open the current action preview before approving it.")
                    return False
                self._external = submitted
                self._presented = None
        if not isinstance(request, _ExternalRequest) or request.message.request_id != identity:
            return self.inputs.respond(identity, answer)
        accepted = False
        try:
            accepted = request.respond(answer)
        finally:
            if not accepted:
                with self.lock:
                    if self._external is submitted:
                        self._external = request
                        self._presented = identity if presented else None
        if not accepted:
            return False
        with self.lock:
            if self._external is not submitted:
                return True
            was_waiting = self.state.waiting
            self.state = transition(self.state, resolution(request.message, answer))
            waiting = self.state.waiting
            self._changed()
        if was_waiting != waiting and self.on_waiting is not None:
            self.on_waiting(waiting)
        return True

    def show_work(self) -> None:
        if self.on_work is not None:
            self.on_work()

    def new(self) -> bool:
        with self.lock:
            if self.closed or self.state.busy:
                return False
        return self.on_reset() if self.on_reset is not None else False

    def begin(self, task: str, stop: Callable[[], None], *, queued: bool = False) -> None:
        self.stop = stop
        self._event(Begin(task, time.time(), queued))

    def queued(self, task: str) -> None:
        self._event(Enqueued(task))

    def step(self, text: str) -> None:
        self._event(Progress(text))

    def say(self, text: str) -> None:
        self._event(Said(text, time.time()))

    def updates(self) -> Callable[[LiveMessage], None]:
        """Bind a producer to this request, so a late stream cannot enter another turn."""
        with self.lock:
            identity, generation = self.state.identity, self.state.generation

        def publish(message: LiveMessage) -> None:
            with self.lock:
                if self.state.identity == identity and self.state.generation == generation:
                    self._event(message)

        return publish

    def problem(self, text: str) -> None:
        self._event(Problem(text, time.time()))
        if self.on_problem is not None:
            self.on_problem(text)

    def done(self, text: str) -> None:
        self._event(Done(text, time.time()))

    def finish(self) -> None:
        self._event(Finished())

    def reset(self) -> None:
        with self.lock:
            self._external = None
            self._presented = None
            recording = self.state.recording
            self.state = Session(identity=uuid.uuid4().hex)
            if recording.phase not in ("idle", "saved", "interrupted"):
                self.state = transition(self.state, RecordingUpdated(recording, time.time()))
            self._changed()

    def recording(self, state: RecordingState) -> None:
        self._event(RecordingUpdated(state, time.time()))

    def stopping(self) -> None:
        with self.lock:
            request = self._external
        if isinstance(request, _ExternalRequest):
            self.clear_external_input(request.message.request_id)
        self.inputs.cancel_all()
        self._event(StopRequested())

    def input(self, title: str, detail: str, *, question: bool, cancel: threading.Event | None) -> str | None:
        if self.closed or self._failed:
            return None
        return self.inputs.ask(title, detail, question=question, cancel=cancel)

    def all_text(self) -> str:
        with self.lock:
            return self.state.all_text()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self._external = None
            self._presented = None
        self.ready.clear()
        self.inputs.cancel_all()

        def destroy() -> None:
            try:
                if self._view is not None:
                    self._view.close()
                    self._view = None
            finally:
                self.closed_ready.set()

        self._post(destroy)
