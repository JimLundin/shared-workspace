"""Captured canvas requests through real journals, files, and source migration."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import TestCase

from assistant import canvas_model as m
from assistant.canvas_requests import CanvasRequests, PreparedCanvas, context_prompt, request_data
from assistant.page_source_values import BrowserKind, Complete, Excerpt, ObservedPage, PageReference
from automation.store import TaskStore
from automation.task import CanvasSource, RequestSource, RoutineSource


def selected_request(identity: str = "canvas-task") -> m.Request:
    return m.Request(identity, "space", "result", "Summarize the selected observations.", (
        m.Evidence("note", 1, "A note", "Exact captured evidence. Åäö.\r\nFinal line.\n", m.LocalSource.NOTE),
        m.Evidence("window", 2, "Report", "A captured window excerpt.", m.LiveWindow(123, 456, "writer.exe", "Report")),
    ))


def test_saved_request_keeps_exact_evidence_and_first_preparation_without_native_handles(tmp_path: Path) -> None:
    root = tmp_path / "requests"
    requests = CanvasRequests(root)
    assert requests.get("missing") is None and requests.list() == () and not root.exists()
    prepared = requests.prepare(selected_request(), at=10)
    assert requests.prepare(selected_request(), at=900) == prepared
    reopened = CanvasRequests(root)
    recovered = reopened.get(prepared.task_id)
    assert recovered == prepared and reopened.list() == (prepared,)
    assert recovered is not None and recovered.request.sources[0].text == selected_request().sources[0].text
    assert recovered.request.sources[1].origin == m.SavedWindow("writer.exe", "Report")
    assert "Exact captured evidence." in recovered.prompt
    tasks = TaskStore(tmp_path / "work.sqlite3")
    task = tasks.submit(prepared.task_id, "Canvas result", prepared.prompt, prepared.source, at=11)
    assert TaskStore(tasks.path).get(task.id) == task


def test_same_identity_cannot_acquire_different_source_versions_order_or_prompt(tmp_path: Path) -> None:
    requests = CanvasRequests(tmp_path / "requests")
    original = selected_request()
    prepared = requests.prepare(original, at=1)
    variants = (
        replace(original, prompt="Use a different instruction."),
        replace(original, sources=tuple(reversed(original.sources))),
        replace(original, sources=(replace(original.sources[0], text="Replaced evidence."), original.sources[1])),
        replace(original, sources=(replace(original.sources[0], revision=2), original.sources[1])),
        replace(original, space_id="another-space"),
    )
    for request in variants:
        with TestCase().assertRaisesRegex(ValueError, "different preparation"):
            requests.prepare(request, at=2)
        assert requests.get(prepared.task_id) == prepared


def test_concurrent_preparations_share_the_first_committed_request(tmp_path: Path) -> None:
    root = tmp_path / "requests"

    def prepare(at: int) -> PreparedCanvas:
        return CanvasRequests(root).prepare(selected_request(), at=at)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(prepare, (10, 20)))
    assert results[0] == results[1] and results[0].at in (10, 20)
    assert CanvasRequests(root).list() == (results[0],)


def test_missing_text_and_empty_selection_do_not_read_referenced_content(tmp_path: Path) -> None:
    target = tmp_path / "unread.txt"
    target.write_text("UNREAD-FILE-CONTENT", encoding="utf-8")
    request = m.Request("reference", "space", "result", "Summarize if evidence is available.", (
        m.Evidence("file", 1, "Reference", "", m.FileSource(str(target))),
    ))
    requests = CanvasRequests(tmp_path / "requests")
    prepared = requests.prepare(request, at=1)
    target.unlink()
    assert "UNREAD-FILE-CONTENT" not in prepared.prompt
    assert "content has not been read" in prepared.prompt
    assert requests.get(request.id) == prepared
    empty = requests.prepare(m.Request("empty", "space", "empty-result", "Draft a greeting.", ()), at=2)
    assert "No canvas sources were selected" in empty.prompt
    assert "recorded answers" in context_prompt(empty.request)


def test_prepared_web_evidence_exposes_captured_coverage_and_distinguishes_an_observed_empty_page(tmp_path: Path) -> None:
    requests = CanvasRequests(tmp_path / "requests")
    reference = PageReference(BrowserKind.EDGE, "browser-binding", "tab-id", "https://example.test/report")
    full = ObservedPage(reference, "Report", "Complete document text.", Complete.MAIN_DOCUMENT, 1)
    excerpt = ObservedPage(reference, "Report", "Partial", Excerpt(10, 17, 200), 2)
    empty = ObservedPage(reference, "Empty report", "", Complete.MAIN_DOCUMENT, 3)
    for identity, page in (("complete", full), ("partial", excerpt), ("empty-page", empty)):
        request = m.Request(identity, "space", identity + "-result", "Summarize the available page evidence.", (
            m.Evidence("page", 1, page.title, page.text, m.WebPage(reference.url, provenance=page.provenance)),
        ))
        prepared = requests.prepare(request, at=4)
        recovered = CanvasRequests(requests.path.parent).get(identity)
        assert recovered == prepared and page.provenance.version in prepared.prompt
    full_request = requests.get("complete")
    partial_request = requests.get("partial")
    empty_request = requests.get("empty-page")
    assert full_request is not None and "complete main document text" in full_request.prompt
    assert partial_request is not None and "excerpt only" in partial_request.prompt
    assert "positions 10 through 17 (exclusive) of 200" in partial_request.prompt
    assert "uncaptured page text is unavailable" in partial_request.prompt
    assert empty_request is not None and "captured main document contains no text" in empty_request.prompt
    assert "content has not been read" not in empty_request.prompt


def test_invalid_or_corrupted_preparations_fail_before_returning_a_source_scope(tmp_path: Path) -> None:
    requests = CanvasRequests(tmp_path / "requests")
    with TestCase().assertRaisesRegex(ValueError, "preparation time"):
        requests.prepare(selected_request(), at=float("nan"))
    invalid = replace(selected_request(), sources=(replace(selected_request().sources[0], revision=True),))
    with TestCase().assertRaises(ValueError):
        requests.prepare(invalid, at=1)
    assert not requests.path.exists()
    prepared = requests.prepare(selected_request(), at=1)
    modified = replace(prepared.request, sources=(replace(prepared.request.sources[0], text="UNSELECTED SUBSTITUTE"),))
    with closing(sqlite3.connect(requests.path)) as database, database:
        database.execute("UPDATE canvas_requests SET request_snapshot=? WHERE task_id=?",
                         (json.dumps(request_data(modified)), prepared.task_id))
    with TestCase().assertRaisesRegex(ValueError, "do not match"):
        requests.get(prepared.task_id)
    with TestCase().assertRaisesRegex(ValueError, "do not match"):
        requests.list()


def test_future_preparation_fields_and_store_versions_are_rejected_without_overwrite(tmp_path: Path) -> None:
    requests = CanvasRequests(tmp_path / "requests")
    prepared = requests.prepare(selected_request(), at=1)
    payload = request_data(prepared.request)
    payload["future_scope"] = "ambient"
    with closing(sqlite3.connect(requests.path)) as database, database:
        database.execute("UPDATE canvas_requests SET request_snapshot=?", (json.dumps(payload),))
    with TestCase().assertRaisesRegex(ValueError, "unsupported fields"):
        requests.get(prepared.task_id)
    with closing(sqlite3.connect(requests.path)) as database, database:
        database.execute("PRAGMA user_version=99")
    before = requests.path.read_bytes()
    with TestCase().assertRaisesRegex(ValueError, "store version"):
        requests.prepare(selected_request("another"), at=2)
    assert requests.path.read_bytes() == before


def test_work_schema_upgrade_preserves_existing_sources_and_rejects_future_alternatives(tmp_path: Path) -> None:
    path = tmp_path / "work.sqlite3"
    tasks = TaskStore(path)
    ordinary = tasks.submit("ordinary", "Ordinary", "Continue this conversation.", RequestSource("chat"), at=1)
    routine = tasks.submit("routine", "Routine", "Prepare the briefing.", RoutineSource("brief", 1, "event"), at=2)
    with closing(sqlite3.connect(path)) as database, database:
        # Version 1 used this exact table layout, before canvas sources existed.
        database.execute("PRAGMA user_version=1")
    reopened = TaskStore(path)
    assert reopened.get(ordinary.id) == ordinary and reopened.get(routine.id) == routine
    with closing(sqlite3.connect(path)) as database:
        version = cast(tuple[object, ...] | None, database.execute("PRAGMA user_version").fetchone())
        assert version == (2,)
    prepared = CanvasRequests(tmp_path / "requests").prepare(selected_request(), at=3)
    scoped = reopened.submit(prepared.task_id, "Canvas", prepared.prompt, prepared.source, at=3)
    assert TaskStore(path).get(scoped.id) == scoped and isinstance(scoped.source, CanvasSource)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("UPDATE tasks SET source=? WHERE id=?", ('{"kind":"future","fingerprint":"' + "a" * 64 + '"}', scoped.id))
    with TestCase().assertRaisesRegex(ValueError, "stored task source"):
        reopened.get(scoped.id)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with TestCase().assertRaisesRegex(ValueError, "schema version"):
        TaskStore(path)
    assert path.read_bytes() == before
