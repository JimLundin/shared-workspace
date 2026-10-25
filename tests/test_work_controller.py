"""Work lifetimes through the controller, real journals, and execution adapters."""

import itertools
import threading
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from assistant.agent_session import LiveMessage
from automation.controller import ControllerSnapshot, Fault, Idle, Stopping, WorkController
from automation.execution import Log, Outcome
from automation.store import TaskStore
from automation.task import (
    ActionSpec,
    Blocked,
    Claim,
    Completed,
    Control,
    Interrupted,
    Question,
    RequestSource,
    Running,
    RunRef,
    Task,
    TaskStatus,
    TextAnswer,
    Until,
    Waiting,
)
from automation.task import (
    Outcome as StoredOutcome,
)


class Clock:
    def __init__(self) -> None:
        self.value = 100.0
        self.lock = threading.Lock()

    def now(self) -> float:
        with self.lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


class QuietLog:
    def step(self, message: str) -> None:
        pass

    def say(self, message: str) -> None:
        pass

    def updates(self) -> Callable[[LiveMessage], None]:
        return lambda message: None

    def problem(self, message: str) -> None:
        pass

    def done(self, message: str) -> None:
        pass


class Observations:
    def __init__(self) -> None:
        self.changed = threading.Condition()
        self.snapshots: list[ControllerSnapshot] = []

    def publish(self, snapshot: ControllerSnapshot) -> None:
        with self.changed:
            self.snapshots.append(snapshot)
            self.changed.notify_all()

    def until(self, predicate: Callable[[ControllerSnapshot], bool]) -> ControllerSnapshot:
        with self.changed:
            assert self.changed.wait_for(lambda: any(predicate(item) for item in self.snapshots), timeout=5), self.snapshots
            return next(item for item in reversed(self.snapshots) if predicate(item))


class Execution:
    def __init__(self, operation: Callable[[Claim, TaskStore, threading.Event], Outcome] | None = None) -> None:
        self.claims: list[Claim] = []
        self.operation = operation

    def run_work(self, claim: Claim, store: TaskStore, log: Log, stop: threading.Event) -> Outcome:
        self.claims.append(claim)
        current = store.get(claim.task.id)
        assert current is not None and isinstance(current.state, Running)
        assert current.state.run == claim.run, "Claiming must commit before execution sees the task."
        if self.operation is not None:
            return self.operation(claim, store, stop)
        return Completed("Verified " + claim.task.prompt, ("Observed fixture output.",))


class Rig:
    def __init__(
        self, path: Path, execution: Execution | None = None, *, owner: str = "owner",
        store: TaskStore | None = None, on_change: Callable[[ControllerSnapshot], None] | None = None,
    ) -> None:
        self.store = store if store is not None else TaskStore(path)
        self.execution = execution if execution is not None else Execution()
        self.observations, self.clock = Observations(), Clock()
        self.errors: list[str] = []
        counter = itertools.count()
        self.controller = WorkController(
            self.store, self.execution, owner=owner, log=lambda claim: QuietLog(), now=self.clock.now,
            new_run_id=lambda: f"{owner}-{next(counter)}", on_change=on_change or self.observations.publish,
            on_error=self.errors.append,
        )

    def submit(self, identity: str, prompt: str | None = None) -> Task:
        return self.store.submit(identity, identity, prompt or identity, RequestSource("conversation"), at=self.clock.now())

    @contextmanager
    def running(self) -> Generator[WorkController]:
        self.controller.start()
        try:
            yield self.controller
        finally:
            self.controller.shutdown()
            assert self.controller.wait(5), "The controller must finish all execution cleanup."


def task_is(snapshot: ControllerSnapshot, identity: str, state: type[Completed] | type[Waiting] | type[Interrupted]) -> bool:
    return any(task.id == identity and isinstance(task.state, state) for task in snapshot.tasks)


def test_startup_recovers_a_committed_task_before_dispatch_and_keeps_its_checkpoint(tmp_path: Path) -> None:
    original = TaskStore(tmp_path / "work.sqlite3")
    original.submit("report", "Report", "Prepare the report", RequestSource("conversation"), at=1)
    abandoned = original.claim("previous-process", "old-run", at=2)
    assert abandoned is not None
    original.progress(abandoned.run, "The source workbook has been identified.", at=3)
    execution = Execution()
    rig = Rig(tmp_path / "work.sqlite3", execution, owner="restarted-process")
    with rig.running():
        rig.observations.until(lambda snapshot: task_is(snapshot, "report", Completed))
    assert len(execution.claims) == 1 and isinstance(execution.claims[0].previous, Interrupted)
    assert execution.claims[0].task.checkpoint is not None
    assert execution.claims[0].task.checkpoint.summary == "The source workbook has been identified."
    reopened = TaskStore(tmp_path / "work.sqlite3").get("report")
    assert reopened is not None and isinstance(reopened.state, Completed)
    assert reopened.state.result == "Verified Prepare the report"
    assert not rig.errors


def test_pause_resume_waits_for_cancelled_execution_to_release_resources(tmp_path: Path) -> None:
    entered, cancellation_seen, release, resumed = (threading.Event() for _ in range(4))
    occupied = threading.Lock()
    attempts = 0

    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        nonlocal attempts
        attempts += 1
        assert occupied.acquire(blocking=False), "Two task runs must not own the execution resource together."
        try:
            if attempts == 1:
                entered.set()
                assert stop.wait(5)
                cancellation_seen.set()
                assert release.wait(5)
                return Completed("A late result from the paused run.", ())
            resumed.set()
            return Completed("The resumed run verified the result.", ())
        finally:
            occupied.release()

    execution = Execution(operate)
    rig = Rig(tmp_path / "work.sqlite3", execution)
    rig.submit("task")
    with rig.running() as controller:
        try:
            assert entered.wait(5)
            assert rig.store.control("task", Control.PAUSE, at=100) is not None
            assert rig.store.control("task", Control.RESUME, at=100) is not None
            controller.wake()
            assert cancellation_seen.wait(5)
            rig.observations.until(lambda snapshot: isinstance(snapshot.status, Stopping))
            assert not resumed.is_set()
            pending = rig.store.get("task")
            assert pending is not None and pending.state is TaskStatus.READY
        finally:
            release.set()
        rig.observations.until(lambda snapshot: task_is(snapshot, "task", Completed))
    final = rig.store.get("task")
    assert final is not None and isinstance(final.state, Completed)
    assert final.state.result == "The resumed run verified the result."
    assert len(execution.claims) == 2 and execution.claims[0].run != execution.claims[1].run
    assert not rig.errors


def test_cancelled_work_is_not_revived_by_a_late_completed_result(tmp_path: Path) -> None:
    entered, cancelled, release = (threading.Event() for _ in range(3))

    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        if claim.task.id == "first":
            entered.set()
            assert stop.wait(5)
            cancelled.set()
            assert release.wait(5)
            return Completed("Too late.", ())
        return Completed("Second task finished.", ())

    rig = Rig(tmp_path / "work.sqlite3", Execution(operate))
    rig.submit("first")
    rig.submit("second")
    with rig.running() as controller:
        try:
            assert entered.wait(5)
            rig.store.control("first", Control.CANCEL, at=100)
            controller.wake()
            assert cancelled.wait(5)
        finally:
            release.set()
        rig.observations.until(lambda snapshot: task_is(snapshot, "second", Completed))
    first = rig.store.get("first")
    assert first is not None and first.state is TaskStatus.CANCELLED
    assert [claim.task.id for claim in rig.execution.claims] == ["first", "second"]
    assert not rig.errors


def test_waiting_question_releases_execution_and_exact_answer_starts_a_fresh_run(tmp_path: Path) -> None:
    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        if claim.task.id == "ask" and not store.responses("ask"):
            store.progress(claim.run, "Need the destination workbook.", at=100)
            return Waiting(Question("destination", "Which workbook?", ""))
        if claim.task.id == "ask":
            answer = store.responses("ask")[0].reply
            assert isinstance(answer, TextAnswer)
            return Completed("Used " + answer.text, ())
        return Completed("Background work finished while the question was pending.", ())

    rig = Rig(tmp_path / "work.sqlite3", Execution(operate))
    rig.submit("ask")
    rig.submit("background")
    with rig.running() as controller:
        rig.observations.until(lambda snapshot: task_is(snapshot, "background", Completed))
        pending = TaskStore(tmp_path / "work.sqlite3").get("ask")
        assert pending is not None and isinstance(pending.state, Waiting)
        assert rig.store.answer("ask", "stale-question", TextAnswer("Wrong"), at=100) is None
        assert rig.store.answer("ask", "destination", TextAnswer("budget.xlsx"), at=100) is not None
        controller.wake()
        rig.observations.until(lambda snapshot: task_is(snapshot, "ask", Completed))
    result = rig.store.get("ask")
    assert result is not None and isinstance(result.state, Completed) and result.state.result == "Used budget.xlsx"
    assert [claim.task.id for claim in rig.execution.claims] == ["ask", "background", "ask"]
    assert rig.execution.claims[0].run != rig.execution.claims[2].run and not rig.errors


def test_shutdown_parks_active_work_and_preserves_queued_tasks_until_restart(tmp_path: Path) -> None:
    entered, cancellation_seen, release = (threading.Event() for _ in range(3))

    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        entered.set()
        assert stop.wait(5)
        cancellation_seen.set()
        assert release.wait(5)
        return Completed("An outcome reported during shutdown.", ())

    rig = Rig(tmp_path / "work.sqlite3", Execution(operate), owner="before-shutdown")
    rig.submit("first")
    rig.submit("second")
    rig.controller.start()
    try:
        assert entered.wait(5)
        rig.controller.shutdown()
        assert cancellation_seen.wait(5)
        rig.observations.until(lambda snapshot: task_is(snapshot, "first", Interrupted))
        assert not rig.controller.wait(0)
        queued = rig.store.get("second")
        assert queued is not None and queued.state is TaskStatus.READY
    finally:
        release.set()
        rig.controller.shutdown()
        assert rig.controller.wait(5)
    assert rig.controller.snapshot().status is Idle.STOPPED
    assert [claim.task.id for claim in rig.execution.claims] == ["first"]
    resumed = Rig(tmp_path / "work.sqlite3", owner="after-restart")
    with resumed.running():
        resumed.observations.until(lambda snapshot: task_is(snapshot, "second", Completed))
    assert [claim.task.id for claim in resumed.execution.claims] == ["first", "second"]
    assert isinstance(resumed.execution.claims[0].previous, Interrupted)
    assert not rig.errors and not resumed.errors


def test_live_interruption_waits_for_resume_instead_of_retrying_forever(tmp_path: Path) -> None:
    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        if claim.task.id == "unavailable":
            return Interrupted(claim.run, "The endpoint is unavailable.")
        return Completed("Other work can continue.", ())

    rig = Rig(tmp_path / "work.sqlite3", Execution(operate))
    rig.submit("unavailable")
    with rig.running() as controller:
        rig.observations.until(lambda snapshot: task_is(snapshot, "unavailable", Waiting))
        rig.submit("other")
        controller.wake()
        rig.observations.until(lambda snapshot: task_is(snapshot, "other", Completed))
        assert [claim.task.id for claim in rig.execution.claims] == ["unavailable", "other"]
        waiting = rig.store.get("unavailable")
        assert waiting is not None and waiting.state == Waiting(Blocked("The endpoint is unavailable."))
    assert not rig.errors


def assert_execution_failure_releases_other_work(tmp_path: Path, error: BaseException) -> None:
    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        if claim.task.id == "broken":
            raise error
        return Completed("Other work can continue.", ())

    rig = Rig(tmp_path / "work.sqlite3", Execution(operate))
    rig.submit("broken")
    rig.submit("other")
    with rig.running():
        rig.observations.until(lambda snapshot: task_is(snapshot, "other", Completed))
        broken = rig.store.get("broken")
        assert broken is not None and isinstance(broken.state, Waiting) and isinstance(broken.state.reason, Blocked)
        assert [claim.task.id for claim in rig.execution.claims] == ["broken", "other"]
    assert rig.errors == [f"{type(error).__name__}: {error}"]


def test_execution_error_waits_for_attention_and_releases_other_work(tmp_path: Path) -> None:
    assert_execution_failure_releases_other_work(tmp_path, RuntimeError("The adapter failed."))


def test_execution_exit_waits_for_attention_and_releases_other_work(tmp_path: Path) -> None:
    assert_execution_failure_releases_other_work(tmp_path, SystemExit("The adapter exited."))


def test_wakeup_uses_supplied_time_and_an_until_wait_releases_other_work(tmp_path: Path) -> None:
    attempts = 0

    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        nonlocal attempts
        if claim.task.id == "delayed":
            attempts += 1
            if attempts == 1:
                return Waiting(Until(120, "Wait for publication."))
        return Completed("The due operation finished.", ())

    rig = Rig(tmp_path / "work.sqlite3", Execution(operate))
    rig.submit("delayed")
    rig.submit("other")
    with rig.running() as controller:
        rig.observations.until(lambda snapshot: task_is(snapshot, "other", Completed))
        assert [claim.task.id for claim in rig.execution.claims] == ["delayed", "other"]
        rig.clock.advance(20)
        controller.wake()
        rig.observations.until(lambda snapshot: task_is(snapshot, "delayed", Completed))
    assert [claim.task.id for claim in rig.execution.claims] == ["delayed", "other", "delayed"]
    assert not rig.errors


class FailingFinishStore(TaskStore):
    def finish(self, run: RunRef, outcome: StoredOutcome, *, at: float) -> Task | None:
        raise OSError("The journal cannot commit the outcome.")


def test_persistence_failure_halts_dispatch_and_leaves_recoverable_work(tmp_path: Path) -> None:
    store = FailingFinishStore(tmp_path / "work.sqlite3")
    rig = Rig(tmp_path / "work.sqlite3", store=store)
    rig.submit("first")
    rig.submit("second")
    rig.controller.start()
    try:
        assert rig.controller.wait(5)
    finally:
        rig.controller.shutdown()
        assert rig.controller.wait(5)
    snapshot = rig.controller.snapshot()
    assert isinstance(snapshot.status, Fault) and "cannot commit" in snapshot.status.reason
    assert [claim.task.id for claim in rig.execution.claims] == ["first"]
    first, second = store.get("first"), store.get("second")
    assert first is not None and isinstance(first.state, Interrupted)
    assert second is not None and second.state is TaskStatus.READY
    assert len(rig.errors) == 1 and "cannot commit" in rig.errors[0]


def test_unresolved_action_prevents_completion_without_silent_reclaim(tmp_path: Path) -> None:
    def operate(claim: Claim, store: TaskStore, stop: threading.Event) -> Outcome:
        store.begin_action(claim.run, "uncertain-action", ActionSpec("write", "Pending external write", "a" * 64), at=100)
        return Completed("This unsupported completion must be refused.", ())

    rig = Rig(tmp_path / "work.sqlite3", Execution(operate))
    rig.submit("task")
    rig.controller.start()
    try:
        assert rig.controller.wait(5)
    finally:
        rig.controller.shutdown()
        assert rig.controller.wait(5)
    assert isinstance(rig.controller.snapshot().status, Fault)
    assert len(rig.execution.claims) == 1
    reopened = TaskStore(tmp_path / "work.sqlite3")
    task = reopened.get("task")
    assert task is not None and isinstance(task.state, Interrupted)
    assert [action.id for action in reopened.unresolved_actions("task")] == ["uncertain-action"]
    assert len(rig.errors) == 1 and "outcome was refused" in rig.errors[0]


def test_wait_includes_final_publication_and_run_is_a_blocking_lifecycle(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    observations = Observations()

    def publish(snapshot: ControllerSnapshot) -> None:
        observations.publish(snapshot)
        if snapshot.status is Idle.STOPPED:
            entered.set()
            assert release.wait(5)

    rig = Rig(tmp_path / "work.sqlite3", on_change=publish)
    rig.submit("task")
    with ThreadPoolExecutor(max_workers=1) as worker:
        running = worker.submit(rig.controller.run)
        try:
            observations.until(lambda snapshot: task_is(snapshot, "task", Completed))
            rig.controller.shutdown()
            assert entered.wait(5) and not rig.controller.wait(0)
        finally:
            release.set()
            rig.controller.shutdown()
            running.result(5)
            assert rig.controller.wait(5)
    assert rig.controller.snapshot().status is Idle.STOPPED and not rig.errors
