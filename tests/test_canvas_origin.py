"""Source identity changes invalidate derived work even when its text is equal."""

from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from assistant import canvas_model as m
from assistant.canvas_store import CanvasStore, evidence_data, evidence_of, origin_data, origin_of
from assistant.page_source_values import BrowserKind, Complete, ObservedPage, PageReference


def test_origin_change_invalidates_descendants_without_replacing_their_snapshots() -> None:
    original = m.Item("brief", "Launch brief", m.Source(m.WebPage("https://example.invalid/original"), "Budget: 200000", 10))
    canvas = m.select(m.add(m.Canvas(), original), original.id)
    canvas, request = m.request(canvas, "first-run", "comparison", "Compare this source")
    canvas = m.accept(canvas, m.Completed(request.id, "The original comparison"))
    canvas = m.draft(canvas, "comparison", "draft")
    canvas = m.edit(canvas, "draft", "The user's revised wording")
    canvas = m.place(canvas, m.Placement("draft", 1100, 300, 510, 430))
    canvas = m.with_space(canvas, replace(canvas.space, camera=m.Camera(80, 40, .8)))
    result_before, draft_before, arrangement = canvas.item("comparison"), canvas.item("draft"), canvas.space

    refreshed_origin = m.WebPage("https://example.invalid/replacement")
    refreshed = m.Item("brief", "Launch brief", m.Source(refreshed_origin, "Budget: 200000", 20))
    changed = m.observe(canvas, refreshed)
    source = changed.item("brief")
    assert source is not None and source.revision == original.revision + 1
    assert m.outdated(changed, "comparison") and m.outdated(changed, "draft")
    assert changed.item("comparison") == result_before and changed.item("draft") == draft_before
    assert changed.space == arrangement

    updating, fresh_request = m.request(changed, "second-run", "comparison", "Compare this source", refresh=True)
    assert fresh_request.sources[0].origin == refreshed_origin
    updated = m.accept(updating, m.Completed(fresh_request.id, "The replacement comparison"))
    assert not m.outdated(updated, "comparison") and m.outdated(updated, "draft")
    assert updated.item("draft") == draft_before and updated.space == arrangement


def test_page_provenance_survives_canvas_and_evidence_roundtrips(tmp_path: Path) -> None:
    page = ObservedPage(PageReference(BrowserKind.EDGE, "binding", "selected", "https://example.invalid/a"),
                        "Page", "Exact\nsource", Complete.MAIN_DOCUMENT, 10)
    origin = m.WebPage(page.reference.url, provenance=page.provenance)
    source = m.Item("page", page.title, m.Source(origin, page.text, page.observed_at))
    canvas = m.select(m.add(m.Canvas(), source), source.id)
    canvas, request = m.request(canvas, "run", "result", "Summarize the selected page")
    canvas = m.accept(canvas, m.Completed(request.id, "Captured result"))
    canvas = m.edit(m.draft(canvas, "result", "draft"), "draft", "User's wording")
    store = CanvasStore(tmp_path / "canvas.json")
    store.save(canvas)
    restored = store.load()
    assert restored == canvas
    assert origin_of(origin_data(origin)) == origin
    assert evidence_of(evidence_data(request.sources[0])) == request.sources[0]
    assert request.sources[0].origin == origin


def test_page_provenance_rejects_tampered_source_evidence_and_stored_content(tmp_path: Path) -> None:
    page = ObservedPage(PageReference(BrowserKind.EDGE, "binding", "selected", "https://example.invalid/a"),
                        "Page", "Original source", Complete.MAIN_DOCUMENT, 10)
    origin = m.WebPage(page.reference.url, provenance=page.provenance)
    with TestCase().assertRaises(ValueError):
        m.WebPage("https://example.invalid/another", provenance=page.provenance)
    with TestCase().assertRaises(ValueError):
        m.Source(origin, "Changed source", 20)
    with TestCase().assertRaises(ValueError):
        m.Evidence("page", 1, "Page", "Changed evidence", origin)
    evidence = m.Evidence("page", 1, "Page", page.text, origin)
    with TestCase().assertRaises(ValueError):
        evidence_of({**evidence_data(evidence), "text": "Changed snapshot"})
    path = tmp_path / "canvas.json"
    store = CanvasStore(path)
    store.save(m.add(m.Canvas(), m.Item("page", "Page", m.Source(origin, page.text, 10))))
    path.write_bytes(path.read_bytes().replace(b"Original source", b"Tampered source"))
    with TestCase().assertRaises(ValueError):
        store.load()


def test_legacy_page_origins_keep_their_original_canonical_encoding() -> None:
    legacy = m.WebPage("https://example.invalid/legacy")
    assert origin_data(legacy) == {"kind": "web", "url": legacy.url}
    assert origin_of(origin_data(legacy)) == legacy
