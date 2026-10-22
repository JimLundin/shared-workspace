"""The live HTTP boundary cancels pending work and never follows redirects."""

import contextlib
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase

from assistant.graph_http import GraphError, Http


@contextmanager
def endpoint(body: bytes, *, status: int = 200, release: threading.Event | None = None) -> Generator[tuple[str, threading.Event, list[str]]]:
    started = threading.Event()
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            started.set()
            if release is not None:
                release.wait(5)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Location", "/must-not-follow")
            self.end_headers()
            with contextlib.suppress(ConnectionError):
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/fixture", started, requests
    finally:
        if release is not None:
            release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_decodes_json_without_following_redirects_or_retrying_posts() -> None:
    with endpoint(b'{"result":42}', status=302) as (url, _started, requests):
        assert Http().request("POST", url, {}, b"{}", threading.Event()) == (302, {"result": 42})
        assert requests == ["/fixture"]


def test_http_bounds_responses_and_keeps_credentials_out_of_errors() -> None:
    for body, reason in ((b'x' * 1_000_001, "size limit"), (b'fixture-token-secret malformed', "failed"), (b'[]', "unexpected")):
        with endpoint(body) as (url, _started, requests), TestCase().assertRaisesRegex(GraphError, reason) as failed:
            Http().request("POST", url, {"Authorization": "Bearer fixture-header-secret"}, None, threading.Event())
        assert "fixture-token-secret" not in str(failed.exception) and "fixture-header-secret" not in str(failed.exception)
        assert len(requests) == 1


def test_http_stop_interrupts_a_stalled_request_and_does_not_repeat_it() -> None:
    release = threading.Event()
    cancel = threading.Event()
    with endpoint(b'{}', release=release) as (url, started, requests), ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(Http().request, "POST", url, {}, b'{}', cancel)
        try:
            assert started.wait(5)
            before = time.monotonic()
            cancel.set()
            with TestCase().assertRaisesRegex(GraphError, "may have completed"):
                pending.result(timeout=2)
            assert time.monotonic() - before < 1
            assert len(requests) == 1
        finally:
            release.set()


def test_cancelled_http_never_starts_a_request() -> None:
    cancel = threading.Event()
    cancel.set()
    with endpoint(b'{}') as (url, _started, requests), TestCase().assertRaisesRegex(GraphError, "Stopped"):
        Http().request("POST", url, {}, None, cancel)
    assert not requests


def test_http_has_an_overall_deadline_in_addition_to_socket_timeouts() -> None:
    release = threading.Event()
    with (
        endpoint(b'{}', release=release) as (url, _started, requests),
        TestCase().assertRaisesRegex(GraphError, "timed out"),
    ):
        Http(timeout=.5).request("POST", url, {}, None, threading.Event())
    assert len(requests) == 1
