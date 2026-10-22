"""Recording publication rules can be checked without devices, a database, or threads."""

from dataclasses import replace

from assistant.llm import RATE
from assistant.recording_state import (
    Batch,
    Exhausted,
    Phase,
    Publication,
    RecordingState,
    Retry,
    RetryPolicy,
    StoredState,
    completion_phase,
    duration,
    published,
)


def test_transcription_retries_back_off_while_capturing_and_pause_after_capture() -> None:
    policy = RetryPolicy()
    assert [policy.failed(count, finished=False) for count in range(7)] == [
        Retry(1, 2), Retry(2, 4), Retry(3, 8), Retry(4, 16), Retry(5, 30), Retry(6, 30), Retry(7, 30),
    ]
    assert policy.failed(0, finished=True) == Retry(1, 2)
    assert policy.failed(1, finished=True) == Retry(2, 4)
    assert policy.failed(2, finished=True) is Exhausted.PAUSED
    assert policy.failed(10, finished=True) is Exhausted.PAUSED


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
    # Revisions, captured frames and transcribed progress must each reject stale data independently.
    for stale in (replace(incoming.saved, revision=9), replace(incoming.saved, frames=RATE * 9, pending=RATE)):
        assert published(current, replace(incoming, saved=stale), stopping=False) is None
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
    started = published(current, incoming, stopping=False)
    assert started is not None and started.phase == "starting"
    recording = published(replace(current, phase="recording"), incoming, stopping=False)
    assert recording is not None and recording.phase == "recording"
    stopping = published(current, incoming, stopping=True, phase="finishing")
    assert stopping is not None and stopping.phase == "stopping" and stopping.control == "stop"
    final = replace(incoming, saved=replace(incoming.saved, finished=True))
    paused = published(stopping, final, stopping=True, paused="Close the transcript to retry", phase="saved")
    assert paused is not None and paused.phase == "paused" and paused.control == "retry"
    assert paused.detail == "Close the transcript to retry"


def test_recording_controls_and_elapsed_labels_follow_capture_progress() -> None:
    initial = RecordingState()
    assert not initial.visible and not initial.capturing and initial.control == "open"
    phases: tuple[tuple[Phase, bool, str], ...] = (
        ("starting", True, "stop"), ("recording", True, "stop"), ("stopping", True, "stop"),
        ("finishing", False, "open"), ("paused", False, "retry"), ("saved", False, "open"), ("interrupted", False, "open"),
    )
    for phase, capturing, control in phases:
        state = replace(initial, phase=phase)
        assert state.visible and state.capturing == capturing and state.control == control
    assert Batch(10, b"\x00\x00\x01\x00").end == 12
    assert [duration(seconds) for seconds in (-1, 0, 59.9, 60, 3599, 3600, 3661)] == [
        "00:00:00", "00:00:00", "00:00:59", "00:01:00", "00:59:59", "01:00:00", "01:01:01",
    ]
