"""Borrowed browser lifetime and cancellable consent, without a personal profile."""

import json
import socket
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from unittest import TestCase
from unittest.mock import patch

from websockets.http11 import Request, Response
from websockets.sync.server import ServerConnection, serve

from assistant.browser import MAX_OUTPUT, Attached, Browser, BrowserError, Cdp, InterruptedError, Product, endpoint
from assistant.llm import Json

from .test_browser import Channel, decoded


@contextmanager
def consent_browser(tmp_path: Path, *, approval_delay: float = 0) -> Generator[tuple[Browser, list[str]]]:
    """A real CDP socket whose WebSocket upgrade waits for browser consent."""
    approvals: list[str] = []

    def approve(_connection: ServerConnection, request: Request) -> Response | None:
        approvals.append(request.path)
        time.sleep(approval_delay)
        return None

    def commands(connection: ServerConnection) -> None:
        for raw in connection:
            assert isinstance(raw, str)
            command = decoded(raw)
            assert command["method"] == "Target.getTargets"
            reply: dict[str, Json] = {"id": command["id"], "result": {"targetInfos": []}}
            connection.send(json.dumps(reply))

    server = serve(commands, "127.0.0.1", 0, process_request=approve, ping_interval=None, close_timeout=.5)
    port = cast(tuple[str, int], server.socket.getsockname())[1]
    (tmp_path / "DevToolsActivePort").write_text(f"{port}\n/devtools/browser/fixture\n", encoding="ascii")
    browser = Browser(tmp_path / "unused", source=Product.EDGE)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("assistant.browser.user_data_root", return_value=tmp_path):
            yield browser, approvals
    finally:
        browser.close()
        server.shutdown()
        thread.join(timeout=2)


def test_attachment_waits_for_consent_then_reuses_the_approved_connection(tmp_path: Path) -> None:
    with consent_browser(tmp_path, approval_delay=2.5) as (browser, approvals):
        first = decoded(browser.request("attach", {}, threading.Event()).text)
        second = decoded(browser.request("attach", {}, threading.Event()).text)
        assert first == second and first["mode"] == "attached"
        assert approvals == ["/devtools/browser/fixture"]


def test_approved_attachment_survives_idle_without_another_consent_request(tmp_path: Path) -> None:
    with consent_browser(tmp_path) as (browser, approvals):
        first = decoded(browser.request("attach", {}, threading.Event()).text)
        time.sleep(2.5)
        second = decoded(browser.request("attach", {}, threading.Event()).text)
        assert first == second and first["mode"] == "attached"
        assert approvals == ["/devtools/browser/fixture"]


def test_endpoint_discovery_is_bounded_and_accepts_only_a_local_browser_path() -> None:
    assert endpoint(b"12345\r\n/devtools/browser/fixture-123\r\n") == "ws://127.0.0.1:12345/devtools/browser/fixture-123"
    for data in (b"", b"0\n/devtools/browser/id", b"65536\n/devtools/browser/id", b"123\n/devtools/page/id",
                 b"123\n/devtools/browser/id?redirect=evil", b"123\n/devtools/browser/../../secret", b"1\n\xff",
                 b"123\n/devtools/browser/id\nextra", b"1" * 513):
        with TestCase().assertRaisesRegex(BrowserError, "invalid debugging endpoint"):
            endpoint(data)


def test_status_does_not_read_profiles_or_request_a_connection(tmp_path: Path) -> None:
    browser = Browser(tmp_path / "separate", source=Product.EDGE)
    with patch("assistant.browser.Cdp.connect") as connect, patch("assistant.browser.read_endpoint") as read:
        status = decoded(browser.request("tabs", {}, threading.Event()).text)
    assert not connect.called and not read.called
    assert status["mode"] == "stopped" and status["browser"] == "edge" and status["tabs"] == []
    assert "edge://inspect/#remote-debugging" in str(status["setup"])
    assert not browser.profile.exists()


def test_attach_reuses_one_connection_and_never_closes_the_personal_browser(tmp_path: Path) -> None:
    port_file = tmp_path / "DevToolsActivePort"
    original = b"12345\n/devtools/browser/fixture-123\n"
    port_file.write_bytes(original)
    channel = Channel('{"id":1,"result":{"targetInfos":[]}}', '{"id":2,"result":{"targetInfos":[]}}')
    connection = Cdp(channel)
    browser = Browser(tmp_path / "unused", source=Product.EDGE)
    cancel = threading.Event()
    with patch("assistant.browser.user_data_root", return_value=tmp_path), patch.object(Cdp, "connect", return_value=connection) as connect:
        first = decoded(browser.request("attach", {}, cancel).text)
        second = decoded(browser.request("attach", {}, cancel).text)
        assert first == second and first["mode"] == "attached"
        connect.assert_called_once_with("ws://127.0.0.1:12345/devtools/browser/fixture-123", cancel, timeout=60)
        assert browser.owner == Attached(Product.EDGE) and browser.process is None
        browser.close()
    assert channel.closed and [command["method"] for command in channel.sent] == ["Target.getTargets", "Target.getTargets"]
    assert port_file.read_bytes() == original and not browser.profile.exists()


def test_attached_tab_pages_identify_created_tabs_and_keep_late_targets_accessible(tmp_path: Path) -> None:
    targets: list[Json] = [
        {"type": "page", "targetId": f"page-{index}", "title": "t" * 300, "url": "https://example.com/" + "u" * 900}
        for index in range(65)
    ]
    enabled: dict[str, Json] = {}
    replies: list[Json] = [
        {"targetInfos": targets}, {"targetInfos": targets}, {"targetInfos": targets},
        {"sessionId": "late-page"}, enabled, {"success": True},
    ]
    messages: list[dict[str, Json]] = [{"id": index, "result": value} for index, value in enumerate(replies, 1)]
    channel = Channel(*(json.dumps(message) for message in messages))
    browser = Browser(tmp_path / "unused", source=Product.EDGE)
    browser.connection = Cdp(channel)
    browser.owner = Attached(Product.EDGE)
    browser.created.add("page-64")
    cancel = threading.Event()

    output = browser.request("tabs", {"limit": 40}, cancel).text
    assert len(output) <= MAX_OUTPUT
    first = decoded(output)
    first_tabs = first["tabs"]
    assert isinstance(first_tabs, list) and 0 < len(first_tabs) < 40
    assert first["total"] == 65 and first["next_offset"] == len(first_tabs)
    assert all(isinstance(tab, dict) and tab["created_in_session"] is False for tab in first_tabs)

    final = decoded(browser.request("tabs", {"offset": 60}, cancel).text)
    final_tabs = final["tabs"]
    assert isinstance(final_tabs, list) and len(final_tabs) == 5 and "next_offset" not in final
    assert isinstance(final_tabs[-1], dict) and final_tabs[-1]["id"] == "page-64" and final_tabs[-1]["created_in_session"] is True
    browser.request("close", {"target_id": "page-64"}, cancel)
    assert channel.sent[-1]["method"] == "Target.closeTarget" and channel.sent[-1]["params"] == {"targetId": "page-64"}
    assert "page-64" not in browser.created
    browser.close()
    assert all(command["method"] != "Browser.close" for command in channel.sent)


def test_missing_or_denied_attachment_does_not_launch_a_separate_browser(tmp_path: Path) -> None:
    browser = Browser(tmp_path / "unused", source=Product.CHROME)
    with patch("assistant.browser.user_data_root", return_value=tmp_path), patch("assistant.browser.edge_path") as launch:
        with TestCase().assertRaisesRegex(BrowserError, "chrome://inspect"):
            browser.request("open", {"url": "https://example.com"}, threading.Event())
        assert not launch.called and not browser.profile.exists()
    with (
        patch("assistant.browser.user_data_root", return_value=tmp_path),
        patch("assistant.browser.read_endpoint", return_value="ws://127.0.0.1:12345/devtools/browser/fixture"),
        patch.object(Cdp, "connect", side_effect=BrowserError("Denied")) as connect,
    ):
        with TestCase().assertRaisesRegex(BrowserError, "Could not attach to Chrome"):
            browser.request("attach", {}, threading.Event())
        assert connect.call_count == 1 and browser.connection is None


def test_stop_closes_the_socket_while_browser_consent_is_pending() -> None:
    cancel, received, closed = threading.Event(), threading.Event(), threading.Event()
    with socket.create_server(("127.0.0.1", 0)) as server, ThreadPoolExecutor(max_workers=2) as workers:
        server.settimeout(4)

        def consent_server() -> None:
            connection, _peer = server.accept()
            with connection:
                connection.settimeout(4)
                request = b""
                while b"\r\n\r\n" not in request:
                    request += connection.recv(4096)
                received.set()
                if connection.recv(1) == b"":
                    closed.set()

        pending_server = workers.submit(consent_server)
        port = cast(tuple[str, int], server.getsockname())[1]
        pending = workers.submit(Cdp.connect, f"ws://127.0.0.1:{port}/devtools/browser/fixture", cancel, timeout=60)
        try:
            assert received.wait(timeout=3)
            started = time.monotonic()
            cancel.set()
            with TestCase().assertRaises(InterruptedError):
                pending.result(timeout=2)
            assert time.monotonic() - started < 2 and closed.wait(timeout=1)
            pending_server.result(timeout=1)
        finally:
            cancel.set()


def test_opening_deadline_closes_pending_consent_without_pressing_stop() -> None:
    cancel = threading.Event()
    with socket.create_server(("127.0.0.1", 0)) as server, ThreadPoolExecutor(max_workers=1) as workers:
        server.settimeout(3)

        def consent_server() -> bool:
            connection, _peer = server.accept()
            with connection:
                connection.settimeout(3)
                request = b""
                while b"\r\n\r\n" not in request:
                    packet = connection.recv(4096)
                    assert packet, "Connection ended before requesting approval."
                    request += packet
                return connection.recv(1) == b""

        pending_server = workers.submit(consent_server)
        port = cast(tuple[str, int], server.getsockname())[1]
        started = time.monotonic()
        with TestCase().assertRaisesRegex(BrowserError, "not approved in time"):
            Cdp.connect(f"ws://127.0.0.1:{port}/devtools/browser/fixture", cancel, timeout=.25)
        assert time.monotonic() - started < 2 and not cancel.is_set()
        assert pending_server.result(timeout=2), "The expired connection is still open."
