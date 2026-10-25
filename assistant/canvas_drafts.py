"""Verified, unsent mail handoffs from exact canvas draft revisions.

The UI supplies its cached immutable canvas. Acceptance freezes its draft and
source evidence; one bounded worker owns Outlook/file I/O and publication.
The accepted version is durable before OutlookDrafts saves or inspects mail.
Existing OutlookDrafts bindings provide inspection and deduplication on retry.
"""

import contextlib
import hashlib
import json
import os
import queue
import tempfile
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from automation.task import identifier, integer, variant

from . import canvas_model as m
from .canvas_store import CanvasStore, decode, encode, evidence_data, evidence_of
from .outlook import (
    Cancelled,
    DraftConflict,
    DraftContent,
    DraftRequest,
    DraftResult,
    DraftUncertain,
    OutlookDrafts,
    SavedEmlDraft,
    SavedOutlookDraft,
    draft_key,
)

MAX_PENDING = 32
MAX_RECEIPTS = 128
MAX_PREPARED_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class DraftVersion:
    item_id: str
    revision: int
    content: DraftContent
    source: m.Evidence

    def __post_init__(self) -> None:
        identifier(self.item_id, "canvas draft identity")
        integer(self.revision, "canvas draft revision", minimum=1)
        variant(self.content, DraftContent, "canvas draft content")
        variant(self.source, m.Evidence, "canvas draft evidence")
        evidence_of(evidence_data(self.source))


@dataclass(frozen=True, slots=True)
class DraftAttempt:
    attempt_id: str
    draft: DraftVersion

    def __post_init__(self) -> None:
        identifier(self.attempt_id, "canvas draft attempt identity")
        variant(self.draft, DraftVersion, "canvas draft version")


@dataclass(frozen=True, slots=True)
class PendingDraft:
    attempt: DraftAttempt


@dataclass(frozen=True, slots=True)
class SavedDraft:
    attempt: DraftAttempt
    result: SavedOutlookDraft | SavedEmlDraft


@dataclass(frozen=True, slots=True)
class FailedDraft:
    attempt: DraftAttempt
    result: DraftConflict | DraftUncertain | Cancelled


type DraftReceipt = PendingDraft | SavedDraft | FailedDraft


@dataclass(frozen=True, slots=True)
class DraftSnapshot:
    revision: int = 0
    receipts: tuple[DraftReceipt, ...] = ()


def capture(canvas: m.Canvas, item_id: str, expected_revision: int, attempt_id: str) -> DraftAttempt:
    """Choose the exact editable item before any work enters the effect queue."""
    identifier(item_id, "canvas draft identity")
    integer(expected_revision, "expected canvas draft revision", minimum=1)
    identifier(attempt_id, "canvas draft attempt identity")
    variant(canvas, m.Canvas, "canvas observation")
    item = canvas.item(item_id)
    if item is None or not isinstance(item.content, m.Draft):
        raise ValueError("Choose an existing editable canvas draft to save.")
    if item.revision != expected_revision:
        raise ValueError("This canvas draft changed. Review its current revision before saving.")
    return DraftAttempt(attempt_id, DraftVersion(item.id, item.revision, DraftContent(item.title, item.content.text), item.content.source))


def mail_request(draft: DraftVersion) -> DraftRequest:
    """A canvas version keeps one mail identity across attempts and restarts."""
    data = {
        "item": draft.item_id, "revision": draft.revision, "subject": draft.content.subject, "body": draft.content.body,
        "source": evidence_data(draft.source),
    }
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return DraftRequest("canvas:" + draft.item_id, f"{draft.revision}:{fingerprint}", draft.content)


def _canvas(draft: DraftVersion) -> m.Canvas:
    return m.Canvas(items=(m.Item(draft.item_id, draft.content.subject, m.Draft(draft.content.body, draft.source), draft.revision),))


def observed(attempt: DraftAttempt, result: DraftResult) -> SavedDraft | FailedDraft:
    match result:
        case SavedOutlookDraft() | SavedEmlDraft():
            return SavedDraft(attempt, result)
        case DraftConflict() | DraftUncertain() | Cancelled.BEFORE_EFFECT:
            return FailedDraft(attempt, result)
        case _:
            assert_never(result)


def record(snapshot: DraftSnapshot, receipt: DraftReceipt) -> DraftSnapshot:
    """Terminal attempts stay terminal; newer attempts keep separate receipts."""
    previous = next((item for item in snapshot.receipts if item.attempt.attempt_id == receipt.attempt.attempt_id), None)
    if previous is not None:
        if previous.attempt != receipt.attempt:
            raise ValueError("This save attempt identity already belongs to a different canvas draft.")
        if not isinstance(previous, PendingDraft) or previous == receipt:
            return snapshot
        values = tuple(receipt if item.attempt.attempt_id == receipt.attempt.attempt_id else item for item in snapshot.receipts)
    else:
        values = (*snapshot.receipts, receipt)
    while len(values) > MAX_RECEIPTS:
        oldest = next(item for item in values if not isinstance(item, PendingDraft))
        values = tuple(item for item in values if item.attempt.attempt_id != oldest.attempt.attempt_id)
    return DraftSnapshot(snapshot.revision + 1, values)


class CanvasDrafts:
    """The read callback must return cached canvas state without performing I/O.

    ``save_draft`` validates and returns a frozen accepted attempt; it does not
    wait for Outlook. ``snapshot`` includes queued attempts immediately.
    ``publish`` runs only on this owner's worker. Receivers can discard an older
    snapshot revision if they also render a direct snapshot read.
    """

    __slots__ = (
        "_cancel", "_closed", "_closing", "_jobs", "_last_published", "_lock", "_snapshot", "_thread", "_wake",
        "drafts", "publish", "read_canvas", "report_error",
    )

    def __init__(
        self, drafts: OutlookDrafts, read_canvas: Callable[[], m.Canvas], *,
        publish: Callable[[DraftSnapshot], None], report_error: Callable[[str], None],
    ) -> None:
        self.drafts, self.read_canvas, self.publish, self.report_error = drafts, read_canvas, publish, report_error
        self._lock = threading.Lock()
        self._jobs: queue.Queue[DraftAttempt] = queue.Queue(maxsize=MAX_PENDING)
        self._wake, self._cancel, self._closing, self._closed = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event(),
        )
        self._thread: threading.Thread | None = None
        self._snapshot = DraftSnapshot()
        self._last_published = -1

    def start(self) -> None:
        with self._lock:
            if self._thread is None and not self._closing.is_set():
                self._start()

    def _start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="canvas draft handoff", daemon=True)
        self._thread.start()

    def save_draft(self, item_id: str, expected_revision: int, attempt_id: str) -> DraftAttempt:
        with self._lock:
            if self._closing.is_set():
                raise ValueError("Canvas draft saving is closing.")
        attempt = capture(self.read_canvas(), item_id, expected_revision, attempt_id)
        with self._lock:
            if self._closing.is_set():
                raise ValueError("Canvas draft saving is closing.")
            existing = next((item for item in self._snapshot.receipts if item.attempt.attempt_id == attempt_id), None)
            if existing is not None:
                if existing.attempt != attempt:
                    raise ValueError("This save attempt identity already belongs to a different canvas draft.")
                return existing.attempt
            updated = record(self._snapshot, PendingDraft(attempt))
            try:
                self._jobs.put_nowait(attempt)
            except queue.Full:
                raise ValueError("The canvas draft queue is full; wait for a pending save to finish.") from None
            self._snapshot = updated
            self._wake.set()
        return attempt

    def snapshot(self) -> DraftSnapshot:
        with self._lock:
            return self._snapshot

    def prepared(self, key: str) -> DraftVersion | None:
        """Recover an accepted version by mail key; performs bounded I/O off UI callbacks."""
        path = self._preparation_path(key)
        try:
            with path.open("rb") as stored:
                data = stored.read(MAX_PREPARED_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(data) > MAX_PREPARED_BYTES:
            raise ValueError("The saved canvas draft preparation exceeds 1 MB.")
        canvas = decode(data.decode("utf-8"))
        match canvas.items:
            case (m.Item(item_id, title, m.Draft(body, source), revision),):
                draft = DraftVersion(item_id, revision, DraftContent(title, body), source)
            case _:
                raise ValueError("A saved canvas draft preparation must contain exactly one editable draft.")
        if canvas != _canvas(draft) or draft_key(mail_request(draft)) != key:
            raise ValueError("The saved canvas draft preparation differs from its accepted version.")
        return draft

    def _preparation_path(self, key: str) -> Path:
        identifier(key, "canvas draft mail key")
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("A saved canvas draft requires its complete lowercase SHA-256 mail key.")
        return self.drafts.folder / "canvas-versions" / (key + ".json")

    def _prepare(self, draft: DraftVersion) -> None:
        canvas = _canvas(draft)
        data = encode(canvas).encode("utf-8")
        if len(data) > MAX_PREPARED_BYTES:
            raise ValueError("The canvas draft preparation exceeds 1 MB.")
        key = draft_key(mail_request(draft))
        path = self._preparation_path(key)
        prepared = self.prepared(key)
        if prepared is None:
            if self._cancel.is_set():
                raise InterruptedError("Canvas draft saving was cancelled before preparation.")
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".canvas-version-", dir=path.parent) as folder:
                pending = Path(folder) / "version.json"
                CanvasStore(pending).save(canvas)
                if self._cancel.is_set():
                    raise InterruptedError("Canvas draft saving was cancelled before preparation publication.")
                # A complete version is published once; a competing record is
                # verified below, never replaced by this accepted revision.
                with contextlib.suppress(FileExistsError):
                    os.link(pending, path)
            prepared = self.prepared(key)
        if prepared is None or encode(_canvas(prepared)).encode("utf-8") != data:
            raise ValueError("The saved canvas draft preparation differs from its accepted version.")

    def _save(self, draft: DraftVersion) -> DraftResult:
        if self._cancel.is_set():
            return Cancelled.BEFORE_EFFECT
        try:
            self._prepare(draft)
        except InterruptedError:
            return Cancelled.BEFORE_EFFECT
        except Exception:
            self.report_error(traceback.format_exc())
            return DraftUncertain("The accepted canvas revision could not be preserved. No mail save was started.")
        return self.drafts.save(mail_request(draft), self._cancel)

    def close(self) -> None:
        with self._lock:
            if self._closing.is_set():
                return
            self._closing.set()
            self._cancel.set()
            if self._thread is None:
                if self._jobs.empty():
                    self._closed.set()
                else:
                    self._start()
            self._wake.set()

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def _publish(self) -> None:
        snapshot = self.snapshot()
        if snapshot.revision == self._last_published:
            return
        self._last_published = snapshot.revision
        try:
            self.publish(snapshot)
        except Exception:
            self.report_error(traceback.format_exc())

    def _run(self) -> None:
        try:
            while True:
                self._publish()
                try:
                    attempt = self._jobs.get_nowait()
                except queue.Empty:
                    if self._closing.is_set():
                        break
                    self._wake.wait()
                    self._wake.clear()
                    continue
                try:
                    outcome = self._save(attempt.draft)
                except Exception:
                    self.report_error(traceback.format_exc())
                    outcome = DraftUncertain("The draft save could not be confirmed. Retry this exact draft version to inspect its outcome.")
                receipt = observed(attempt, outcome)
                with self._lock:
                    self._snapshot = record(self._snapshot, receipt)
        finally:
            self._closed.set()
