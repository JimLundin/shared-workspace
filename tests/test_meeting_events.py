"""Published transcripts cross into durable work through their public owners."""

import json
import os
import threading
from pathlib import Path
from typing import cast
from unittest import TestCase

from assistant.meeting_events import TranscriptEvents
from assistant.meeting_store import MeetingArchive, MeetingStore
from assistant.meetings import Meetings
from assistant.recording_state import AudioSource, RecordingState
from automation.routines import MEETING_NOTES_ID, RoutineStore, TranscriptReady
from automation.store import TaskStore
from automation.task import RoutineSource

from .test_llm import settings
from .test_meetings import SPEECH, GeneratedCapture


def saved_meeting(archive: MeetingArchive, title: str) -> MeetingStore:
    store = archive.create("microphone", title)
    store.append(SPEECH)
    store.finish_capture()
    batch = store.batch()
    assert batch is not None
    store.complete(batch, "The team decided to ship on Friday.")
    store.export()
    return store


def transcript_payload(prompt: str) -> dict[str, object]:
    # The event section is JSON; Windows path separators are escaped in its text.
    return cast(dict[str, object], json.loads(prompt.splitlines()[-1]))


def test_first_scan_baselines_history_and_new_completions_deliver_once_across_restart(tmp_path: Path) -> None:
    archive = MeetingArchive(tmp_path / "meetings")
    old = saved_meeting(archive, "Historical planning")
    tasks = TaskStore(tmp_path / "work.sqlite3")
    routines = RoutineStore(tasks.path)
    errors: list[str] = []
    events = TranscriptEvents(archive, routines, on_error=errors.append, clock=lambda: 30)
    routines.ensure_meeting_notes(at=10)
    events.recover(archive.journals())
    routines.enable(MEETING_NOTES_ID, at=20)
    events.completed(old)
    assert routines.dispatch(tasks, now=30) == ()

    current = saved_meeting(archive, "Current planning")
    events.completed(current)
    submitted = routines.dispatch(tasks, now=30)
    assert len(submitted) == 1
    task = submitted[0]
    assert isinstance(task.source, RoutineSource)
    assert task.source.routine_id == MEETING_NOTES_ID
    assert task.source.occurrence_id == "transcript:" + current.folder.name
    data = transcript_payload(task.prompt)
    assert data["transcript_path"] == str(current.transcript.absolute())
    assert str(old.transcript.absolute()) not in data.values()
    destination = data["notes_path"]
    assert isinstance(destination, str)
    assert Path(destination) == tasks.path.parent / "work-results" / task.id / "notes.md"

    reopened_tasks = TaskStore(tasks.path)
    reopened_routines = RoutineStore(tasks.path)
    restarted = TranscriptEvents(archive, reopened_routines, on_error=errors.append, clock=lambda: 50)
    restarted.recover(archive.journals())
    restarted.completed(current)
    assert reopened_routines.dispatch(reopened_tasks, now=50) == ()
    assert reopened_tasks.list_tasks() == (task,)
    assert not errors


def test_capture_audio_export_and_existing_file_all_gate_a_completion(tmp_path: Path) -> None:
    archive = MeetingArchive(tmp_path / "meetings")
    tasks = TaskStore(tmp_path / "work.sqlite3")
    routines = RoutineStore(tasks.path)
    errors: list[str] = []
    events = TranscriptEvents(archive, routines, on_error=errors.append, clock=lambda: 30)
    routines.ensure_meeting_notes(at=10)
    events.recover(())
    routines.enable(MEETING_NOTES_ID, at=20)
    store = archive.create("microphone", "Publication gates")

    events.completed(store)
    assert routines.pending(30) == (), "An unfinished capture must not trigger work."
    store.append(SPEECH)
    store.finish_capture()
    store.export()
    events.completed(store)
    assert routines.pending(30) == (), "Saved but untranscribed audio must not trigger work."
    batch = store.batch()
    assert batch is not None
    store.complete(batch, "Ready in the journal, but not the file.")
    events.completed(store)
    assert routines.pending(30) == (), "A stale Markdown file must not trigger work."
    store.export()
    store.transcript.unlink()
    events.completed(store)
    assert routines.pending(30) == (), "Missing transcript output must not trigger work."
    store.export()
    events.completed(store)
    delivered = routines.dispatch(tasks, now=30)
    assert len(delivered) == 1
    assert transcript_payload(delivered[0].prompt)["transcript_path"] == str(store.transcript.absolute())
    assert not errors


def test_startup_repairs_an_export_delivery_gap_without_retiming_old_completions(tmp_path: Path) -> None:
    archive = MeetingArchive(tmp_path / "meetings")
    tasks = TaskStore(tmp_path / "work.sqlite3")
    routines = RoutineStore(tasks.path)
    errors: list[str] = []
    initial = TranscriptEvents(archive, routines, on_error=errors.append)
    routines.ensure_meeting_notes(at=100)
    initial.recover(())
    before_enable = saved_meeting(archive, "Finished before enablement")
    os.utime(before_enable.transcript, (190, 190))
    routines.enable(MEETING_NOTES_ID, at=200)
    after_enable = saved_meeting(archive, "Published before the crash")
    os.utime(after_enable.transcript, (210, 210))

    reopened = RoutineStore(tasks.path)
    restarted = TranscriptEvents(archive, reopened, on_error=errors.append, clock=lambda: 1000)
    restarted.recover(archive.journals())
    delivered = reopened.dispatch(tasks, now=1000)
    assert len(delivered) == 1
    assert isinstance(delivered[0].source, RoutineSource)
    assert delivered[0].source.occurrence_id == "transcript:" + after_enable.folder.name
    assert not errors


def test_unreadable_initial_history_cannot_be_enabled_and_later_scans_continue_past_damage(tmp_path: Path) -> None:
    archive = MeetingArchive(tmp_path / "meetings")
    historical = saved_meeting(archive, "Historical")
    damaged = archive.root / "damaged" / "session.sqlite3"
    damaged.parent.mkdir()
    damaged.write_bytes(b"A damaged SQLite file")
    tasks = TaskStore(tmp_path / "work.sqlite3")
    routines = RoutineStore(tasks.path)
    routines.ensure_meeting_notes(at=10)
    errors: list[str] = []
    events = TranscriptEvents(archive, routines, on_error=errors.append, clock=lambda: 30)
    events.recover(archive.journals())
    with TestCase().assertRaisesRegex(ValueError, "baselined"):
        routines.enable(MEETING_NOTES_ID, at=20)
    assert errors and routines.pending(20) == ()

    events.recover((historical.folder / "session.sqlite3",))
    routines.enable(MEETING_NOTES_ID, at=20)
    new = saved_meeting(archive, "New after initialization")
    os.utime(new.transcript, (30, 30))
    events.recover(archive.journals())
    delivered = routines.dispatch(tasks, now=40)
    assert len(delivered) == 1
    data = transcript_payload(delivered[0].prompt)
    assert data["transcript_path"] == str(new.transcript.absolute())
    assert str(historical.transcript.absolute()) not in data.values()
    assert len(errors) == 2


class UnavailableRoutines(RoutineStore):
    __slots__ = ("unavailable",)

    def __init__(self, path: Path, unavailable: threading.Event) -> None:
        super().__init__(path)
        self.unavailable = unavailable

    def observe_transcript(self, event: TranscriptReady) -> None:
        if self.unavailable.is_set():
            raise OSError("Fixture: work storage is unavailable")
        super().observe_transcript(event)


def test_delivery_failure_leaves_the_recording_saved_and_restart_recovers_it(tmp_path: Path) -> None:
    archive = MeetingArchive(tmp_path / "meetings")
    tasks = TaskStore(tmp_path / "work.sqlite3")
    unavailable, failed, completed = threading.Event(), threading.Event(), threading.Event()
    unavailable.set()
    routines = UnavailableRoutines(tasks.path, unavailable)
    routines.ensure_meeting_notes(at=10)
    routines.baseline_transcripts(())
    routines.enable(MEETING_NOTES_ID, at=20)
    errors: list[str] = []
    calls: list[bytes] = []

    def failure(message: str) -> None:
        errors.append(message)
        failed.set()

    events = TranscriptEvents(archive, routines, on_error=failure, clock=lambda: 30)

    def transcribe(audio: bytes, *, context: str) -> str:
        calls.append(audio)
        return "Saved despite an unavailable work store."

    def complete(store: MeetingStore) -> None:
        events.completed(store)
        completed.set()

    service = Meetings(
        settings("https://fixture.invalid/v1"), lambda source: GeneratedCapture(1), archive=archive,
        transcribe_audio=transcribe, on_recovery=events.recover, on_complete=complete,
    )
    try:
        service.start(title="Delivered after restart")
        assert failed.wait(5) and completed.wait(5)
    finally:
        service.close()
    assert service.snapshot().phase == "saved"
    transcript = Path(service.snapshot().path)
    assert "Saved despite an unavailable work store." in transcript.read_text(encoding="utf-8")
    assert len(calls) == 1 and "work storage is unavailable" in errors[0]
    assert tasks.list_tasks() == ()

    unavailable.clear()
    restarted = TranscriptEvents(archive, RoutineStore(tasks.path), on_error=errors.append)
    restarted.recover(archive.journals())
    delivered = routines.dispatch(tasks, now=transcript.stat().st_mtime + 1)
    assert len(delivered) == 1
    assert transcript_payload(delivered[0].prompt)["transcript_path"] == str(transcript.absolute())


def test_first_baseline_uses_the_startup_archive_while_a_new_capture_finishes(tmp_path: Path) -> None:
    archive = MeetingArchive(tmp_path / "meetings")
    saved_meeting(archive, "Old meeting")
    tasks = TaskStore(tmp_path / "work.sqlite3")
    routines = RoutineStore(tasks.path)
    routines.ensure_meeting_notes(at=10)
    errors: list[str] = []
    events = TranscriptEvents(archive, routines, on_error=errors.append, clock=lambda: 40)
    entered, resume_scan, captured, delivered = (threading.Event() for _ in range(4))

    def recover(journals: tuple[Path, ...]) -> None:
        entered.set()
        assert resume_scan.wait(5)
        events.recover(journals)
        routines.enable(MEETING_NOTES_ID, at=30)

    def changed(state: RecordingState) -> None:
        if state.title == "Captured during startup" and state.phase == "finishing":
            captured.set()

    def complete(store: MeetingStore) -> None:
        events.completed(store)
        delivered.set()

    def transcribe(audio: bytes, *, context: str) -> str:
        return "A new meeting, not historical content."

    service = Meetings(
        settings("https://fixture.invalid/v1"), lambda source: GeneratedCapture(1), archive=archive,
        transcribe_audio=transcribe, on_update=changed, on_recovery=recover, on_complete=complete,
    )
    try:
        service.start(title="Captured during startup")
        assert entered.wait(5) and captured.wait(5)
        resume_scan.set()
        assert delivered.wait(5)
    finally:
        resume_scan.set()
        service.close()
    submitted = routines.dispatch(tasks, now=50)
    assert len(submitted) == 1 and "Captured during startup" in submitted[0].prompt
    assert not errors


def test_an_older_meeting_delivers_while_a_new_capture_owns_the_ui(tmp_path: Path) -> None:
    entering_second, create_second, first_reply, release_reply, first_finished, delivered = (threading.Event() for _ in range(6))

    class DelayedArchive(MeetingArchive):
        __slots__ = ()

        def create(self, source: AudioSource, title: str = "Meeting") -> MeetingStore:
            if title == "Second":
                entering_second.set()
                assert create_second.wait(5)
            return super().create(source, title)

    archive = DelayedArchive(tmp_path / "meetings")
    tasks = TaskStore(tmp_path / "work.sqlite3")
    routines = RoutineStore(tasks.path)
    routines.ensure_meeting_notes(at=10)
    routines.baseline_transcripts(())
    routines.enable(MEETING_NOTES_ID, at=20)
    errors: list[str] = []
    events = TranscriptEvents(archive, routines, on_error=errors.append, clock=lambda: 30)
    transcribed = 0

    def transcribe(audio: bytes, *, context: str) -> str:
        nonlocal transcribed
        transcribed += 1
        if transcribed == 1:
            first_reply.set()
            assert release_reply.wait(5)
            return "First meeting decisions."
        return "Second meeting decisions."

    def changed(state: RecordingState) -> None:
        if state.title == "First" and state.phase == "finishing":
            first_finished.set()

    def complete(store: MeetingStore) -> None:
        events.completed(store)
        if store.state().title == "First":
            delivered.set()

    service = Meetings(
        settings("https://fixture.invalid/v1"), lambda source: GeneratedCapture(1), archive=archive,
        transcribe_audio=transcribe, on_update=changed, on_recovery=events.recover, on_complete=complete,
    )
    try:
        service.start(title="First")
        assert first_reply.wait(5) and first_finished.wait(5)
        first_path = Path(service.snapshot().path)
        service.start(title="Second")
        assert entering_second.wait(5)
        release_reply.set()
        assert delivered.wait(5)
        assert service.snapshot().title == "Second" and service.snapshot().phase == "starting"
        submitted = routines.dispatch(tasks, now=40)
        assert len(submitted) == 1 and "First" in submitted[0].prompt
        assert "First meeting decisions." in first_path.read_text(encoding="utf-8")
    finally:
        release_reply.set()
        create_second.set()
        service.close()
    assert not errors
