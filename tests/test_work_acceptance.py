"""A finished meeting becomes verified, durable work through application seams."""

import hashlib
import json
import threading
from pathlib import Path
from typing import cast

from assistant.agent_execution import AgentExecution
from assistant.llm import Settings
from assistant.meeting_events import TranscriptEvents
from assistant.meeting_store import MeetingArchive, MeetingStore
from assistant.meetings import Meetings
from assistant.workspace import Workspace
from automation.controller import ControllerSnapshot, WorkController
from automation.routines import RoutineStore
from automation.store import TaskStore
from automation.task import ActionVerified, Completed, FileDigest, RoutineSource, Task, Waiting

from .fake_endpoint import fake_endpoint
from .test_agent import FakeMachine, FakeWindow, calls, says
from .test_meetings import GeneratedCapture
from .test_workspace import live_tree

TRANSCRIPT = "Decision: ship on Friday. Ada owns the release notes; Lee will review them on Thursday."


def finished_meeting_task(tmp_path: Path, settings: Settings, errors: list[str]) -> tuple[Task, MeetingStore]:
    work_path = tmp_path / "work.sqlite3"
    tasks, archive = TaskStore(work_path), MeetingArchive(tmp_path / "meetings")
    routines = RoutineStore(work_path)
    routine = routines.ensure_meeting_notes(at=10)
    delivered = threading.Event()
    submissions: list[Task] = []

    def dispatch() -> None:
        accepted = routines.dispatch(tasks, now=30)
        if accepted:
            submissions.extend(accepted)
            delivered.set()

    events = TranscriptEvents(archive, routines, on_error=errors.append, on_change=dispatch, clock=lambda: 30)
    events.recover(archive.journals())
    routines.enable(routine.id, at=20)
    def transcribe(audio: bytes, *, context: str) -> str:
        return TRANSCRIPT

    meetings = Meetings(
        settings, lambda source: GeneratedCapture(1), archive=archive, transcribe_audio=transcribe,
        on_recovery=events.recover, on_complete=events.completed,
    )
    try:
        meetings.start(title="Release planning")
        assert delivered.wait(10), errors
    finally:
        meetings.close()

    assert len(submissions) == 1
    task = submissions[0]
    assert isinstance(task.source, RoutineSource) and task.source.routine_id == routine.id
    journals = archive.journals()
    assert len(journals) == 1
    return task, archive.open(journals[0].parent)


def test_finished_meeting_produces_one_verified_notes_file_and_survives_restart(tmp_path: Path) -> None:
    work_path = tmp_path / "work.sqlite3"
    errors: list[str] = []

    with fake_endpoint() as endpoint:
        settings = Settings(endpoint.url, "fixture-key", "transcribe", "chat", "gpt-6-astra", "", "")
        task, meeting = finished_meeting_task(tmp_path, settings, errors)
        transcript = meeting.transcript
        assert TRANSCRIPT in transcript.read_text(encoding="utf-8")

        # These JSON inputs are part of the instruction delivered to the model.
        inputs = cast("dict[str, object]", json.loads(task.prompt.splitlines()[-1]))
        assert inputs["transcript_path"] == str(transcript.absolute())
        destination = inputs["notes_path"]
        assert isinstance(destination, str)
        notes_path = Path(destination)
        assert notes_path.is_relative_to(tmp_path / "work-results" / task.id)
        notes = (
            "# Release planning\n\n"
            f"Source: [meeting transcript](<{transcript.absolute()}>).\n\n"
            "Decision: ship on Friday.\n\n"
            "- Ada: write the release notes.\n"
            "- Lee: review them on Thursday.\n"
        )
        endpoint.replies = [
            calls(("read_file", {"path": str(transcript)})),
            calls(("write_file", {"path": str(notes_path), "content": notes})),
            says("Saved the release decisions and assigned actions, with a link to the source transcript."),
        ]
        machine, log = FakeMachine(), FakeWindow()
        execution = AgentExecution(
            settings, machine, journal=lambda record: None, record=lambda record: None,
            report_error=errors.append, close_background=machine.close_background,
            workspace=Workspace(live_tree(tmp_path / "source"), tmp_path / "workspace"),
        )
        settled = threading.Event()

        def published(snapshot: ControllerSnapshot) -> None:
            if any(item.id == task.id and isinstance(item.state, (Completed, Waiting)) for item in snapshot.tasks):
                settled.set()

        controller = WorkController(
            TaskStore(work_path), execution, owner="acceptance-worker", log=lambda claim: log, now=lambda: 40,
            new_run_id=lambda: "meeting-notes-run", on_change=published, on_error=errors.append,
        )
        controller.start()
        try:
            assert settled.wait(15), errors
        finally:
            controller.shutdown()
            assert controller.wait(5), "All execution resources must be released before reopening the work."

        completed = TaskStore(work_path).get(task.id)
        assert completed is not None and isinstance(completed.state, Completed), (completed, errors)
        assert notes_path.read_text(encoding="utf-8") == notes
        attempted = TaskStore(work_path).actions(task.id)
        assert len(attempted) == 1 and attempted[0].specification.tool == "write_file"
        assert isinstance(attempted[0].outcome, ActionVerified)
        verification = attempted[0].specification.verification
        assert isinstance(verification, FileDigest) and Path(verification.path) == notes_path
        assert hashlib.sha256(notes_path.read_bytes()).hexdigest() == verification.sha256
        assert attempted[0].outcome.evidence in completed.state.evidence
        assert any(TRANSCRIPT in request.body.decode("utf-8") for request in endpoint.received[1:])
        assert len(endpoint.received) == 3

    reopened_tasks, reopened_routines = TaskStore(work_path), RoutineStore(work_path)
    archive = MeetingArchive(tmp_path / "meetings")
    restarted = TranscriptEvents(archive, reopened_routines, on_error=errors.append, clock=lambda: 50)
    restarted.recover(archive.journals())
    restarted.completed(meeting)
    assert reopened_routines.dispatch(reopened_tasks, now=50) == ()
    assert reopened_tasks.list_tasks() == (completed,)
    assert notes_path.read_text(encoding="utf-8") == notes
    assert not errors
