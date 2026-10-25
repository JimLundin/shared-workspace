"""Canvas replies recover by exact failed attempt, after confirmed journal reads."""

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from assistant.work_window import AnswerTask, RemoveContext, WorkSnapshot
from automation.context import Preference
from automation.task import Question, RequestSource, TaskStatus, TextAnswer, Waiting

from .test_external_input import has_pending_input
from .test_work_agent import RecordingBridge, WorkRig
from .test_work_submissions import PollHold


class SnapshotFeed:
    __slots__ = ("changed", "latest")

    def __init__(self) -> None:
        self.changed = threading.Condition()
        self.latest = WorkSnapshot()

    def publish(self, snapshot: WorkSnapshot) -> None:
        with self.changed:
            self.latest = snapshot
            self.changed.notify_all()

    def until(self, predicate: Callable[[WorkSnapshot], bool]) -> WorkSnapshot:
        with self.changed:
            assert self.changed.wait_for(lambda: predicate(self.latest), timeout=5), "The requested durable observation did not arrive."
            return self.latest


@contextmanager
def journal_outage(rig: WorkRig, gate: PollHold, feed: SnapshotFeed, watched: tuple[str, ...]) -> Generator[None]:
    # The bridge gate proves no journal connection is open while the fixture
    # replaces the database path. No timing or queue-drain assumption is used.
    gate.arm()
    rig.agent.watch(watched)
    assert gate.entered.wait(5)
    path = rig.tasks.path
    backup = path.with_suffix(".retained")
    try:
        path.rename(backup)
        path.mkdir()
    finally:
        gate.release.set()
    try:
        feed.until(lambda snapshot: bool(snapshot.status) and not snapshot.reply_problems)
        yield
    finally:
        path.rmdir()
        backup.rename(path)
        rig.agent.watch(watched)


def check_replaced_request(rig: WorkRig, feed: SnapshotFeed, command_finished: threading.Event) -> None:
    claim = rig.tasks.claim("worker", "replacement-run", at=rig.clock())
    assert claim is not None and claim.task.id == "canvas-task"
    rig.tasks.finish(claim.run, Waiting(Question("new-format", "Which new format?", "")), at=rig.clock())
    marker = "stale-reply-marker"
    rig.saved_context.save(Preference(marker, "Test marker", "Present", "Fixture", rig.clock()))
    feed.until(lambda snapshot: any(record.id == marker for record in snapshot.context))
    command_finished.clear()
    rig.agent.command(AnswerTask("canvas-task", "format", TextAnswer("A reply to the replaced request")))
    rig.agent.command(RemoveContext(marker))
    assert command_finished.wait(5) and rig.saved_context.get(marker) is None
    snapshot = feed.until(lambda state: all(record.id != marker for record in state.context))
    task = next(task for task in snapshot.tasks if task.id == "canvas-task")
    assert isinstance(task.state, Waiting) and isinstance(task.state.reason, Question) and task.state.reason.id == "new-format"
    assert not snapshot.reply_problems, "A failed reply to the replaced request cannot reopen it."


def test_watched_canvas_reply_failures_have_distinct_exact_receipts_after_confirmed_reads(tmp_path: Path) -> None:
    gate, feed, command_finished = PollHold(), SnapshotFeed(), threading.Event()
    bridge = RecordingBridge(on_poll=gate.poll, on_wake=command_finished.set)
    rig = WorkRig(tmp_path, bridge=bridge, on_snapshot=feed.publish, start=False)
    watched = ("canvas-task", "another-task")
    try:
        rig.agent.new_conversation()
        for index, identity in enumerate(watched):
            rig.tasks.submit(identity, "Choose the format", "Choose the format", RequestSource(f"saved-canvas-{index}"), at=rig.clock())
            claim = rig.tasks.claim("worker", f"question-run-{index}", at=rig.clock())
            assert claim is not None
            rig.tasks.finish(claim.run, Waiting(Question("format", "Which format?", "Choose the output format.")), at=rig.clock())
        rig.agent.watch(watched)
        rig.agent.start_worker()
        feed.until(lambda snapshot: {task.id for task in snapshot.tasks} == set(watched))
        assert not rig.agent.running() and not has_pending_input(rig.panel)

        previous_receipt = ""
        previous_reason = ""
        for attempt in range(2):
            with journal_outage(rig, gate, feed, watched):
                marker = f"command-marker-{attempt}"
                rig.saved_context.save(Preference(marker, "Test marker", "Present", "Fixture", rig.clock()))
                command_finished.clear()
                rig.agent.command(AnswerTask("canvas-task", "format", TextAnswer("Markdown")))
                rig.agent.command(RemoveContext(marker))
                # This separate journal mutation occurs after the AnswerTask
                # in the same command queue. Its wake and missing record prove
                # the failed answer was processed before the outage ends.
                assert command_finished.wait(5)
                assert rig.saved_context.get(marker) is None
                assert rig.agent.snapshot().reply_problems == ()
            def another_attempt(state: WorkSnapshot, receipt: str = previous_receipt) -> bool:
                return any(problem.task_id == "canvas-task" and problem.request_id == "format" and problem.receipt_id != receipt
                           for problem in state.reply_problems)

            snapshot = feed.until(another_attempt)
            assert len(snapshot.reply_problems) == 1
            problem = snapshot.reply_problems[0]
            assert problem.task_id == "canvas-task" and problem.request_id == "format"
            assert problem.receipt_id and "not saved" in problem.reason
            if previous_reason:
                assert problem.reason == previous_reason, "Equal failure reasons still need separate attempt identities."
            previous_receipt, previous_reason = problem.receipt_id, problem.reason
            assert rig.tasks.responses("canvas-task") == ()

        rig.agent.command(AnswerTask("canvas-task", "format", TextAnswer("Markdown")))
        snapshot = feed.until(lambda state: any(task.id == "canvas-task" and task.state is TaskStatus.READY for task in state.tasks)
                              and not state.reply_problems)
        assert [response.reply for response in rig.tasks.responses("canvas-task")] == [TextAnswer("Markdown")]
        other = next(task for task in snapshot.tasks if task.id == "another-task")
        assert isinstance(other.state, Waiting) and isinstance(other.state.reason, Question) and other.state.reason.id == "format"
        assert rig.tasks.responses("another-task") == ()
        check_replaced_request(rig, feed, command_finished)
    finally:
        gate.release.set()
        rig.close()
