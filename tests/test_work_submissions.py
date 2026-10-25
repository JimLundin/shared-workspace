"""Identified submissions remain reviewable across queue, journal, and UI failures."""

import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase

from assistant import agent_session
from automation.task import Completed, Question, RequestSource, RoutineSource, TaskStatus, TextAnswer, Waiting

from .test_external_input import has_pending_input
from .test_work_agent import RecordingBridge, WorkRig, invocation


class PollHold:
    """Pause the explicit bridge adapter while no journal connection is open."""

    __slots__ = ("armed", "entered", "release")

    def __init__(self) -> None:
        self.armed, self.entered, self.release = threading.Event(), threading.Event(), threading.Event()

    def arm(self) -> None:
        self.entered.clear()
        self.release.clear()
        self.armed.set()

    def poll(self) -> None:
        if self.armed.is_set():
            self.armed.clear()
            self.entered.set()
            assert self.release.wait(5)


@contextmanager
def unavailable_journal(rig: WorkRig, gate: PollHold) -> Generator[None]:
    gate.arm()
    completed = rig.agent.refresh()
    assert gate.entered.wait(5)
    path = rig.tasks.path
    backup = path.with_suffix(".retained")
    try:
        path.rename(backup)
        path.mkdir()
    finally:
        gate.release.set()
    try:
        assert completed.wait(5)
        yield
    finally:
        path.rmdir()
        backup.rename(path)


def test_identified_duplicates_reuse_the_original_source_and_invocation_context_after_new_chat(tmp_path: Path) -> None:
    current = invocation("Original workbook")
    rig = WorkRig(tmp_path, context=lambda: current)
    try:
        rig.agent.submit_identified("canvas-task", "Summarize this workbook")
        rig.refresh()
        original = rig.tasks.get("canvas-task")
        assert original is not None
        current = invocation("A different workbook")
        assert rig.agent.new_conversation()
        rig.agent.submit_identified("canvas-task", "Summarize this workbook")
        snapshot = rig.refresh()
        assert rig.tasks.get("canvas-task") == original
        assert rig.bridge.registrations == [(original.id, invocation("Original workbook"), 1234)]
        assert snapshot.submission_problems == () and len(snapshot.tasks) == 1
        assert snapshot.status == "" and not rig.errors
        rig.agent.submit_identified("canvas-task", "An unrelated request")
        snapshot = rig.refresh()
        assert rig.tasks.get("canvas-task") == original
        assert len(rig.bridge.registrations) == 1
        assert [(problem.task_id, "different request" in problem.reason) for problem in snapshot.submission_problems] == [(original.id, True)]
        # An unrelated row with the same ID cannot clear the failed submission.
        assert rig.refresh().submission_problems == snapshot.submission_problems
    finally:
        rig.close()


def test_an_identified_request_cannot_attach_a_routine_task_with_the_same_id_and_prompt(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path)
    try:
        original = rig.tasks.submit("collision", "Routine work", "Routine work", RoutineSource("routine", 0, "occurrence"), at=rig.clock())
        rig.agent.submit_identified("collision", "Routine work")
        snapshot = rig.refresh()
        assert rig.tasks.get("collision") == original and rig.bridge.registrations == []
        assert [problem.task_id for problem in snapshot.submission_problems] == ["collision"]
    finally:
        rig.close()


def test_invalid_identified_input_is_rejected_before_the_canvas_accepts_it(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path, start=False)
    try:
        with TestCase().assertRaisesRegex(ValueError, "task identity"):
            rig.agent.submit_identified("invalid\0identity", "A request")
        with TestCase().assertRaisesRegex(ValueError, "task prompt"):
            rig.agent.submit_identified("valid-identity", " \n ")
        assert rig.agent.snapshot().tasks == () and not rig.agent.running()
    finally:
        rig.close()


def test_unconfirmed_submission_keeps_known_rows_and_later_matching_work_clears_only_its_problem(tmp_path: Path) -> None:
    gate = PollHold()
    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_poll=gate.poll))
    try:
        rig.agent.submit_identified("known-task", "Known work")
        before = rig.refresh()
        original = rig.tasks.get("known-task")
        assert original is not None
        rig.agent.new_conversation()
        rig.agent.watch(("known-task", "unconfirmed", "another-unconfirmed"))
        with unavailable_journal(rig, gate):
            rig.agent.submit_identified("unconfirmed", "A request whose commit is unconfirmed")
            rig.agent.submit_identified("another-unconfirmed", "A separate request")
            assert rig.agent.refresh().wait(5)
            snapshot = rig.agent.snapshot()
            assert snapshot.tasks == before.tasks
            assert {problem.task_id for problem in snapshot.submission_problems} == {"unconfirmed", "another-unconfirmed"}
            assert all("Could not confirm" in problem.reason for problem in snapshot.submission_problems)
        # A successful later observation can confirm an earlier committed ID,
        # including its original conversation after the frontend started anew.
        rig.tasks.submit(
            "unconfirmed", "A request whose commit is unconfirmed", "A request whose commit is unconfirmed", original.source, at=rig.clock(),
        )
        snapshot = rig.refresh()
        assert any(task.id == "unconfirmed" for task in snapshot.tasks)
        assert [problem.task_id for problem in snapshot.submission_problems] == ["another-unconfirmed"]
    finally:
        rig.close()


def test_watching_a_missing_id_distinguishes_an_observed_absence_from_a_failed_read(tmp_path: Path) -> None:
    gate = PollHold()
    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_poll=gate.poll))
    try:
        rig.agent.watch(("missing-task",))
        problem = rig.refresh().submission_problems[0]
        assert problem.task_id == "missing-task" and "No saved task was found" in problem.reason
        with unavailable_journal(rig, gate):
            snapshot = rig.agent.snapshot()
            assert "Could not confirm" in snapshot.submission_problems[0].reason
    finally:
        rig.close()


def test_canvas_and_meeting_watches_retain_each_others_tasks(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path)
    try:
        rig.agent.watch(("canvas-result",))
        rig.agent.watch(("meeting-followup",), owner="meetings")
        assert {problem.task_id for problem in rig.refresh().submission_problems} == {"canvas-result", "meeting-followup"}
        rig.agent.watch(())
        assert {problem.task_id for problem in rig.refresh().submission_problems} == {"meeting-followup"}
        rig.agent.watch(("canvas-result",))
        rig.agent.watch((), owner="meetings")
        assert {problem.task_id for problem in rig.refresh().submission_problems} == {"canvas-result"}
    finally:
        rig.close()


def test_watch_capacity_rejection_preserves_both_views_interests(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path)
    canvas = tuple(f"canvas-{index}" for index in range(256))
    meetings = tuple(f"meeting-{index}" for index in range(256))
    try:
        rig.agent.watch(canvas)
        rig.agent.watch(meetings, owner="meetings")
        with TestCase().assertRaisesRegex(ValueError, "512"):
            rig.agent.watch((*meetings, "one-too-many"), owner="meetings")
        assert {problem.task_id for problem in rig.refresh().submission_problems} == {*canvas, *meetings}
        rig.agent.watch((), owner="meetings")
        rig.agent.watch(("replacement",), owner="meetings")
        assert {problem.task_id for problem in rig.refresh().submission_problems} == {*canvas, "replacement"}
    finally:
        rig.close()


def test_unrelated_failures_cannot_evict_a_watched_identity_collision(tmp_path: Path) -> None:
    gate = PollHold()
    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_poll=gate.poll))
    try:
        rig.agent.submit_identified("watched-collision", "The original request")
        rig.agent.watch(("watched-collision",))
        rig.refresh()
        rig.agent.submit_identified("watched-collision", "A conflicting request")
        collision = rig.refresh().submission_problems[0]
        with unavailable_journal(rig, gate):
            for batch in range(3):
                for index in range(100):
                    rig.agent.submit_identified(f"failed-{batch}-{index}", "Another request during the outage")
                assert rig.agent.refresh().wait(5)
            assert collision in rig.agent.snapshot().submission_problems
        snapshot = rig.refresh()
        assert collision in snapshot.submission_problems
        original = next(task for task in snapshot.tasks if task.id == "watched-collision")
        assert original.prompt == "The original request"
    finally:
        rig.close()


def test_watch_recovers_an_old_completed_id_outside_the_overview_and_coalesces_replacements(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path, start=False)
    try:
        rig.tasks.submit("old-canvas-task", "Old task", "Old task", RequestSource("old-conversation"), at=10)
        claim = rig.tasks.claim("worker", "old-run", at=11)
        assert claim is not None
        rig.tasks.finish(claim.run, Completed("The old saved result", ()), at=12)
        for index in range(1_000):
            rig.tasks.submit(f"newer-{index:04}", "Newer work", "Newer work", RequestSource("another-conversation"), at=100 + index)
        assert all(task.id != "old-canvas-task" for task in rig.tasks.list_tasks(limit=1_000))
        rig.agent.watch(("superseded-interest",))
        rig.agent.watch(("old-canvas-task",) * 600)
        with TestCase().assertRaisesRegex(ValueError, "512"):
            rig.agent.watch(tuple(f"over-capacity-{index}" for index in range(513)))
        rig.agent.start_worker()
        snapshot = rig.refresh()
        recovered = next(task for task in snapshot.tasks if task.id == "old-canvas-task")
        assert recovered.state == Completed("The old saved result", ())
        assert not snapshot.submission_problems
        rig.agent.watch(())
        assert all(task.id != "old-canvas-task" for task in rig.refresh().tasks)
    finally:
        rig.close()


def test_stop_remains_available_when_ordinary_input_capacity_is_full(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path, start=False)
    try:
        for index in range(256):
            rig.agent.submit_identified(f"accepted-{index:03}", f"Accepted request {index}")
        with TestCase().assertRaisesRegex(ValueError, "queue is full"):
            rig.agent.submit_identified("over-capacity", "This request was not accepted")
        for _ in range(10):
            rig.agent.stop()
        rig.agent.close()
        assert rig.agent.wait_closed(10)
        tasks = rig.tasks.list_tasks(limit=1_000)
        assert len(tasks) == 256 and all(task.state is TaskStatus.CANCELLED for task in tasks)
        assert rig.tasks.get("over-capacity") is None
    finally:
        rig.close()


def test_stop_ordering_preserves_new_requests_accepted_after_stop(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path, start=False)
    try:
        rig.agent.submit_identified("before-stop", "Cancel this request")
        rig.agent.stop()
        rig.agent.submit_identified("after-stop", "A new request after Stop")
        rig.agent.close()
        assert rig.agent.wait_closed(5)
        before, after = rig.tasks.get("before-stop"), rig.tasks.get("after-stop")
        assert before is not None and before.state is TaskStatus.CANCELLED
        assert after is not None and after.state is TaskStatus.READY
    finally:
        rig.close()


def test_a_failed_answer_commit_restores_the_exact_question_for_a_new_reply(tmp_path: Path) -> None:
    gate = PollHold()
    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_poll=gate.poll))
    try:
        rig.agent.submit_identified("question-task", "Choose the workbook")
        rig.refresh()
        claim = rig.tasks.claim("worker", "question-run", at=rig.clock())
        assert claim is not None
        rig.tasks.finish(claim.run, Waiting(Question("which-file", "Which file?", "Choose a workbook.")), at=rig.clock())
        rig.refresh()
        with unavailable_journal(rig, gate):
            assert rig.panel.submit("First answer could not commit")
            assert rig.agent.refresh().wait(5)
            assert not has_pending_input(rig.panel)
        rig.refresh()
        question = rig.panel.state.question
        assert has_pending_input(rig.panel) and isinstance(question, agent_session.Question)
        assert question.request_id == "which-file"
        assert rig.panel.submit("Use the revenue workbook")
        rig.refresh()
        assert [response.reply for response in rig.tasks.responses("question-task")] == [TextAnswer("Use the revenue workbook")]
        assert not has_pending_input(rig.panel)
    finally:
        rig.close()


def test_a_later_confirmed_answer_is_not_reopened_after_a_failed_observation(tmp_path: Path) -> None:
    gate = PollHold()
    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_poll=gate.poll))
    try:
        rig.agent.submit_identified("question-task", "Choose a year")
        rig.refresh()
        claim = rig.tasks.claim("worker", "question-run", at=rig.clock())
        assert claim is not None
        rig.tasks.finish(claim.run, Waiting(Question("which-year", "Which year?", "")), at=rig.clock())
        rig.refresh()
        with unavailable_journal(rig, gate):
            assert rig.panel.submit("2026")
            assert rig.agent.refresh().wait(5)
        rig.tasks.answer("question-task", "which-year", TextAnswer("2026"), at=rig.clock())
        rig.refresh()
        assert not rig.panel.waiting and not rig.panel.respond("which-year", "A duplicate")
        assert [response.reply for response in rig.tasks.responses("question-task")] == [TextAnswer("2026")]
    finally:
        rig.close()
