"""Routine behavior through committed transcripts, real files, and reopened SQLite."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast
from unittest import TestCase

from assistant.meeting_store import MeetingStore
from automation.routines import (
    Accepted,
    EventTrigger,
    Every,
    FileChanged,
    FileObservation,
    Once,
    RoutineStore,
    TranscriptReady,
    transcript_ready,
)
from automation.store import TaskStore
from automation.task import Blocked, Completed, Control, RoutineSource, Waiting


def ready_event(store: MeetingStore, at: float) -> TranscriptReady | None:
    saved = store.state()
    return transcript_ready(
        store.folder.name, saved.title, str(store.transcript), finished=saved.finished, pending=saved.pending,
        revision=saved.revision, exported_revision=saved.exported_revision, completed_at=at,
    )


def test_transcript_trigger_waits_for_capture_tail_and_committed_export(tmp_path: Path) -> None:
    routines = RoutineStore(tmp_path / "work.sqlite3")
    routines.baseline_transcripts(())
    routine = routines.ensure_meeting_notes(at=1)
    routines.enable(routine.id, at=2)
    meeting = MeetingStore.create(tmp_path / "meetings", "microphone", "Release review")
    assert ready_event(meeting, 3) is None
    meeting.append(b"\x01\x00" * 97)
    meeting.finish_capture()
    meeting.export()
    assert ready_event(meeting, 4) is None
    batch = meeting.batch()
    assert batch is not None
    meeting.complete(batch, "The final decision: Ada will publish the release notes on Friday.")
    assert ready_event(meeting, 5) is None
    meeting.export()
    event = ready_event(meeting, 6)
    assert event is not None
    routines.observe_transcript(event)
    routines.observe_transcript(event)
    tasks = TaskStore(tmp_path / "work.sqlite3")
    accepted = routines.dispatch(tasks, now=7)
    assert len(accepted) == 1
    task = accepted[0]
    data = cast(dict[str, object], json.loads(task.prompt.splitlines()[-1]))
    assert data["transcript_path"] == str(meeting.transcript)
    destination = data["notes_path"]
    assert isinstance(destination, str)
    assert Path(destination).is_relative_to(tmp_path / "work-results" / task.id)
    assert not Path(destination).is_relative_to(meeting.folder)
    assert "final decision" in meeting.transcript.read_text(encoding="utf-8")
    assert len(tasks.list_tasks()) == 1


def test_initial_transcript_baseline_and_disabled_template_create_no_tasks(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    old = TranscriptReady("old-meeting", "Previous meeting", str(tmp_path / "old.md"), 10)
    routine = routines.ensure_meeting_notes(at=20)
    with TestCase().assertRaises(ValueError):
        routines.enable(routine.id, at=21)
    routines.baseline_transcripts((old,))
    routines.observe_transcript(old)
    assert not routines.dispatch(tasks, now=22)
    routines.enable(routine.id, at=23)
    routines.observe_transcript(old)
    assert not routines.dispatch(tasks, now=24)
    recent = TranscriptReady("new-meeting", "Current meeting", str(tmp_path / "new.md"), 25)
    routines.observe_transcript(recent)
    assert len(routines.dispatch(tasks, now=26)) == 1
    reopened = RoutineStore(path)
    reopened.ensure_meeting_notes(at=27)
    future = TranscriptReady("future-meeting", "Following meeting", str(tmp_path / "future.md"), 28)
    reopened.observe_transcript(future)
    assert len(reopened.pending(29)) == 1
    assert len(tasks.list_tasks()) == 1


def test_recovered_completion_is_delivered_once_after_database_reopen(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    routines.baseline_transcripts(())
    routine = routines.ensure_meeting_notes(at=1)
    routines.enable(routine.id, at=2)
    event = TranscriptReady("meeting-durable", "Planning", str(tmp_path / "transcript.md"), 10)
    # The publisher's export survived, but its live callback did not.
    after_restart = RoutineStore(path)
    after_restart.observe_transcript(event)
    tasks = TaskStore(path)
    first = after_restart.dispatch(tasks, now=11)
    assert len(first) == 1
    tasks.control(first[0].id, Control.CANCEL, at=12)
    for at in (13, 14, 15):
        reopened = RoutineStore(path)
        reopened.observe_transcript(TranscriptReady(event.identity, event.title, event.path, at))
        assert not reopened.dispatch(tasks, now=at)
    assert len(tasks.list_tasks()) == 1


def test_schedule_is_opt_in_and_missed_intervals_coalesce_without_overlapping_work(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routine = routines.create("brief", "Project briefing", "Prepare the project briefing.", Every(100, 60), at=1)
    assert not routines.dispatch(tasks, now=100)
    routines.enable(routine.id, at=100)
    first = routines.dispatch(tasks, now=100)
    assert len(first) == 1
    assert not routines.dispatch(tasks, now=299)
    claim = tasks.claim("worker", "run-brief", at=299)
    assert claim is not None
    tasks.finish(claim.run, Completed("Briefing prepared.", ("Briefing was checked.",)), at=300)
    resumed = RoutineStore(path)
    due = resumed.pending(301)
    assert len(due) == 1 and due[0].due_at == 280
    second = resumed.dispatch(tasks, now=301)
    assert len(second) == 1 and second[0].id != first[0].id
    assert len(tasks.list_tasks()) == 2
    assert not resumed.dispatch(tasks, now=339)
    tasks.control(second[0].id, Control.CANCEL, at=350)
    resumed.disable(routine.id, at=350)
    resumed.enable(routine.id, at=401)
    assert not resumed.dispatch(tasks, now=459)
    assert len(resumed.dispatch(tasks, now=460)) == 1


def test_one_shot_waits_for_its_due_time_and_does_not_repeat_after_reenable(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("once", "One report", "Write the report.", Once(100), at=1)
    routines.enable("once", at=2)
    assert not routines.dispatch(tasks, now=99.99)
    accepted = routines.dispatch(tasks, now=100)
    assert len(accepted) == 1
    tasks.control(accepted[0].id, Control.CANCEL, at=101)
    routines.disable("once", at=102)
    routines.enable("once", at=103)
    assert not RoutineStore(path).dispatch(tasks, now=1000)
    assert len(tasks.list_tasks()) == 1


def test_one_shot_disabled_before_due_runs_once_when_reenabled_after_due(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("once", "One overdue report", "Write the overdue report.", Once(100), at=1)
    routines.enable("once", at=2)
    assert not routines.dispatch(tasks, now=50)
    routines.disable("once", at=60)
    assert not routines.dispatch(tasks, now=100)

    reopened = RoutineStore(path)
    reopened.enable("once", at=110)
    task, = reopened.dispatch(tasks, now=110)
    assert len(tasks.list_tasks()) == 1
    tasks.control(task.id, Control.CANCEL, at=111)

    # A terminal task and another restart must not make the overdue occurrence new.
    after_restart = RoutineStore(path)
    recovered_tasks = TaskStore(path)
    assert not after_restart.dispatch(recovered_tasks, now=200)
    assert len(recovered_tasks.list_tasks()) == 1


def test_one_shot_waits_for_recorded_enablement_after_clock_moves_backward(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("once", "One report", "Write the report.", Once(90), at=1)
    routines.enable("once", at=100)

    reopened = RoutineStore(path)
    assert not reopened.dispatch(tasks, now=95)
    assert not tasks.list_tasks()
    assert len(reopened.dispatch(tasks, now=100)) == 1
    assert len(tasks.list_tasks()) == 1


def test_outbox_retains_the_occurrence_selected_before_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("interval", "Check status", "Check the release status.", Every(100, 60), at=1)
    routines.enable("interval", at=2)
    original = routines.pending(280)
    assert len(original) == 1
    delayed = RoutineStore(path).pending(1000)
    assert len(delayed) == 1 and delayed[0].id == original[0].id
    assert delayed[0].due_at == 280
    accepted = RoutineStore(path).dispatch(tasks, now=1001)
    assert len(accepted) == 1 and accepted[0].source == original[0].source


def test_submitted_waiting_task_recovers_its_outbox_receipt_without_duplication(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("once", "Review", "Review the proposal.", Once(10), at=1)
    routines.enable("once", at=2)
    firing, = routines.pending(10)
    handoff = routines.accept(firing.id, at=10)
    assert handoff is not None
    task = tasks.submit("existing-task", handoff.title, handoff.prompt, handoff.source, at=10)
    claim = tasks.claim("worker", "run-review", at=11)
    assert claim is not None
    tasks.finish(claim.run, Waiting(Blocked("Waiting for the proposal to become available.")), at=12)
    # Submission committed, but the process ended before acknowledge().
    reopened = RoutineStore(path)
    recovered = reopened.dispatch(tasks, now=13)
    assert len(recovered) == 1 and recovered[0].id == task.id
    summary, = reopened.summaries(13)
    assert summary.last_firing is not None and summary.last_firing.task_id == task.id
    assert not reopened.dispatch(tasks, now=14)
    assert len(tasks.list_tasks()) == 1


def test_disable_and_edit_fence_pending_handoffs_but_keep_accepted_work(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("cancel", "Cancelled", "An instruction to withdraw.", Once(10), at=1)
    routines.enable("cancel", at=2)
    pending, = routines.pending(10)
    routines.disable("cancel", at=11)
    assert routines.accept(pending.id, at=12) is None
    assert not routines.dispatch(tasks, now=12)
    routines.create("edit", "Original", "Use the accepted original instruction.", Once(20), at=12)
    routines.enable("edit", at=13)
    original, = routines.pending(20)
    accepted = routines.accept(original.id, at=20)
    assert accepted is not None and isinstance(accepted.state, Accepted)
    routines.update("edit", "Replacement", "Use the replacement instruction.", Once(100), at=21, expected_revision=0)
    routines.disable("edit", at=22)
    recovered = RoutineStore(path).dispatch(tasks, now=23)
    assert len(recovered) == 1
    assert "accepted original instruction" in recovered[0].prompt
    assert "replacement instruction" not in recovered[0].prompt
    assert len(tasks.list_tasks()) == 1


def test_edit_rejects_stale_ui_revision_and_replaces_pending_configuration(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("edited", "Draft", "Review the draft.", Once(10), at=1)
    routines.enable("edited", at=2)
    previous, = routines.pending(10)
    updated = routines.update("edited", "Final", "Review the final document.", Once(20), at=11, expected_revision=0)
    with TestCase().assertRaises(ValueError):
        routines.update("edited", "Stale", "Overwrite the current edit.", Once(30), at=12, expected_revision=0)
    assert routines.accept(previous.id, at=13) is None
    assert not routines.dispatch(tasks, now=19)
    task, = routines.dispatch(tasks, now=20)
    assert isinstance(task.source, RoutineSource)
    assert "final document" in task.prompt and task.source.revision == updated.revision
    assert "Overwrite" not in task.prompt


def test_exact_file_watch_has_a_new_baseline_after_enable_without_reusing_event_ids(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    watched = tmp_path / "report.txt"
    other = tmp_path / "other.txt"
    watched.write_text("Initial report.", encoding="utf-8")
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("watch", "Review changes", "Review the changed report.", FileChanged(str(watched)), at=1)
    routines.enable("watch", at=2)
    routines.scan_files(3)
    other.write_text("This file is unrelated.", encoding="utf-8")
    routines.scan_files(4)
    assert not routines.dispatch(tasks, now=4)
    watched.write_text("A changed report with new decisions.", encoding="utf-8")
    routines.scan_files(5)
    first, = routines.dispatch(tasks, now=5)
    tasks.control(first.id, Control.CANCEL, at=6)
    routines.disable("watch", at=6)
    watched.write_text("Changes while the routine is disabled.", encoding="utf-8")
    routines.scan_files(7)
    routines.enable("watch", at=8)
    routines.scan_files(9)
    assert not routines.dispatch(tasks, now=9)
    watched.write_text("A subsequent enabled change.", encoding="utf-8")
    routines.scan_files(10)
    second, = routines.dispatch(tasks, now=10)
    assert second.id != first.id
    tasks.control(second.id, Control.CANCEL, at=11)
    watched.unlink()
    RoutineStore(path).scan_files(12)
    third, = RoutineStore(path).dispatch(tasks, now=12)
    assert third.id not in (first.id, second.id)
    assert len(tasks.list_tasks()) == 3


def test_out_of_order_file_observations_do_not_turn_old_state_into_new_work(tmp_path: Path) -> None:
    routines = RoutineStore(tmp_path / "work.sqlite3")
    watched = str(tmp_path / "report.txt")
    routines.create("watch", "Watch", "Read the changed file.", FileChanged(watched), at=1)
    routines.enable("watch", at=2)
    routines.observe_file(FileObservation(watched, "initial", 3))
    routines.observe_file(FileObservation(watched, "new", 5))
    routines.observe_file(FileObservation(watched, "old", 4))
    routines.observe_file(FileObservation(watched, "new", 6))
    assert len(routines.pending(6)) == 1


def test_corrupt_saved_trigger_is_rejected_before_work_is_submitted(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    routines = RoutineStore(path)
    tasks = TaskStore(path)
    routines.create("corrupt", "Broken", "This should never run.", Once(10), at=1)
    routines.enable("corrupt", at=2)
    with closing(sqlite3.connect(path)) as external, external:
        external.execute("UPDATE routines SET trigger=? WHERE id=?", ('{"kind":"every","anchor":0,"seconds":true}', "corrupt"))
    with TestCase().assertRaises(ValueError):
        RoutineStore(path).dispatch(tasks, now=20)
    assert not tasks.list_tasks()


def test_routine_controls_and_event_history_survive_independent_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    first = RoutineStore(path)
    first.baseline_transcripts(())
    first.create("meeting", "Actions", "Extract meeting actions.", EventTrigger.TRANSCRIPT_COMPLETED, at=1)
    first.enable("meeting", at=2)
    event = TranscriptReady("shared", "Meeting", str(tmp_path / "shared.md"), 3)
    first.observe_transcript(event)
    second = RoutineStore(path)
    second.observe_transcript(event)
    tasks = TaskStore(path)
    accepted = second.dispatch(tasks, now=4)
    assert len(accepted) == 1
    assert not first.dispatch(tasks, now=4)
    first.disable("meeting", at=5)
    second.observe_transcript(TranscriptReady("later", "Later", str(tmp_path / "later.md"), 6))
    assert not second.dispatch(tasks, now=6)
    assert len(tasks.list_tasks()) == 1
