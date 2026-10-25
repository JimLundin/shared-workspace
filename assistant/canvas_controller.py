"""UI-thread canvas ownership, asynchronous publication and coalesced persistence."""

import contextlib
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from . import canvas_model as m
from .canvas_store import CanvasStore


class Backend(Protocol):
    """Submit exact observations; publish events with the same request identity.

    Implementations must return promptly. Publish may be called from any thread.
    A successful answer consumes only the specified pending question.
    """

    def connect(self, publish: Callable[[m.Event], None]) -> None: ...
    def submit(self, request: m.Request) -> None: ...
    def attach(self, request: m.Request) -> None: ...
    def cancel(self, request_id: str) -> None: ...
    def answer(self, request_id: str, question_id: str, text: str) -> bool: ...
    def presented(self, request_id: str, preview: m.Approval) -> bool: ...


@dataclass(frozen=True, slots=True)
class SaveFailure:
    state: m.Canvas
    reason: str


class CanvasController:
    __slots__ = ("_events", "_save", "_save_failure", "_saved_state", "_writer", "backend", "changed", "closed", "state", "status", "store")

    def __init__(self, store: CanvasStore, backend: Backend, *, changed: Callable[[], None]) -> None:
        self.store, self.backend, self.changed = store, backend, changed
        self.status = ""
        self._save_failure: SaveFailure | None = None
        self._saved_state: m.Canvas | None = None
        self.closed = False
        try:
            self.state = store.load()
            self._saved_state = self.state
        except (OSError, ValueError) as error:
            # Keep the original intact if it cannot be read. A new session has a
            # different file, so even a future-schema document is recoverable.
            self.state = m.Canvas()
            self.store = CanvasStore(store.path.with_name(f"canvas-recovery-{int(time.time())}.json"))
            self.status = f"Could not read the saved canvas: {error}. This session will save separately."
        self._events: queue.SimpleQueue[m.Event | m.Item | str] = queue.SimpleQueue()
        self._save: queue.Queue[m.Canvas | None] = queue.Queue(maxsize=1)
        self._writer = threading.Thread(target=self._write, name="canvas persistence", daemon=True)
        backend.connect(self.publish)
        for item in self.state.items:
            if isinstance(item.content, m.Result) and isinstance(item.content.state, m.Running):
                backend.attach(item.content.state.request)
        self._writer.start()

    @property
    def saved_state(self) -> m.Canvas | None:
        """The last complete state read or successfully written by the owner.

        Queuing a commit never advances this observation. The writer publishes
        one immutable value only after the store has completed its atomic save.
        """
        return self._saved_state

    @property
    def save_failure(self) -> SaveFailure | None:
        return self._save_failure

    def commit(self, state: m.Canvas, *, render: bool = True) -> None:
        if self.closed or state == self.state:
            return
        self.state = state
        with contextlib.suppress(queue.Empty):
            self._save.get_nowait()
            self._save.task_done()
        self._save.put_nowait(state)
        if render:
            self.changed()

    def _write(self) -> None:
        while (state := self._save.get()) is not None:
            try:
                self.store.save(state)
                self._saved_state = state
                self._save_failure = None
            except (OSError, ValueError) as error:
                self._save_failure = SaveFailure(state, f"Canvas changes could not be saved: {error}")
                self._events.put(self._save_failure.reason)
            finally:
                self._save.task_done()
        self._save.task_done()

    def publish(self, event: m.Event | m.Item) -> None:
        if not self.closed:
            self._events.put(event)

    def problem(self, text: str) -> None:
        if not self.closed:
            self.status = text
            self.changed()

    def drain(self) -> None:
        state = self.state
        status = self.status
        for _ in range(128):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                if isinstance(event, str):
                    self.status = event
                elif isinstance(event, m.Item):
                    state = m.observe(state, event)
                else:
                    state = m.accept(state, event)
            except ValueError as error:
                self.status = str(error)
        if state != self.state:
            self.commit(state)
        elif status != self.status:
            self.changed()

    def submit(self, prompt: str, *, result_id: str | None = None) -> bool:
        try:
            state, request = m.request(self.state, uuid.uuid4().hex, result_id or uuid.uuid4().hex, prompt, refresh=result_id is not None)
        except ValueError as error:
            self.problem(str(error))
            return False
        self.commit(state)
        try:
            self.backend.submit(request)
        except (OSError, ValueError) as error:
            self.commit(m.accept(self.state, m.Failed(request.id, str(error))))
            self.problem(str(error))
            return False
        self.status = ""
        self.changed()
        return True

    def retry(self, identity: str) -> bool:
        item = self.state.item(identity)
        if item is None or not isinstance(item.content, m.Result) or not isinstance(item.content.state, m.Running):
            return False
        run = item.content.state
        if not isinstance(run.stage, m.Attention):
            return False
        try:
            # Reuse the original evidence and durable identity, even if the
            # canvas sources or conversation have changed since submission.
            self.backend.submit(run.request)
        except (OSError, ValueError) as error:
            self.commit(m.accept(self.state, m.NeedsAttention(run.request.id, str(error))))
            self.problem(str(error))
            return False
        self.status = ""
        queued = replace(run, stage=m.Stage.QUEUED, label="Reconnecting saved request…")
        self.commit(m.put(self.state, replace(item, content=replace(item.content, state=queued))))
        return True

    def cancel(self, identity: str) -> None:
        item = self.state.item(identity)
        if item is None or not isinstance(item.content, m.Result) or not isinstance(item.content.state, m.Running):
            return
        request_id = item.content.state.request.id
        try:
            self.backend.cancel(request_id)
        except (OSError, ValueError) as error:
            self.problem(str(error))
            return
        self.commit(m.accept(self.state, m.Stopped(request_id)))

    def answer(self, identity: str, text: str) -> bool:
        item = self.state.item(identity)
        if item is None or not isinstance(item.content, m.Result) or not isinstance(item.content.state, m.Running):
            return False
        run = item.content.state
        if not isinstance(run.stage, (m.Question, m.Approval)):
            return False
        try:
            accepted = self.backend.answer(run.request.id, run.stage.id, text)
        except (OSError, ValueError) as error:
            self.problem(str(error))
            return False
        if accepted:
            saving = replace(run, stage=m.Stage.WORKING, label="Saving your reply…")
            self.commit(m.put(self.state, replace(item, content=replace(item.content, state=saving))))
        return accepted

    def presented(self, identity: str, preview: m.Approval) -> bool:
        item = self.state.item(identity)
        if item is None or not isinstance(item.content, m.Result) or not isinstance(item.content.state, m.Running):
            return False
        run = item.content.state
        return run.stage == preview and self.backend.presented(run.request.id, preview)

    def voice(self, text: str, *, reply: Callable[[str], bool], active: bool, fallback: Callable[[str], None]) -> None:
        """Recheck pending input when queued voice text reaches the UI owner."""
        if reply(text):
            return
        if active and self.state.space.selection:
            self.submit(text)
        else:
            fallback(text)

    def new_space(self, title: str) -> None:
        if len(self.state.spaces) >= 16:
            raise ValueError("The desktop supports up to 16 workspaces.")
        space = m.Space(uuid.uuid4().hex, m.bounded(title.strip(), "Workspace name", 80))
        self.commit(replace(self.state, spaces=(*self.state.spaces, space), current=space.id))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # Finish the latest saved arrangement; closing the view does not cancel
        # agent work. The backend continues to own those tasks.
        try:
            self._save.put(None, timeout=4)
        except queue.Full as error:
            raise OSError("The canvas writer could not finish its pending save.") from error
        self._writer.join(timeout=4)
        if self._writer.is_alive():
            raise OSError("The canvas writer did not finish saving before shutdown.")
        if self._save_failure is not None:
            raise OSError(self._save_failure.reason)


def default_store() -> CanvasStore:
    from usage import DIRECTORY  # noqa: PLC0415

    return CanvasStore(Path(DIRECTORY) / "desktop" / "canvas.json")
