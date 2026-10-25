"""One executing task, durable controls, and volatile immutable observations.

The process must hold exclusive work ownership before starting this controller.
Each controller is one-shot and receives a fresh owner identity. Recovery is a
startup operation, never a lease sweeper for a peer that might still be alive.
"""

import math
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, assert_never

from .execution import Log, Outcome
from .store import MAX_PAGE, TaskStore
from .task import Blocked, Claim, Completed, Failed, Interrupted, RunRef, Task, Waiting, identifier, integer, owns


class Idle(Enum):
    NEW = auto()
    READY = auto()
    STOPPED = auto()


@dataclass(frozen=True, slots=True)
class Working:
    run: RunRef


@dataclass(frozen=True, slots=True)
class Stopping:
    run: RunRef


@dataclass(frozen=True, slots=True)
class Fault:
    reason: str


type Status = Idle | Working | Stopping | Fault


@dataclass(frozen=True, slots=True)
class ControllerSnapshot:
    status: Status
    tasks: tuple[Task, ...]


class WorkExecution(Protocol):
    """Execute one claim; return only after its native resources are released."""

    def run_work(self, claim: Claim, store: TaskStore, log: Log, stop: threading.Event) -> Outcome: ...


@dataclass(slots=True)
class _Flight:
    claim: Claim
    stop: threading.Event
    result: Future[Outcome]
    thread: threading.Thread


def live_outcome(outcome: Outcome) -> Completed | Failed | Waiting:
    """Live interruptions wait for a defined resume, rather than spinning."""
    match outcome:
        case Completed() | Failed() | Waiting():
            return outcome
        case Interrupted(reason=reason):
            return Waiting(Blocked(reason))
        case _:
            assert_never(outcome)


def failure_text(error: BaseException) -> str:
    detail = str(error).replace("\0", "").strip()[:4_000]
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


class WorkController:
    """Own execution threads while the task store owns durable decisions.

    start() recovers and publishes the initial snapshot synchronously. On
    return the process may report readiness. run() performs the same setup and
    serves on the caller's thread instead.

    Callbacks and log factories must be nonblocking, volatile adapters. The
    controller retains only its latest bounded snapshot, not a queue of model
    messages. GUI/IPC owners choose how to consume or coalesce observations.
    shutdown() requests cancellation promptly; wait() includes all execution
    cleanup and final publication. A caller must retain process ownership until
    wait() succeeds.
    """

    __slots__ = (
        "_finished", "_flight", "_lock", "_shutdown", "_snapshot", "_started", "_wake",
        "execution", "limit", "log", "new_run_id", "now", "on_change", "on_error", "owner", "poll_seconds", "store",
    )

    def __init__(
        self, store: TaskStore, execution: WorkExecution, *, owner: str,
        log: Callable[[Claim], Log], now: Callable[[], float], new_run_id: Callable[[], str],
        on_change: Callable[[ControllerSnapshot], None], on_error: Callable[[str], None],
        poll_seconds: float = .1, limit: int = 100,
    ) -> None:
        identifier(owner, "worker owner")
        integer(limit, "task snapshot limit", minimum=1, maximum=MAX_PAGE)
        if isinstance(poll_seconds, bool) or not math.isfinite(poll_seconds) or not 0 < poll_seconds <= 1:
            raise ValueError("Work polling must be between zero and one second.")
        self.store, self.execution, self.owner = store, execution, owner
        self.log, self.now, self.new_run_id = log, now, new_run_id
        self.on_change, self.on_error = on_change, on_error
        self.poll_seconds, self.limit = poll_seconds, limit
        self._lock = threading.Lock()
        self._shutdown, self._wake, self._finished = threading.Event(), threading.Event(), threading.Event()
        self._finished.set()
        self._snapshot = ControllerSnapshot(Idle.NEW, ())
        self._started = False
        self._flight: _Flight | None = None

    def snapshot(self) -> ControllerSnapshot:
        """Read the latest published value without doing storage work."""
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        """Recover under the caller's exclusive ownership, then serve in a thread."""
        self._prepare()
        try:
            threading.Thread(target=self._serve, name="work controller", daemon=True).start()
        except BaseException as error:
            self._close(error)
            raise

    def run(self) -> None:
        """Blocking alternative to start(), with the same ownership precondition."""
        self._prepare()
        self._serve()

    def wake(self) -> None:
        """Notify the controller after submissions, answers, or durable controls."""
        self._wake.set()

    def shutdown(self) -> None:
        """Park unfinished work; never turn application shutdown into cancellation."""
        self._shutdown.set()
        with self._lock:
            if self._flight is not None:
                self._flight.stop.set()
        self._wake.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for this controller's execution cleanup and final callbacks."""
        with self._lock:
            finished = self._finished
        return finished.wait(timeout)

    def _prepare(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("A work controller can only be started once.")
            self._started = True
            self._finished = threading.Event()
        try:
            self.store.recover(self.owner, at=self.now())
            self._publish(Idle.READY)
        except BaseException as error:
            self._close(error)
            raise

    def _publish(self, status: Status) -> None:
        snapshot = ControllerSnapshot(status, self.store.list_tasks(limit=self.limit))
        with self._lock:
            if snapshot == self._snapshot:
                return
            self._snapshot = snapshot
        self.on_change(snapshot)

    def _serve(self) -> None:
        error: BaseException | None = None
        try:
            while True:
                self._wake.clear()
                flight = self._flight
                if flight is not None and flight.result.done():
                    self._settle(flight)
                    flight = None
                if self._shutdown.is_set():
                    break
                if flight is None:
                    claim = self.store.claim(self.owner, self.new_run_id(), at=self.now())
                    if claim is not None:
                        self._dispatch(claim)
                    else:
                        self._publish(Idle.READY)
                else:
                    task = self.store.get(flight.claim.task.id)
                    if task is None or not owns(task, flight.claim.run):
                        flight.stop.set()
                    self._publish(Stopping(flight.claim.run) if flight.stop.is_set() else Working(flight.claim.run))
                self._wake.wait(self.poll_seconds)
        except BaseException as caught:
            error = caught
            if not isinstance(caught, Exception):
                raise
        finally:
            self._close(error)

    def _dispatch(self, claim: Claim) -> None:
        stop = threading.Event()
        result: Future[Outcome] = Future()
        thread = threading.Thread(target=self._execute, args=(claim, stop, result), name="work execution", daemon=True)
        flight = _Flight(claim, stop, result, thread)
        with self._lock:
            if self._shutdown.is_set():
                stop.set()
            self._flight = flight
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._flight = None
            self.store.interrupt(claim.run, "Execution could not start.", at=self.now())
            raise
        self._publish(Stopping(claim.run) if stop.is_set() else Working(claim.run))

    def _execute(self, claim: Claim, stop: threading.Event, result: Future[Outcome]) -> None:
        try:
            if stop.is_set():
                outcome: Outcome = Interrupted(claim.run, "Toolkit is stopping.")
            else:
                log = self.log(claim)
                outcome = (Interrupted(claim.run, "Toolkit is stopping.") if stop.is_set()
                           else self.execution.run_work(claim, self.store, log, stop))
            result.set_result(outcome)
        except BaseException as error:
            result.set_exception(error)
        finally:
            self._wake.set()

    def _settle(self, flight: _Flight) -> None:
        # Future completion means the execution adapter returned, while joining
        # also finishes the target's final notification before another claim.
        flight.thread.join()
        try:
            outcome = live_outcome(flight.result.result())
        except BaseException as error:
            self._report(failure_text(error))
            outcome = Waiting(Blocked("Execution failed. Inspect the task and resume when its blocker is resolved."))
        if self._shutdown.is_set():
            self.store.interrupt(flight.claim.run, "Toolkit stopped before the task outcome was saved.", at=self.now())
        else:
            saved = self.store.finish(flight.claim.run, outcome, at=self.now())
            if saved is None:
                latest = self.store.get(flight.claim.task.id)
                if latest is not None and owns(latest, flight.claim.run):
                    raise RuntimeError("The task outcome was refused; execution has been stopped without claiming more work.")
        with self._lock:
            self._flight = None
        self._publish(Idle.READY)

    def _report(self, reason: str) -> None:
        try:
            self.on_error(reason)
        except BaseException:
            # A broken error adapter must not hide the controller's Fault
            # snapshot or skip its execution cleanup.
            traceback.print_exc()

    def _close(self, error: BaseException | None) -> None:
        self._shutdown.set()
        reasons = [failure_text(error)] if error is not None else []
        flight = self._flight
        try:
            if flight is not None:
                flight.stop.set()
                try:
                    self.store.interrupt(
                        flight.claim.run,
                        "The work controller stopped after an error." if reasons else "Toolkit stopped before the task finished.",
                        at=self.now(),
                    )
                    self._publish(Stopping(flight.claim.run))
                except BaseException as parking_error:
                    reasons.append(failure_text(parking_error))
                finally:
                    flight.thread.join()
                    with self._lock:
                        self._flight = None
            status: Status = Fault("\n".join(reasons)) if reasons else Idle.STOPPED
            try:
                self._publish(status)
            except BaseException as publication_error:
                reasons.append(failure_text(publication_error))
                with self._lock:
                    self._snapshot = ControllerSnapshot(Fault("\n".join(reasons)), self._snapshot.tasks)
            if reasons:
                self._report("\n".join(reasons))
        finally:
            self._finished.set()
