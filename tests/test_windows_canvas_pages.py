"""NativeResources browser wiring with local CDP fixtures, no personal browser."""

import sys
import unittest
from pathlib import Path
from unittest import TestCase

if sys.platform != "win32":
    raise unittest.SkipTest("The native canvas resource adapter imports WinAPI")

from assistant import canvas_model as m
from assistant.canvas_native import NativeResources
from assistant.canvas_store import CanvasStore
from assistant.page_source_values import Complete

from .test_page_sources import page_browser


def test_native_page_catalogue_keeps_late_tab_ids_and_captures_the_selected_source(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, _reader, peer, sessions):
        for index in range(45):
            peer.add(f"page-{index}", f"https://example.invalid/{index}", f"Page {index}")
        url = "https://example.invalid/" + "long/" * 250
        content = "Selected\n" + "page " * 1_600
        peer.add("selected", url, content)
        resources = NativeResources(browser=browser)
        try:
            pages = resources.connect_pages()
            assert len(pages) == 46 and pages[-1].id == "selected"
            assert len(sessions.opened) == 1
            item = resources.page(pages[-1].id, "canvas-page")
            assert item.id == "canvas-page" and isinstance(item.content, m.Source)
            assert item.content.text == content and isinstance(item.content.origin, m.WebPage)
            provenance = item.content.origin.provenance
            assert provenance is not None and provenance.reference.target_id == "selected"
            assert provenance.reference.url == url and provenance.scope is Complete.MAIN_DOCUMENT
            assert not peer.activations
        finally:
            resources.close()


def test_native_refresh_invalidates_results_while_preserving_edited_drafts_and_saved_evidence(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, _reader, peer, _sessions):
        peer.add("selected", "https://example.invalid/a", "Original source")
        resources = NativeResources(browser=browser)
        try:
            original = resources.page("selected", "source")
            canvas = m.select(m.add(m.Canvas(), original), original.id)
            canvas, request = m.request(canvas, "first-run", "result", "Summarize the source")
            canvas = m.accept(canvas, m.Completed(request.id, "Original summary"))
            canvas = m.edit(m.draft(canvas, "result", "draft"), "draft", "User's wording")
            edited = canvas.item("draft")
            peer.add("selected", "https://example.invalid/a", "Updated source")
            refreshed = resources.refresh(original)
            updated = m.observe(canvas, refreshed)
            assert m.outdated(updated, "result") and m.outdated(updated, "draft")
            assert updated.item("draft") == edited and updated.space == canvas.space
            assert request.sources[0].text == "Original source"
            assert isinstance(refreshed.content, m.Source) and refreshed.content.text == "Updated source"
            store = CanvasStore(tmp_path / "canvas.json")
            store.save(updated)
            assert store.load() == updated
        finally:
            resources.close()


def test_native_open_activates_only_the_original_target_without_reading_a_loading_document(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, _reader, peer, _sessions):
        peer.add("selected", "https://example.invalid/a", "Selected")
        peer.add("sibling", "https://example.invalid/a", "Sibling")
        resources = NativeResources(browser=browser)
        try:
            item = resources.page("selected", "source")
            assert isinstance(item.content, m.Source)
            peer.observations["selected"] = {"kind": "loading"}
            peer.commands.clear()
            resources.open(item.content.origin, item.id)
            assert peer.activations == ["selected"]
            assert [command["method"] for command in peer.commands] == ["Target.getTargets", "Target.activateTarget"]
        finally:
            resources.close()


def test_native_closed_or_navigated_pages_keep_the_old_source_and_require_reselection(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, _reader, peer, sessions):
        peer.add("selected", "https://example.invalid/a", "Selected")
        peer.add("sibling", "https://example.invalid/a", "Sibling")
        resources = NativeResources(browser=browser)
        try:
            item = resources.page("selected", "source")
            assert isinstance(item.content, m.Source)
            peer.targets.pop("selected")
            with TestCase().assertRaisesRegex(OSError, "closed"):
                resources.refresh(item)
            with TestCase().assertRaisesRegex(OSError, "closed"):
                resources.open(item.content.origin, item.id)
            peer.add("selected", "https://example.invalid/another", "Other page")
            with TestCase().assertRaisesRegex(OSError, "navigated"):
                resources.refresh(item)
            with TestCase().assertRaisesRegex(OSError, "navigated"):
                resources.open(item.content.origin, item.id)
            assert item.content.text == "Selected" and not peer.activations
            assert len(sessions.opened) == 1
        finally:
            resources.close()


def test_native_legacy_and_cancelled_sources_do_not_connect_or_replace_content(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, _reader, peer, sessions):
        peer.add("selected", "https://example.invalid/a", "Selected")
        resources = NativeResources(browser=browser)
        try:
            legacy = m.Item("legacy", "Saved page", m.Source(m.WebPage("https://example.invalid/a"), "Saved text", 10))
            assert isinstance(legacy.content, m.Source)
            with TestCase().assertRaisesRegex(OSError, "reconnect"):
                resources.refresh(legacy)
            with TestCase().assertRaisesRegex(OSError, "reconnect"):
                resources.open(legacy.content.origin, legacy.id)
            assert not peer.commands
            resources.cancel.set()
            with TestCase().assertRaisesRegex(OSError, "stopped"):
                resources.page("selected", "source")
            assert not peer.commands and len(sessions.opened) == 1 and legacy.content.text == "Saved text"
        finally:
            resources.close()
