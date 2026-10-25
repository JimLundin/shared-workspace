"""Exact browser-source bindings through a real, local CDP WebSocket fixture."""

import json
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from enum import Enum, auto
from pathlib import Path
from typing import cast
from unittest import TestCase

from websockets.sync.server import ServerConnection, serve

from assistant.browser import Attached, Browser, Cdp, Product
from assistant.browser_session import BrowserSession, Source
from assistant.llm import Json
from assistant.page_source_values import (
    MAX_TEXT,
    BrowserKind,
    Cancelled,
    Complete,
    Excerpt,
    ObservedPage,
    PageProvenance,
    PageReference,
    RebindRequired,
    Unavailable,
    provenance_data,
    provenance_of,
    validate_provenance,
)
from assistant.page_sources import PageSources

from .test_browser import decoded


class Behavior(Enum):
    NORMAL = auto()
    NO_EVALUATION_REPLY = auto()
    DISCONNECT = auto()
    INVALID_ATTACHMENT = auto()
    NO_DETACH_REPLY = auto()
    PAUSE_DETACH_REPLY = auto()


class Documents:
    """A small CDP peer; commands that could create or navigate tabs are errors."""

    def __init__(self) -> None:
        self.targets: dict[str, dict[str, Json]] = {}
        self.observations: dict[str, Json] = {}
        self.commands: list[dict[str, Json]] = []
        self.sessions: dict[str, str] = {}
        self.activations: list[str] = []
        self.errors: list[Exception] = []
        self.behavior = Behavior.NORMAL
        self.evaluating = threading.Event()
        self.detaching = threading.Event()
        self.release_detach = threading.Event()
        self.detached = threading.Event()
        self.disconnected = threading.Event()
        self.serial = 0

    def add(self, identity: str, url: str, content: str, *, title: str = "Fixture page", total: int | None = None) -> None:
        self.targets[identity] = {"targetId": identity, "type": "page", "title": title, "url": url}
        units = len(content.encode("utf-16-le")) // 2
        self.observations[identity] = {"kind": "source", "url": url, "title": title, "text": content,
                                      "end_utf16": units, "total_utf16": total if total is not None else units}

    def respond(self, command: dict[str, Json]) -> dict[str, Json] | None:
        method = command["method"]
        params = command["params"]
        assert isinstance(params, dict)
        match method:
            case "Target.getTargets":
                return {"targetInfos": list(self.targets.values())}
            case "Target.attachToTarget":
                target = params["targetId"]
                assert isinstance(target, str) and target in self.targets and params["flatten"] is True
                self.serial += 1
                session = f"source-session-{self.serial}"
                self.sessions[session] = target
                return {"sessionId": False if self.behavior is Behavior.INVALID_ATTACHMENT else session}
            case "Target.detachFromTarget":
                detached_session = params["sessionId"]
                assert isinstance(detached_session, str) and detached_session in self.sessions
                if self.behavior is Behavior.NO_DETACH_REPLY:
                    return None
                if self.behavior is Behavior.PAUSE_DETACH_REPLY:
                    self.detaching.set()
                    assert self.release_detach.wait(timeout=2), "The test did not acknowledge the completed source read."
                del self.sessions[detached_session]
                self.detached.set()
                return {}
            case "Target.activateTarget":
                target = params["targetId"]
                assert isinstance(target, str) and target in self.targets
                self.activations.append(target)
                return {}
            case _:
                source_session = command.get("sessionId")
                assert isinstance(source_session, str) and source_session in self.sessions
                target = self.sessions[source_session]
        match method:
            case "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "main-frame"}}}
            case "Page.createIsolatedWorld":
                assert params["frameId"] == "main-frame"
                return {"executionContextId": 37}
            case "Runtime.evaluate":
                assert params["contextId"] == 37 and params["returnByValue"] is True
                self.evaluating.set()
                return None if self.behavior is Behavior.NO_EVALUATION_REPLY else {"result": {"value": self.observations[target]}}
            case _:
                raise AssertionError(f"Unexpected browser effect: {method}")

    def serve(self, connection: ServerConnection) -> None:
        try:
            for raw in connection:
                assert isinstance(raw, str)
                command = decoded(raw)
                self.commands.append(command)
                if command["method"] == "Runtime.evaluate" and self.behavior is Behavior.DISCONNECT:
                    connection.close()
                    return
                response = self.respond(command)
                if response is not None:
                    reply: dict[str, Json] = {"id": command["id"], "result": response}
                    connection.send(json.dumps(reply))
        except Exception as error:
            self.errors.append(error)
        finally:
            self.sessions.clear()
            self.disconnected.set()


class LocalSessions:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.opened: list[Source] = []

    def open(self, source: Source, cancel: threading.Event, *, visible: bool = False) -> BrowserSession:
        self.opened.append(source)
        assert isinstance(source, Product) and not visible
        return BrowserSession(Cdp.connect(self.uri, cancel), Attached(source))


@contextmanager
def page_browser(tmp_path: Path) -> Generator[tuple[Browser, PageSources, Documents, LocalSessions]]:
    documents = Documents()
    server = serve(documents.serve, "127.0.0.1", 0, ping_interval=None, close_timeout=.5)
    port = cast(tuple[str, int], server.socket.getsockname())[1]
    sessions = LocalSessions(f"ws://127.0.0.1:{port}/devtools/browser/page-source-fixture")
    browser = Browser(tmp_path / "unused-profile", source=Product.EDGE, opener=sessions)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        browser.connect(threading.Event())
        yield browser, PageSources(browser), documents, sessions
    finally:
        browser.close()
        server.shutdown()
        thread.join(timeout=2)
        assert documents.disconnected.wait(timeout=2)
        assert not thread.is_alive() and not documents.sessions and not documents.errors
        assert not browser.profile.exists()


def observed(reader: PageSources, target: str = "selected", *, at: float = 10) -> ObservedPage:
    result = reader.capture(target, threading.Event(), at=at)
    assert isinstance(result, ObservedPage), result
    return result


def test_disconnected_and_precancelled_sources_do_not_connect_or_create_profiles(tmp_path: Path) -> None:
    sessions = LocalSessions("not a valid browser endpoint")
    browser = Browser(tmp_path / "unused", source=Product.EDGE, opener=sessions)
    reader = PageSources(browser)
    assert isinstance(reader.capture("selected", threading.Event(), at=10), Unavailable)
    cancelled = threading.Event()
    cancelled.set()
    assert reader.capture("selected", cancelled, at=10) is Cancelled.READ
    assert not sessions.opened and not browser.profile.exists() and browser.connection is None


def test_selected_target_keeps_full_url_and_more_than_the_generic_snapshot_limit(tmp_path: Path) -> None:
    url = "https://example.invalid/" + "long/" * 250
    content = "  Exact selected text.\n" + "source " * 1_200 + "\n\tEnd.  "
    with page_browser(tmp_path) as (browser, reader, peer, sessions):
        peer.add("sibling", url, "Do not capture the same-URL sibling")
        peer.add("selected", url, content, title="Selected page")
        result = observed(reader)
        assert result.text == content and result.title == "Selected page"
        assert result.reference.url == url and result.reference.target_id == "selected"
        assert result.reference.browser is BrowserKind.EDGE and result.scope is Complete.MAIN_DOCUMENT
        assert result.observed_at == 10 and not peer.activations
        assert sessions.opened == [Product.EDGE] and browser.sessions == {}
        assert peer.detached.wait(timeout=1) and not peer.sessions


def test_refresh_uses_exact_binding_and_versions_text_independently_of_observation_time(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (_browser, reader, peer, sessions):
        peer.add("selected", "https://example.invalid/a", "Original\ntext")
        first = observed(reader)
        second = reader.refresh(first.reference, threading.Event(), at=20)
        assert isinstance(second, ObservedPage)
        assert second.version == first.version and second.observed_at == 20
        peer.add("selected", first.reference.url, "Updated\ntext", title="Updated title")
        changed = reader.refresh(first.reference, threading.Event(), at=30)
        assert isinstance(changed, ObservedPage) and changed.version != first.version
        assert changed.reference == first.reference and first.text == "Original\ntext"
        assert sessions.opened == [Product.EDGE]


def test_closed_or_navigated_target_cannot_be_replaced_by_a_same_url_sibling(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (_browser, reader, peer, _sessions):
        peer.add("selected", "https://example.invalid/a", "Original")
        peer.add("sibling", "https://example.invalid/a", "Replacement")
        original = observed(reader)
        peer.targets.pop("selected")
        closed = reader.refresh(original.reference, threading.Event(), at=20)
        assert isinstance(closed, RebindRequired) and closed.reference == original.reference
        peer.add("selected", "https://example.invalid/navigated", "New page")
        navigated = reader.refresh(original.reference, threading.Event(), at=30)
        assert isinstance(navigated, RebindRequired)
        assert peer.serial == 1 and original.text == "Original" and not peer.activations


def test_navigation_between_catalogue_and_read_rejects_the_observation(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (_browser, reader, peer, _sessions):
        peer.add("selected", "https://example.invalid/a", "Original")
        payload = peer.observations["selected"]
        assert isinstance(payload, dict)
        peer.observations["selected"] = {**payload, "url": "https://example.invalid/other", "text": "Other page"}
        result = reader.capture("selected", threading.Event(), at=10)
        assert isinstance(result, RebindRequired) and result.reference.url == "https://example.invalid/a"
        assert peer.detached.wait(timeout=1) and not peer.sessions


def test_new_connection_or_reader_requires_an_explicit_new_capture(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, reader, peer, sessions):
        peer.add("selected", "https://example.invalid/a", "Original")
        first = observed(reader)
        assert isinstance(PageSources(browser).refresh(first.reference, threading.Event(), at=20), RebindRequired)
        browser.close()
        assert peer.disconnected.wait(timeout=2)
        peer.disconnected.clear()
        browser.connect(threading.Event())
        assert isinstance(reader.refresh(first.reference, threading.Event(), at=30), RebindRequired)
        recaptured = observed(reader, at=40)
        assert recaptured.reference.target_id == first.reference.target_id
        assert recaptured.reference.binding_id != first.reference.binding_id and sessions.opened == [Product.EDGE, Product.EDGE]


def test_main_document_and_excerpt_coverage_use_utf16_units(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (_browser, reader, peer, _sessions):
        peer.add("selected", "https://example.invalid/a", "Hi 🌍\n")
        complete = observed(reader)
        assert complete.text == "Hi 🌍\n" and complete.scope is Complete.MAIN_DOCUMENT
        content = "x" * (MAX_TEXT - 1)
        peer.add("selected", complete.reference.url, content, total=MAX_TEXT + 4)
        excerpt = reader.refresh(complete.reference, threading.Event(), at=20)
        assert isinstance(excerpt, ObservedPage) and excerpt.scope == Excerpt(0, MAX_TEXT - 1, MAX_TEXT + 4)
        assert excerpt.text == content and excerpt.version != complete.version
        peer.add("selected", complete.reference.url, "")
        empty = observed(reader)
        assert empty.text == "" and empty.scope is Complete.MAIN_DOCUMENT


def test_resolve_checks_live_binding_without_reading_or_focusing_the_page(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (_browser, reader, peer, _sessions):
        peer.add("selected", "https://example.invalid/a", "Original")
        original = observed(reader)
        peer.commands.clear()
        peer.observations["selected"] = {"kind": "loading"}
        assert reader.resolve(original.reference, threading.Event()) == original.reference
        assert [command["method"] for command in peer.commands] == ["Target.getTargets"]
        assert not peer.activations
        peer.targets.pop("selected")
        assert isinstance(reader.resolve(original.reference, threading.Event()), RebindRequired)


def test_malformed_observations_preserve_the_previous_immutable_source(tmp_path: Path) -> None:
    malformed: tuple[dict[str, Json], ...] = (
        {"end_utf16": True}, {"total_utf16": True}, {"end_utf16": 2}, {"total_utf16": 1},
        {"end_utf16": -1}, {"text": "x" * (MAX_TEXT + 1)}, {"text": "\ud800"}, {"title": "\udfff"},
        {"kind": "unknown"}, {"url": "file:///private"}, {"url": "https://user:password@example.invalid/"},
        {"total_utf16": 100_000_001}, {"kind": "loading"}, {"kind": "no_body"},
    )
    with page_browser(tmp_path) as (_browser, reader, peer, _sessions):
        peer.add("selected", "https://example.invalid/a", "Original")
        original = observed(reader)
        payload = peer.observations["selected"]
        assert isinstance(payload, dict)
        for mutation in malformed:
            peer.observations["selected"] = {**payload, **mutation}
            result = reader.refresh(original.reference, threading.Event(), at=20)
            assert isinstance(result, Unavailable), (mutation, result)
            assert original.text == "Original" and not peer.sessions
        assert not peer.activations


def test_cancellation_during_a_real_socket_read_detaches_and_does_not_replace_content(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (_browser, reader, peer, _sessions), ThreadPoolExecutor(max_workers=1) as worker:
        peer.add("selected", "https://example.invalid/a", "Original")
        original = observed(reader)
        peer.behavior = Behavior.NO_EVALUATION_REPLY
        peer.evaluating.clear()
        peer.detached.clear()
        cancel = threading.Event()
        pending = worker.submit(reader.refresh, original.reference, cancel, at=20)
        try:
            assert peer.evaluating.wait(timeout=2)
            started = time.monotonic()
            cancel.set()
            assert pending.result(timeout=2) is Cancelled.READ
            assert time.monotonic() - started < 2 and peer.detached.wait(timeout=1)
            assert original.text == "Original" and not peer.sessions
        finally:
            cancel.set()


def test_disconnect_or_invalid_attachment_drops_only_the_borrowed_connection(tmp_path: Path) -> None:
    for behavior in (Behavior.DISCONNECT, Behavior.INVALID_ATTACHMENT, Behavior.NO_DETACH_REPLY):
        with page_browser(tmp_path) as (browser, reader, peer, _sessions):
            peer.add("selected", "https://example.invalid/a", "Original")
            first = observed(reader)
            peer.behavior = behavior
            result = reader.refresh(first.reference, threading.Event(), at=20)
            if behavior is Behavior.DISCONNECT:
                assert isinstance(result, RebindRequired)
            else:
                assert isinstance(result, Unavailable)
            assert browser.connection is None and peer.disconnected.wait(timeout=2)
            assert isinstance(reader.refresh(first.reference, threading.Event(), at=30), RebindRequired)
            assert first.text == "Original" and not peer.activations


def test_cancellation_after_a_valid_read_while_detaching_discards_replacement_content(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, reader, peer, _sessions), ThreadPoolExecutor(max_workers=1) as worker:
        peer.add("selected", "https://example.invalid/a", "Original")
        original = observed(reader)
        peer.add("selected", original.reference.url, "Replacement content already read")
        peer.behavior = Behavior.PAUSE_DETACH_REPLY
        peer.detached.clear()
        cancel = threading.Event()
        pending = worker.submit(reader.refresh, original.reference, cancel, at=20)
        try:
            assert peer.detaching.wait(timeout=2) and not pending.done()
            cancel.set()
            peer.release_detach.set()
            assert pending.result(timeout=2) is Cancelled.READ
            assert peer.detached.wait(timeout=1) and not peer.sessions
            assert browser.connection is not None and original.text == "Original"
        finally:
            cancel.set()
            peer.release_detach.set()


def test_waiting_for_the_browser_lock_is_cancellable_without_sending_commands(tmp_path: Path) -> None:
    with page_browser(tmp_path) as (browser, reader, peer, _sessions), ThreadPoolExecutor(max_workers=1) as worker:
        cancel = threading.Event()
        with browser.lock:
            pending = worker.submit(reader.capture, "selected", cancel, at=10)
            cancel.set()
            assert pending.result(timeout=2) is Cancelled.READ
        assert not peer.commands


def test_provenance_roundtrips_a_real_file_and_rejects_text_identity_scope_or_digest_changes(tmp_path: Path) -> None:
    reference = PageReference(BrowserKind.EDGE, "binding-1", "selected", "https://example.invalid/a")
    page = ObservedPage(reference, "Title", "🌍\nExcerpt", Excerpt(0, 10, 100), 10)
    path = tmp_path / "page.json"
    path.write_text(json.dumps(provenance_data(page.provenance), ensure_ascii=False), encoding="utf-8")
    restored = provenance_of(cast(object, json.loads(path.read_text(encoding="utf-8"))))
    assert restored == page.provenance
    validate_provenance(restored, page.text)
    for changed in (
        replace(restored, reference=replace(reference, url="https://example.invalid/replacement")),
        replace(restored, reference=replace(reference, target_id="sibling")),
        replace(restored, reference=replace(reference, binding_id="other-binding")),
        replace(restored, scope=Excerpt(10, 20, 100)),
        replace(restored, version="0" * 64),
    ):
        with TestCase().assertRaises(ValueError):
            validate_provenance(changed, page.text)
    with TestCase().assertRaises(ValueError):
        validate_provenance(restored, "Changed text")
    assert replace(page, observed_at=100, title="Renamed title").version == page.version


def test_saved_provenance_rejects_malformed_contracts() -> None:
    reference = PageReference(BrowserKind.EDGE, "binding", "tab", "https://example.invalid/a")
    provenance = ObservedPage(reference, "Title", "Text", Complete.MAIN_DOCUMENT, 10).provenance
    original = provenance_data(provenance)
    malformed: tuple[dict[str, Json], ...] = (
        {}, {**original, "extra": True}, {**original, "format": True}, {**original, "version": "broken"},
        {**original, "scope": {"kind": "excerpt", "start_utf16": 0, "end_utf16": 4, "total_utf16": 4}},
        {**original, "scope": {"kind": "complete_main_document", "extra": True}},
        {**original, "reference": {"browser": "edge", "binding_id": "binding", "target_id": "tab", "url": "javascript:alert(1)"}},
    )
    for data in malformed:
        with TestCase().assertRaises(ValueError):
            provenance_of(data)
    with TestCase().assertRaises(ValueError):
        PageProvenance(reference, Complete.MAIN_DOCUMENT, "not a digest")
