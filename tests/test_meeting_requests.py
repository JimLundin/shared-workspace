"""Saved meeting selections survive retries and process restarts unchanged."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from assistant.meeting_followup import (
    NoCaptureNotice,
    Transcript,
    TranscriptBundle,
    TranscriptSource,
    bundle_data,
)
from assistant.meeting_requests import MeetingRequests, PreparedFollowup, followup_prompt, task_identity


def recorded(folder: Path, name: str = "segment-one", *, content: bytes = b"Alice will send the report.\n") -> Transcript:
    path = folder / name / "transcript.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return Transcript(
        TranscriptSource(name, "Meeting segment", str(path.absolute()), hashlib.sha256(content).hexdigest(), 10, 30, "both", NoCaptureNotice.NONE),
        content,
    )


def test_request_reopens_with_exact_prompt_time_and_sources_after_original_file_is_removed(tmp_path: Path) -> None:
    root = tmp_path / "followups"
    store = MeetingRequests(root)
    assert store.get("missing") is None and store.list() == () and not root.exists()
    transcript = recorded(tmp_path, content="# Réunion\r\n\r\nAlice: I'll send it. 😀\r\n".encode())
    bundle = TranscriptBundle((transcript,))
    prompt = followup_prompt(bundle)
    assert "meeting_save_draft" in prompt and "Never send email." in prompt
    assert "draft_id, and revision returned by meeting_followup_result" in prompt
    assert "actual Outlook or .eml destination" in prompt
    prepared = store.prepare(task_identity(bundle), bundle, prompt, at=100)
    assert prepared == PreparedFollowup(task_identity(bundle), bundle, prompt, 100)
    assert store.prepare(prepared.task_id, bundle, prompt, at=900) == prepared
    Path(transcript.source.path).unlink()
    reopened = MeetingRequests(root)
    recovered = reopened.get(prepared.task_id)
    assert recovered is not None and recovered == prepared and reopened.list() == (prepared,)
    assert recovered.bundle.transcripts[0].content == transcript.content


def test_prepared_identity_rejects_changed_prompt_content_order_or_metadata_without_replacing_binding(tmp_path: Path) -> None:
    store = MeetingRequests(tmp_path / "followups")
    first, second = recorded(tmp_path), recorded(tmp_path, "segment-two")
    bundle = TranscriptBundle((first, second))
    prepared = store.prepare("one-task", bundle, "Original exact prompt", at=100)
    changed = recorded(tmp_path, content=b"Bob will send the invoice.\n")
    cases = (
        (bundle, "A different prompt"),
        (TranscriptBundle((changed, second)), prepared.prompt),
        (TranscriptBundle((second, first)), prepared.prompt),
        (TranscriptBundle((replace(first, source=replace(first.source, title="Changed title")), second)), prepared.prompt),
    )
    for sources, prompt in cases:
        with TestCase().assertRaisesRegex(ValueError, "different preparation"):
            store.prepare(prepared.task_id, sources, prompt, at=200)
        assert store.get(prepared.task_id) == prepared


def test_concurrent_exact_reservations_share_the_first_committed_binding(tmp_path: Path) -> None:
    root = tmp_path / "followups"
    bundle = TranscriptBundle((recorded(tmp_path),))

    def prepare(at: int) -> PreparedFollowup:
        return MeetingRequests(root).prepare("shared-task", bundle, "The same prompt", at=at)

    with ThreadPoolExecutor(max_workers=2) as workers:
        values = tuple(workers.map(prepare, (100, 200)))
    assert values[0] == values[1] and values[0].at in (100, 200)
    assert MeetingRequests(root).list() == (values[0],)


def test_recovery_pages_are_bounded_and_ordered_without_hiding_direct_identity_lookups(tmp_path: Path) -> None:
    store = MeetingRequests(tmp_path / "followups")
    bundle = TranscriptBundle((recorded(tmp_path),))
    old = store.prepare("old", bundle, "Original", at=10)
    newest = store.prepare("new", bundle, "New", at=30)
    middle = store.prepare("middle", bundle, "Middle", at=20)
    assert store.list(limit=2) == (newest, middle) and store.get(old.task_id) == old
    for limit in (0, 129, True):
        with TestCase().assertRaisesRegex(ValueError, "preparation limit"):
            store.list(limit=limit)


def test_invalid_external_requests_and_damaged_source_snapshots_fail_before_submission(tmp_path: Path) -> None:
    store = MeetingRequests(tmp_path / "followups")
    bundle = TranscriptBundle((recorded(tmp_path),))
    with TestCase().assertRaisesRegex(ValueError, "task identity"):
        store.prepare("invalid\0id", bundle, "A prompt", at=1)
    with TestCase().assertRaisesRegex(ValueError, "preparation time"):
        store.prepare("task", bundle, "A prompt", at=float("nan"))
    assert not store.path.exists()
    original = store.prepare("task", bundle, "A prompt", at=1)
    changed = TranscriptBundle((recorded(tmp_path, content=b"A different source observation."),))
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("UPDATE meeting_requests SET sources=? WHERE task_id=?", (json.dumps(bundle_data(changed)), original.task_id))
        connection.commit()
    with TestCase().assertRaisesRegex(ValueError, "do not match"):
        store.get(original.task_id)
    with TestCase().assertRaisesRegex(ValueError, "do not match"):
        store.list()


def test_task_identity_reuses_each_ordered_source_version_and_changes_for_new_evidence(tmp_path: Path) -> None:
    first, second = recorded(tmp_path), recorded(tmp_path, "segment-two")
    original = TranscriptBundle((first, second))
    assert task_identity(original) == task_identity(TranscriptBundle((first, second)))
    assert task_identity(original) != task_identity(TranscriptBundle((second, first)))
    changed = recorded(tmp_path, content=b"A correction to the first recording.")
    assert task_identity(original) != task_identity(TranscriptBundle((changed, second)))
