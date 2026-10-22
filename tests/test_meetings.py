"""Durable meeting transcripts with generated PCM and a controlled endpoint."""

import io
import sqlite3
import threading
import time
import wave
from array import array
from collections.abc import Callable
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from assistant.llm import RATE, Failure
from assistant.meeting_store import MAX_PENDING_BYTES, BacklogFullError, MeetingStore
from assistant.meetings import Meetings
from assistant.recording_state import AudioSource, RecordingState

from .fake_endpoint import Received, Reply, fake_endpoint
from .test_llm import settings

SPEECH = array("h", [1400] * RATE).tobytes()


def wait_for(predicate: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "The meeting worker did not reach the expected state."
        time.sleep(0.01)


class GeneratedCapture:
    __slots__ = ("blocks", "finished", "hold", "opened", "tail")

    def __init__(self, blocks: int = 31, tail: bytes = b"", *, hold: bool = False) -> None:
        self.blocks, self.tail, self.hold = blocks, tail, hold
        self.opened, self.finished = threading.Event(), threading.Event()

    def record(self, stop: threading.Event, consume: Callable[[bytes], None], ready: Callable[[], None]) -> None:
        self.opened.set()
        ready()
        for _ in range(self.blocks):
            if stop.is_set():
                break
            consume(SPEECH)
        if self.hold:
            stop.wait(5)
        consume(self.tail)
        self.finished.set()


def test_journal_orders_chunks_prefers_pauses_and_flushes_the_final_partial_second(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "both", "Planning")
    for _ in range(22):
        store.append(SPEECH)
    assert store.batch() is None
    store.append(bytes(RATE * 2))
    first = store.batch()
    assert first is not None and len(first.pcm) == RATE * 2 * 23
    store.complete(first, "First part with åäö.")
    store.complete(first, "A retry must not duplicate this.")
    tail = SPEECH[:2468]
    store.append(tail)
    assert store.batch() is None
    store.finish_capture()
    last = store.batch()
    assert last is not None and last.start == 23 * RATE and last.pcm == tail
    assert last.context == "First part with åäö."
    store.complete(last, "The last sentence.")
    store.export()
    transcript = store.transcript.read_text(encoding="utf-8")
    assert transcript.count("First part") == 1 and "retry must not" not in transcript
    assert "00:00:00 - 00:00:23" in transcript and "The last sentence." in transcript
    assert store.state().pending == 0 and store.batch() is None
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audio").fetchone() == (0,)
        assert connection.execute("PRAGMA auto_vacuum").fetchone() == (1,)
    assert (store.folder / "session.sqlite3").stat().st_size < len(first.pcm) // 4
    assert SPEECH[:512] not in (store.folder / "session.sqlite3").read_bytes()


def test_batch_reads_capture_completion_and_audio_from_one_snapshot(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "microphone")
    store.append(SPEECH)
    tail = SPEECH[:RATE]
    connect = sqlite3.connect
    attempted = appended = finished = False
    errors: list[str] = []

    def finish() -> None:
        nonlocal appended, finished
        if not appended:
            store.append(tail)
            appended = True
        store.finish_capture()
        finished = True

    def interleave(statement: str) -> None:
        nonlocal attempted
        if statement == "SELECT finished FROM meeting WHERE id=1" and not attempted:
            attempted = True
            try:
                finish()
            except sqlite3.OperationalError as error:
                # A reader snapshot may defer the writer; retry after it closes.
                if "locked" not in str(error):
                    errors.append(str(error))

    def connection(database: Path, *, timeout: float, isolation_level: None) -> sqlite3.Connection:
        native = connect(database, timeout=0, isolation_level=isolation_level)
        native.set_trace_callback(interleave)
        return native

    with patch("assistant.meeting_store.sqlite3.connect", new=connection):
        batch = store.batch()
    assert attempted and not errors
    assert batch is None or batch.pcm == SPEECH + tail, "A finished capture was paired with audio read before its final block."
    if not finished:
        finish()
    final = store.batch()
    assert final is not None and final.pcm == SPEECH + tail
    store.complete(final, "Every word, including the final sentence.")
    assert store.state().pending == 0


def test_pending_audio_is_bounded_and_the_last_saved_block_is_recoverable(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "microphone", max_pending_bytes=len(SPEECH) * 2)
    store.append(SPEECH)
    with TestCase().assertRaises(BacklogFullError):
        store.append(SPEECH)
    store.finish_capture("The pending audio limit was reached.")
    reopened = MeetingStore(store.folder)
    batch = reopened.batch()
    assert batch is not None and batch.pcm == SPEECH * 2
    reopened.complete(batch, "Recovered text.")
    reopened.export()
    assert "Recovered text." in reopened.transcript.read_text(encoding="utf-8")


def test_text_commit_survives_an_interrupted_markdown_export(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "microphone")
    store.append(SPEECH)
    store.finish_capture()
    batch = store.batch()
    assert batch is not None
    store.complete(batch, "The decision is durable.")
    original = store.transcript.read_bytes()
    with patch.object(Path, "replace", side_effect=OSError("fixture: disk unavailable")), TestCase().assertRaises(OSError):
        store.export()
    assert store.transcript.read_bytes() == original
    reopened = MeetingStore(store.folder)
    assert reopened.batch() is None
    reopened.export()
    assert "The decision is durable." in reopened.transcript.read_text(encoding="utf-8")


def test_recovery_does_not_overwrite_edits_to_a_completed_transcript(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "microphone")
    store.append(SPEECH)
    store.finish_capture()
    batch = store.batch()
    assert batch is not None
    store.complete(batch, "Original transcription.")
    store.export()
    store.transcript.write_text("The user's corrected transcript.\n", encoding="utf-8")
    MeetingStore(store.folder).export()
    assert store.transcript.read_text(encoding="utf-8") == "The user's corrected transcript.\n"


def test_export_retries_brief_reader_locks_without_losing_the_old_file(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "microphone")
    original = store.transcript.read_bytes()
    store.append(SPEECH)
    store.finish_capture()
    batch = store.batch()
    assert batch is not None
    store.complete(batch, "Saved after the reader closed.")
    replace = Path.replace
    attempts = 0

    def locked(path: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            assert store.transcript.read_bytes() == original
            raise PermissionError("fixture: a reader is holding the file")
        return replace(path, target)

    with patch.object(Path, "replace", new=locked):
        store.export()
    assert attempts == 3
    assert "Saved after the reader closed." in store.transcript.read_text(encoding="utf-8")


def test_recording_and_transcription_continue_while_the_markdown_file_is_locked(tmp_path: Path) -> None:
    export = MeetingStore.export
    locked = threading.Event()
    locked.set()
    capture = GeneratedCapture(31, hold=True)

    def busy(store: MeetingStore) -> None:
        state = store.state()
        if state.frames and state.pending < state.frames and locked.is_set():
            raise PermissionError("fixture: another app is reading the transcript")
        export(store)

    with fake_endpoint() as endpoint, patch.object(MeetingStore, "export", new=busy):
        service = Meetings(settings(endpoint.url), lambda source: capture, root=tmp_path)
        try:
            service.start()
            wait_for(lambda: "refresh its file" in service.snapshot().detail)
            wait_for(lambda: service.snapshot().seconds == 31)
            assert service.snapshot().capturing and service.snapshot().pending_seconds == 1
            assert "hello there" in service.snapshot().preview
            locked.clear()
            service.stop()
            wait_for(lambda: service.snapshot().phase == "saved")
            assert Path(service.snapshot().path).read_text(encoding="utf-8").count("hello there") == 2
        finally:
            locked.clear()
            service.close()


def test_recording_uses_the_configured_endpoint_and_saves_incremental_text(tmp_path: Path) -> None:
    capture = GeneratedCapture(tail=SPEECH[:1000])
    states: list[RecordingState] = []
    with fake_endpoint() as endpoint:
        service = Meetings(settings(endpoint.url), lambda source: capture, root=tmp_path, on_update=states.append)
        try:
            service.start()
            wait_for(lambda: service.snapshot().phase == "saved")
            requests = endpoint.received
            assert len(requests) == 2
            assert all(request.path.endswith("/audio/transcriptions") for request in requests)
            assert b'name="prompt"\r\n\r\nhello there\r\n' in requests[1].body
            state = service.snapshot()
            assert state.seconds == 31 + 500 / RATE and state.pending_seconds == 0
            assert Path(state.path).read_text(encoding="utf-8").count("hello there") == 2
            assert any(state.phase == "recording" for state in states)
        finally:
            service.close()


def test_a_tiny_final_fragment_is_padded_for_upload_without_changing_recorded_duration(tmp_path: Path) -> None:
    tail = SPEECH[:1000]
    with fake_endpoint() as endpoint:
        service = Meetings(settings(endpoint.url), lambda source: GeneratedCapture(0, tail), root=tmp_path)
        try:
            service.start()
            wait_for(lambda: service.snapshot().phase == "saved")
            body = endpoint.last.body
            with wave.open(io.BytesIO(body[body.index(b"RIFF"):]), "rb") as uploaded:
                assert uploaded.getnframes() == RATE
                audio = uploaded.readframes(RATE)
                assert audio[:len(tail)] == tail and not any(audio[len(tail):])
            assert service.snapshot().seconds == len(tail) / (RATE * 2)
        finally:
            service.close()


def test_slow_transcription_does_not_block_capture_stop_or_shutdown(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    capture = GeneratedCapture(40, tail=SPEECH[:500], hold=True)

    def slow(*args: object, **kwargs: object) -> str:
        entered.set()
        assert release.wait(5)
        return "The late response."

    service = Meetings(settings("https://fixture.invalid/v1"), lambda source: capture, root=tmp_path)
    with patch("assistant.meetings.transcribe", side_effect=slow):
        try:
            service.start()
            assert entered.wait(5)
            wait_for(lambda: service.snapshot().seconds == 40)
            service.close(timeout=0.2)
            store = MeetingStore(Path(service.snapshot().path).parent)
            assert store.state().finished and store.state().frames == RATE * 40 + 250
            assert store.state().pending == store.state().frames
        finally:
            release.set()
            service.close()


def test_restart_recovers_pending_audio_without_opening_the_microphone(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "both")
    store.append(SPEECH)
    opened: list[str] = []
    capture = GeneratedCapture(0)
    exported = threading.Event()

    def create(source: AudioSource) -> GeneratedCapture:
        opened.append(source)
        return capture

    def published(state: RecordingState) -> None:
        if state.phase == "interrupted" and state.pending_seconds == 0 and state.revision == state.exported_revision:
            exported.set()

    with fake_endpoint() as endpoint:
        service = Meetings(settings(endpoint.url), create, root=tmp_path, on_update=published)
        try:
            service.start_worker()
            # Committing text to the journal precedes publishing its file. Wait
            # for the public completion update before opening that file on Windows.
            assert exported.wait(5), service.snapshot()
        finally:
            service.close()
        saved = store.state()
        assert saved.finished and saved.pending == 0 and saved.revision == saved.exported_revision
        assert not opened
        transcript = store.transcript.read_text(encoding="utf-8")
        assert "hello there" in transcript and "capture was not restarted" in transcript


def test_failed_requests_retain_the_audio_and_retry_without_duplicate_text(tmp_path: Path) -> None:
    capture = GeneratedCapture(1)
    calls = 0

    def intermittent(*args: object, **kwargs: object) -> str | Failure:
        nonlocal calls
        calls += 1
        return Failure("fixture: temporarily offline") if calls == 1 else "Recovered after the outage."

    service = Meetings(settings("https://fixture.invalid/v1"), lambda source: capture, root=tmp_path)
    with patch("assistant.meetings.transcribe", side_effect=intermittent):
        try:
            service.start()
            wait_for(lambda: calls == 1)
            assert MeetingStore(Path(service.snapshot().path).parent).state().pending == RATE
            wait_for(lambda: service.snapshot().phase == "saved")
            assert Path(service.snapshot().path).read_text(encoding="utf-8").count("Recovered after the outage.") == 1
            assert calls == 2
        finally:
            service.close()


def test_a_full_backlog_stops_capture_and_saves_every_completed_block(tmp_path: Path) -> None:
    capture = GeneratedCapture(50)
    service = Meetings(settings("https://fixture.invalid/v1"), lambda source: capture, root=tmp_path, max_pending_bytes=RATE * 4)
    with patch("assistant.meetings.transcribe", return_value=Failure("fixture offline")):
        try:
            service.start()
            wait_for(lambda: bool(service.snapshot().detail))
            store = MeetingStore(Path(service.snapshot().path).parent)
            assert store.state().finished and store.state().frames == RATE * 2
            assert "storage limit" in store.state().problem
        finally:
            service.close()


def test_export_failure_can_be_retried_immediately_without_uploading_audio_again(tmp_path: Path) -> None:
    export = MeetingStore.export
    failed = False
    retried = threading.Event()
    capture = GeneratedCapture(1)

    def interrupted(store: MeetingStore) -> None:
        nonlocal failed
        state = store.state()
        if state.finished and state.frames and not state.pending and not failed:
            failed = True
            raise OSError("fixture: interrupted transcript replacement")
        export(store)

    def changed(state: RecordingState) -> None:
        if state.phase == "paused" and not retried.is_set():
            retried.set()
            service.retry()

    with fake_endpoint() as endpoint, patch.object(MeetingStore, "export", new=interrupted):
        service = Meetings(settings(endpoint.url), lambda source: capture, root=tmp_path, on_update=changed)
        try:
            service.start()
            wait_for(lambda: service.snapshot().phase == "saved")
            assert failed and retried.is_set()
            assert len(endpoint.received) == 1
            assert "hello there" in Path(service.snapshot().path).read_text(encoding="utf-8")
        finally:
            service.close()


def test_a_new_meeting_keeps_its_state_when_the_previous_request_finishes(tmp_path: Path) -> None:
    entered, release_response = threading.Event(), threading.Event()
    creating, release_create = threading.Event(), threading.Event()
    create = MeetingStore.create
    requests = 0

    def delayed_create(
        root: Path, source: AudioSource, title: str = "Meeting", *, max_pending_bytes: int = MAX_PENDING_BYTES,
    ) -> MeetingStore:
        if title == "Second":
            creating.set()
            assert release_create.wait(5)
        return create(root, source, title, max_pending_bytes=max_pending_bytes)

    def reply(request: Received) -> Reply:
        nonlocal requests
        requests += 1
        if requests == 1:
            entered.set()
            assert release_response.wait(5)
            return Reply(b'{"text":"First meeting."}')
        return Reply(b'{"text":"Second meeting."}')

    with fake_endpoint() as endpoint, patch.object(MeetingStore, "create", new=delayed_create):
        endpoint.respond = reply
        service = Meetings(settings(endpoint.url), lambda source: GeneratedCapture(1), root=tmp_path)
        try:
            service.start(title="First")
            assert entered.wait(5)
            wait_for(lambda: service.snapshot().phase == "finishing")
            original = MeetingStore(Path(service.snapshot().path).parent)
            service.start(title="Second")
            assert creating.wait(5)
            release_response.set()
            wait_for(lambda: original.state().pending == 0)
            assert service.snapshot().phase == "starting" and service.snapshot().title == "Second"
            release_create.set()
            wait_for(lambda: service.snapshot().phase == "saved")
            assert "Second meeting." in Path(service.snapshot().path).read_text(encoding="utf-8")
            assert "First meeting." in original.transcript.read_text(encoding="utf-8")
        finally:
            release_response.set()
            release_create.set()
            service.close()
