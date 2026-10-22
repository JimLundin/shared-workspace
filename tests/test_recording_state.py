"""Recording publication rules can be checked without devices, a database, or threads."""

from dataclasses import replace

from assistant.llm import RATE
from assistant.recording_state import Publication, RecordingState, StoredState, completion_phase, published


def observation() -> Publication:
    return Publication("meeting-1", "meeting-1/transcript.md", StoredState("Meeting", "both", 0, RATE * 10, RATE * 2, False, "", 10, 8), "Words")


def test_completion_waits_for_pending_audio_and_the_latest_export() -> None:
    saved = replace(observation().saved, finished=True, pending=0, exported_revision=10)
    assert completion_phase(saved) == "saved"
    assert completion_phase(replace(saved, exported_revision=9)) == "finishing"
    assert completion_phase(replace(saved, pending=1)) == "finishing"
    interrupted = replace(saved, problem="Device disconnected")
    assert completion_phase(interrupted) == "interrupted"
    assert completion_phase(replace(interrupted, pending=1)) == "finishing"


def test_publication_rejects_older_progress_and_keeps_capture_finished() -> None:
    incoming = observation()
    initial = RecordingState(identity="meeting-1", phase="recording")
    current = published(initial, incoming, stopping=False)
    assert current is not None and current.seconds == 10 and current.pending_seconds == 2
    assert initial.seconds == 0
    old = replace(incoming, saved=replace(incoming.saved, revision=9, frames=RATE * 9))
    assert published(current, old, stopping=False) is None
    less_transcribed = replace(incoming, saved=replace(incoming.saved, revision=11, pending=RATE * 3))
    assert published(current, less_transcribed, stopping=False) is None
    final = replace(incoming, saved=replace(incoming.saved, revision=11, exported_revision=11, pending=0, finished=True))
    complete = published(current, final, stopping=False)
    assert complete is not None and complete.phase == "saved"
    delayed = replace(final, saved=replace(final.saved, finished=False))
    assert published(complete, delayed, stopping=False) is None
    stale_export = replace(final, saved=replace(final.saved, exported_revision=10))
    assert published(complete, stale_export, stopping=False) is None


def test_capture_reservation_and_pause_take_precedence_over_worker_updates() -> None:
    incoming = observation()
    assert published(RecordingState(phase="starting"), incoming, stopping=False) is None
    assert published(RecordingState(identity="meeting-2"), incoming, stopping=False) is None
    current = RecordingState(identity="meeting-1", phase="starting")
    stopping = published(current, incoming, stopping=True, phase="finishing")
    assert stopping is not None and stopping.phase == "stopping" and stopping.control == "stop"
    final = replace(incoming, saved=replace(incoming.saved, finished=True))
    paused = published(stopping, final, stopping=True, paused="Close the transcript to retry", phase="saved")
    assert paused is not None and paused.phase == "paused" and paused.control == "retry"
    assert paused.detail == "Close the transcript to retry"
