"""Managed browser captures never fall back to an external browser owner."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from assistant.page_source_values import BrowserKind, PageReference, RebindRequired

from .test_page_sources import page_browser


def test_managed_capture_refresh_does_not_wait_on_or_read_the_external_browser(tmp_path: Path) -> None:
    reference = PageReference(BrowserKind.TOOLKIT, "page-a", "document-a", "https://example.test/book")
    with page_browser(tmp_path) as (browser, reader, peer, _), ThreadPoolExecutor(max_workers=1) as worker:
        peer.add("document-a", reference.url, "Unrelated external page")
        peer.commands.clear()
        browser.lock.acquire()
        try:
            refreshed = worker.submit(reader.refresh, reference, threading.Event(), at=123).result(timeout=1)
        finally:
            browser.lock.release()
        resolved = reader.resolve(reference, threading.Event())

        assert isinstance(refreshed, RebindRequired) and refreshed.reference == reference
        assert isinstance(resolved, RebindRequired) and resolved.reference == reference
        assert "Toolkit browser card" in refreshed.reason and "Toolkit browser card" in resolved.reason
        assert peer.commands == []
