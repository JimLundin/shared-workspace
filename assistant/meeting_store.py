"""A durable, bounded queue of audio and an atomically refreshed Markdown transcript.

Text replaces its audio in one SQLite transaction. Freed audio pages are erased
and reclaimed; completed meetings retain text and timing, not a full recording.
"""

import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, cast

from .llm import RATE
from .pcm import quiet_tail
from .recording_state import AudioSource, Batch, StoredState, duration, source_name

MAX_PENDING_BYTES: Final = 256 * 1024 * 1024
MIN_CHUNK_SECONDS: Final = 20
MAX_CHUNK_SECONDS: Final = 30


def _replace_transcript(temporary: Path, transcript: Path) -> None:
    # Ordinary Windows readers often omit FILE_SHARE_DELETE. Their short-lived
    # handles must not turn an otherwise successful transcription into a failure.
    deadline = time.monotonic() + 2
    while True:
        try:
            temporary.replace(transcript)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


class _Cursor(Protocol):
    def fetchone(self) -> tuple[object, ...] | None: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...
    def __iter__(self) -> Iterator[tuple[object, ...]]: ...


class _Connection(Protocol):
    def execute(self, sql: str, parameters: Sequence[object] = ()) -> _Cursor: ...
    def executescript(self, sql: str) -> _Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


def _integer(value: object) -> int:
    if not isinstance(value, int):
        raise ValueError("Invalid meeting timing in the local journal.")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid text in the local meeting journal.")
    return value


class BacklogFullError(OSError):
    """The last block was saved, but capture must stop before storing another."""


class MeetingStore:
    __slots__ = ("folder", "max_pending_bytes", "publish")

    def __init__(
        self, folder: Path, *, max_pending_bytes: int = MAX_PENDING_BYTES, publish: Callable[[Path, Path], None] = _replace_transcript,
    ) -> None:
        self.folder = folder
        self.max_pending_bytes = max_pending_bytes
        self.publish = publish

    @property
    def transcript(self) -> Path:
        return self.folder / "transcript.md"

    @contextmanager
    def connection(self, *, writing: bool = False) -> Generator[_Connection]:
        # SQLite's stubs expose Any for query results. Narrow to object at this
        # boundary and validate every column before it enters the domain model.
        connection = cast(_Connection, sqlite3.connect(self.folder / "session.sqlite3", timeout=2, isolation_level=None))
        try:
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("PRAGMA synchronous=FULL")
            if writing:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if writing:
                connection.commit()
        except BaseException:
            if writing:
                connection.rollback()
            raise
        finally:
            connection.close()

    @classmethod
    def create(
        cls, root: Path, source: AudioSource, title: str = "Meeting", *, max_pending_bytes: int = MAX_PENDING_BYTES,
        publish: Callable[[Path, Path], None] = _replace_transcript,
    ) -> MeetingStore:
        now = time.time()
        folder = root / (datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H-%M-%S_") + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True)
        store = cls(folder, max_pending_bytes=max_pending_bytes, publish=publish)
        with store.connection() as connection:
            connection.executescript("""
                PRAGMA auto_vacuum=FULL;
                CREATE TABLE meeting (id INTEGER PRIMARY KEY CHECK (id=1), title TEXT NOT NULL, source TEXT NOT NULL,
                    started REAL NOT NULL, frames INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0,
                    finished INTEGER NOT NULL DEFAULT 0, problem TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0, exported_revision INTEGER NOT NULL DEFAULT -1);
                CREATE TABLE audio (start INTEGER PRIMARY KEY, frames INTEGER NOT NULL, quiet INTEGER NOT NULL, pcm BLOB NOT NULL);
                CREATE TABLE segments (start INTEGER PRIMARY KEY, end INTEGER NOT NULL, text TEXT NOT NULL);
            """)
            connection.execute("INSERT INTO meeting (id, title, source, started) VALUES (1, ?, ?, ?)",
                               (" ".join(title.split())[:160] or "Meeting", source, now))
        store.export()
        return store

    def state(self) -> StoredState:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT title, source, started, frames, pending, finished, problem, revision, exported_revision FROM meeting WHERE id=1",
            ).fetchone()
        if row is None or row[1] not in ("both", "microphone") or not isinstance(row[2], (int, float)):
            raise ValueError("The local meeting journal is incomplete.")
        return StoredState(_text(row[0]), row[1], float(row[2]), _integer(row[3]), _integer(row[4]),
                           bool(row[5]), _text(row[6]), _integer(row[7]), _integer(row[8]))

    def append(self, pcm: bytes) -> None:
        if not pcm:
            return
        if len(pcm) % 2 or len(pcm) > RATE * 2:
            raise ValueError("Meeting audio must be written in blocks of at most one second.")
        frames = len(pcm) // 2
        with self.connection(writing=True) as connection:
            row = connection.execute("SELECT frames, finished FROM meeting WHERE id=1").fetchone()
            if row is None or row[1]:
                raise ValueError("Cannot append audio to a finished meeting.")
            connection.execute("INSERT INTO audio (start, frames, quiet, pcm) VALUES (?, ?, ?, ?)",
                               (_integer(row[0]), frames, int(quiet_tail(pcm)), pcm))
            connection.execute("UPDATE meeting SET frames=frames+?, pending=pending+?, revision=revision+1 WHERE id=1", (frames, frames))
            pending = connection.execute("SELECT pending FROM meeting WHERE id=1").fetchone()
        if pending is not None and _integer(pending[0]) * 2 >= self.max_pending_bytes:
            raise BacklogFullError("Recording stopped because untranscribed audio reached its storage limit. Saved audio can be retried.")

    def finish_capture(self, problem: str = "") -> None:
        with self.connection(writing=True) as connection:
            connection.execute("UPDATE meeting SET finished=1, problem=?, revision=revision+1 WHERE id=1", (problem,))

    def batch(self) -> Batch | None:
        with self.connection() as connection:
            # Capture may append its tail and finish between these queries.
            # One read snapshot keeps the completion flag with the same audio.
            connection.execute("BEGIN")
            rows = connection.execute("SELECT start, frames, quiet, pcm FROM audio ORDER BY start LIMIT ?", (MAX_CHUNK_SECONDS,)).fetchall()
            complete = connection.execute("SELECT finished FROM meeting WHERE id=1").fetchone()
            prior = connection.execute("SELECT text FROM segments WHERE text!='' ORDER BY start DESC LIMIT 1").fetchone()
            connection.commit()
        if not rows:
            return None
        frames, parts = 0, bytearray()
        for row in rows:
            if not isinstance(row[3], bytes) or len(row[3]) != _integer(row[1]) * 2:
                raise ValueError("An audio block in the meeting journal is incomplete.")
            if _integer(row[0]) != _integer(rows[0][0]) + frames:
                raise ValueError("An audio block is missing from the meeting journal.")
            parts.extend(row[3])
            frames += _integer(row[1])
            if frames >= MIN_CHUNK_SECONDS * RATE and (row[2] or frames >= MAX_CHUNK_SECONDS * RATE):
                break
        else:
            if not complete or not complete[0]:
                return None
        return Batch(_integer(rows[0][0]), bytes(parts), _text(prior[0])[-600:] if prior else "")

    def complete(self, batch: Batch, text: str) -> None:
        with self.connection(writing=True) as connection:
            # Retrying after a write/export failure must not duplicate a segment
            # or decrement the pending count a second time.
            if connection.execute("SELECT start FROM segments WHERE start=?", (batch.start,)).fetchone() is not None:
                return
            connection.execute("INSERT INTO segments (start, end, text) VALUES (?, ?, ?)", (batch.start, batch.end, text.strip()))
            connection.execute("DELETE FROM audio WHERE start>=? AND start<?", (batch.start, batch.end))
            connection.execute("UPDATE meeting SET pending=pending-?, revision=revision+1 WHERE id=1", (len(batch.pcm) // 2,))

    def preview(self) -> str:
        with self.connection() as connection:
            rows = connection.execute("SELECT start, end, text FROM segments WHERE text!='' ORDER BY start DESC LIMIT 8").fetchall()
        return "\n\n".join(f"**{duration(_integer(row[0]) / RATE)}**  {_text(row[2])[:2000]}" for row in reversed(rows))

    def export(self) -> None:
        temporary = self.transcript.with_suffix(".tmp")
        state = self.state()
        if state.revision == state.exported_revision and self.transcript.is_file():
            return  # preserve corrections the user made to a completed transcript
        status = "Recording" if not state.finished else "Transcription pending" if state.pending else "Saved"
        # The transcription worker owns export after creation, so one atomic
        # replacement is sufficient; capture only writes to the SQLite journal.
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as output, self.connection() as connection:
                output.write(f"# {state.title}\n\n{datetime.fromtimestamp(state.started).isoformat(timespec='seconds')}\n\n")
                output.write(f"{source_name(state.source)} · {duration(state.frames / RATE)} captured · {status}\n\n")
                if state.problem:
                    output.write(f"> {state.problem}\n\n")
                for row in connection.execute("SELECT start, end, text FROM segments ORDER BY start"):
                    if text := _text(row[2]):
                        output.write(f"## {duration(_integer(row[0]) / RATE)} - {duration(_integer(row[1]) / RATE)}\n\n{text}\n\n")
                if state.pending:
                    output.write(f"*{duration(state.pending / RATE)} of audio is saved locally and awaiting transcription.*\n")
                output.flush()
                os.fsync(output.fileno())
            self.publish(temporary, self.transcript)
            with self.connection(writing=True) as connection:
                connection.execute("UPDATE meeting SET exported_revision=? WHERE id=1", (state.revision,))
        finally:
            temporary.unlink(missing_ok=True)


class MeetingArchive:
    """Create and reopen journals with the same storage and publication policy."""

    __slots__ = ("max_pending_bytes", "publish", "root")

    def __init__(
        self, root: Path, *, max_pending_bytes: int = MAX_PENDING_BYTES, publish: Callable[[Path, Path], None] = _replace_transcript,
    ) -> None:
        self.root, self.max_pending_bytes, self.publish = root, max_pending_bytes, publish

    def create(self, source: AudioSource, title: str = "Meeting") -> MeetingStore:
        return MeetingStore.create(self.root, source, title, max_pending_bytes=self.max_pending_bytes, publish=self.publish)

    def open(self, folder: Path) -> MeetingStore:
        return MeetingStore(folder, max_pending_bytes=self.max_pending_bytes, publish=self.publish)

    def journals(self) -> tuple[Path, ...]:
        return tuple(sorted(self.root.glob("*/session.sqlite3")))
