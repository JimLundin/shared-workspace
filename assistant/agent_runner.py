"""Serial conversation work, independent of the SDK and the Windows application."""

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Protocol, assert_never

from .activity import ContextSnapshot
from .agent import Log
from .agent_window import AgentWindow


@dataclass(frozen=True, slots=True)
class Task:
    text: str
    context: ContextSnapshot | None = None


class Idle(Enum):
    READY = auto()


@dataclass(frozen=True, slots=True)
class Active:
    task: Task
    pending: tuple[Task, ...] = ()


@dataclass(frozen=True, slots=True)
class Stopping:
    task: Task


type Tasks = Idle | Active | Stopping


class Control(Enum):
    COMPLETE = auto()
    STOP = auto()


def transition(state: Tasks, event: Task | Control) -> Tasks:
    """Stop discards queued work; a new conversation can start after completion."""
    match state:
        case Idle.READY:
            return Active(event) if isinstance(event, Task) else state
        case Active(task, pending):
            match event:
                case Task():
                    return replace(state, pending=(*pending, event))
                case Control.COMPLETE:
                    return Active(pending[0], pending[1:]) if pending else Idle.READY
                case Control.STOP:
                    return Stopping(task)
                case _:
                    assert_never(event)
        case Stopping():
            return Idle.READY if event is Control.COMPLETE else state
        case _:
            assert_never(state)


class Execution(Protocol):
    """Own one conversation's execution resources and history, across turns."""

    def run(self, task: Task, log: Log, stop: threading.Event) -> None: ...
    def reset(self) -> None: ...


class AgentRunner:
    __slots__ = (
        "_create_window", "_execution", "_finished", "_lock", "_state", "_stop", "_window_lock",
        "context", "on_done", "on_start", "report_error", "window",
    )

    def __init__(
        self, execution: Execution, create_window: Callable[[AgentRunner], AgentWindow], *,
        on_done: Callable[[], None], report_error: Callable[[str], None], on_start: Callable[[], None] | None = None,
        context: Callable[[], ContextSnapshot] | None = None,
    ) -> None:
        self._execution, self._create_window = execution, create_window
        self.on_done, self.report_error, self.on_start, self.context = on_done, report_error, on_start, context
        self._state: Tasks = Idle.READY
        self._lock, self._window_lock = threading.Lock(), threading.Lock()
        self._stop, self._finished = threading.Event(), threading.Event()
        self._finished.set()
        self.window: AgentWindow | None = None

    def running(self) -> bool:
        with self._lock:
            return self._state is not Idle.READY

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for work and its completion callback, without inspecting a worker thread."""
        with self._lock:
            finished = self._finished
        return finished.wait(timeout)

    def prepare(self) -> None:
        self._panel(None, reveal=False)

    def show(self) -> None:
        self._panel(None, focus=True)

    def voice(self, text: str) -> None:
        if self.window is None or not self.window.reply_by_voice(text):
            self.start(text)

    def _panel(self, task: str | None, *, focus: bool = False, queued: bool = False, reveal: bool = True) -> AgentWindow:
        with self._window_lock:
            window = self.window or self._create_window(self)
            if task is not None:
                window.begin(task, self.stop, queued=queued)
            with self._lock:
                if self.window is None and isinstance(self._state, Active):
                    for pending in self._state.pending:
                        window.queued(pending.text)
                self.window = window
            if reveal and not queued:
                window.show(focus=focus)
            return window

    def start(self, text: str) -> None:
        if not (text := text.strip()):
            return
        task = Task(text, self.context() if self.context is not None else None)
        with self._lock:
            previous = self._state
            self._state = transition(previous, task)
            if previous is not Idle.READY:
                if isinstance(self._state, Active) and self.window is not None:
                    self.window.queued(text)
                return
            finished = self._finished = threading.Event()
            stop = self._stop = threading.Event()
        try:
            window = self._panel(text)
            if self.on_start is not None:
                self.on_start()
            threading.Thread(target=self._work, args=(task, window, stop, finished), name="agent", daemon=True).start()
        except Exception:
            try:
                with self._lock:
                    self._state = Idle.READY
                    if self.window is not None:
                        self.window.finish()
            finally:
                finished.set()
            raise

    def _work(self, task: Task, window: AgentWindow, stop: threading.Event, finished: threading.Event) -> None:
        try:
            while True:
                if not stop.is_set():
                    self._execution.run(task, window, stop)
                with self._lock:
                    self._state = transition(self._state, Control.COMPLETE)
                    if not isinstance(self._state, Active):
                        window.finish()
                        break
                    task = self._state.task
                    stop = self._stop = threading.Event()
                window = self._panel(task.text, queued=True)
                if stop.is_set():
                    window.stopping()
        except Exception:
            self.report_error(traceback.format_exc())
            with self._lock:
                self._state = Idle.READY
                window.problem("Couldn't continue the agent conversation; see the assistant's log.")
                window.finish()
        finally:
            try:
                self.on_done()
            finally:
                finished.set()

    def stop(self) -> None:
        with self._lock:
            self._state = transition(self._state, Control.STOP)
            self._stop.set()
            if self.window is not None:
                self.window.stopping()

    def new_conversation(self) -> bool:
        with self._lock:
            if self._state is not Idle.READY:
                return False
            self._execution.reset()
            if self.window is not None:
                self.window.reset()
            return True
