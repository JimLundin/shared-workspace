"""CDP transport failures and browser limits do not leave ambiguous side effects."""

import json
import threading
from pathlib import Path
from typing import cast
from unittest import TestCase
from unittest.mock import patch

from assistant.browser import MAX_OUTPUT, MAX_RESPONSE, Attached, Browser, BrowserError, Cdp, InterruptedError, Managed, Product, Separate, web_url
from assistant.browser_session import BrowserSession, Source
from assistant.browser_tabs import TabStrip
from assistant.llm import Json


def decoded(value: str) -> dict[str, Json]:
    result = cast(Json, json.loads(value))
    assert isinstance(result, dict)
    return result


class Channel:
    __slots__ = ("cancel", "closed", "replies", "sent")

    def __init__(self, *replies: str | bytes | Exception, cancel: threading.Event | None = None) -> None:
        self.replies = list(replies)
        self.sent: list[dict[str, Json]] = []
        self.closed = False
        self.cancel = cancel

    def send(self, message: str) -> None:
        self.sent.append(decoded(message))

    def recv(self, timeout: float) -> str | bytes:
        assert 0 < timeout <= .1
        if self.cancel is not None:
            self.cancel.set()
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.closed = True

    @classmethod
    def results(cls, *results: dict[str, Json]) -> Channel:
        return cls(*(json.dumps(dict[str, Json](id=index, result=result)) for index, result in enumerate(results, 1)))


class ScriptedSessions:
    def __init__(self, *sessions: BrowserSession | Exception) -> None:
        self.sessions = list(sessions)
        self.opened: list[tuple[Source, bool]] = []

    def open(self, source: Source, cancel: threading.Event, *, visible: bool = False) -> BrowserSession:
        self.opened.append((source, visible))
        session = self.sessions.pop(0)
        if isinstance(session, Exception):
            raise session
        return session


def connected_browser(
    path: Path, channel: Channel, *, owner: Attached | Managed | None = None, strip: TabStrip | None = None,
) -> Browser:
    owner = owner if owner is not None else Attached(Product.EDGE)
    source = owner.product if isinstance(owner, Attached) else Separate.PROFILE
    browser = Browser(path, source=source, opener=ScriptedSessions(BrowserSession(Cdp(channel), owner)), strip=strip)
    browser.connect(threading.Event(), visible=isinstance(owner, Managed) and owner.visible)
    return browser


def test_cdp_ignores_events_and_late_replies_and_preserves_session() -> None:
    channel = Channel('{"method":"Page.loadEventFired"}', '{"id":99,"result":{}}', TimeoutError(), '{"id":1,"result":{"value":42}}')
    cdp = Cdp(channel)
    assert cdp.call("Runtime.evaluate", {"expression": "21*2"}, threading.Event(), session="page-session") == {"value": 42}
    assert channel.sent == [{"id": 1, "method": "Runtime.evaluate", "params": {"expression": "21*2"}, "sessionId": "page-session"}]
    cdp.close()
    assert channel.closed


def test_cdp_cancellation_and_timeout_never_repeat_an_action() -> None:
    stop = threading.Event()
    channel = Channel('{"method":"Page.loadEventFired"}', cancel=stop)
    with TestCase().assertRaisesRegex(InterruptedError, "may already have completed"):
        Cdp(channel).call("Input.dispatchMouseEvent", {}, stop)
    assert len(channel.sent) == 1
    channel = Channel()
    with TestCase().assertRaisesRegex(InterruptedError, "Stopped"):
        Cdp(channel).call("Page.navigate", {}, stop)
    assert not channel.sent
    with TestCase().assertRaisesRegex(InterruptedError, "timed out"):
        Cdp(channel).call("Runtime.evaluate", {}, threading.Event(), timeout=0)
    assert len(channel.sent) == 1


def test_cdp_rejects_malformed_oversized_and_error_replies() -> None:
    for response, message in (
        ("not JSON", "invalid data"), ("[]", "unexpected response"),
        ('{"id":1,"error":{"message":"Page is closed"}}', "Page is closed"),
        ("x" * (MAX_RESPONSE + 1), "size limit"),
    ):
        with TestCase().assertRaisesRegex(BrowserError, message):
            Cdp(Channel(response)).call("Page.getFrameTree", {}, threading.Event())


def test_cdp_refuses_non_loopback_debugging_and_proxy_use() -> None:
    for uri in ("ws://localhost:9222/", "wss://127.0.0.1:9222/", "ws://example.com/", "ws://user:pass@127.0.0.1:9222/", "ws://[broken"):
        with TestCase().assertRaisesRegex(BrowserError, "loopback"):
            Cdp.connect(uri)
    channel = Channel()
    with patch("websockets.sync.client.connect", return_value=channel) as connect:
        cdp = Cdp.connect("ws://127.0.0.1:12345/devtools/browser/fixture")
        arguments = cast(tuple[tuple[object, ...], dict[str, object]], connect.call_args)
        assert arguments[1]["proxy"] is None
        cdp.close()


def test_browser_initialization_and_status_do_not_launch_or_create_a_profile(tmp_path: Path) -> None:
    browser = Browser(tmp_path / "profile")
    assert decoded(browser.request("tabs", {}, threading.Event()).text) == {"mode": "stopped", "tabs": []}
    assert not browser.profile.exists()
    stop = threading.Event()
    stop.set()
    with TestCase().assertRaisesRegex(InterruptedError, "Stopped"):
        browser.request("open", {"url": "https://example.com"}, stop)
    assert browser.process is None and not browser.profile.exists()


def test_browser_accepts_web_pages_but_not_embedded_credentials_or_local_schemes() -> None:
    for url in ("https://example.com/a?q=1", "http://127.0.0.1:12345/", "about:blank"):
        assert web_url(url) == url
    for url in ("file:///c:/secret", "javascript:alert(1)", "https://user:secret@example.com", "https://example.com/\nsecret", "https://[broken"):
        with TestCase().assertRaises(BrowserError):
            web_url(url)


def test_browser_snapshot_limits_keep_valid_json_and_progress_offsets(tmp_path: Path) -> None:
    source: dict[str, Json] = {
        "title": "Fixture", "url": "https://example.com", "text": "\x00" * 6000, "text_total": 7000,
        "controls": [{"id": str(i), "name": "n" * 300} for i in range(100)],
        "offset": 4, "total": 104, "more_controls": False,
    }
    channel = Channel.results(
        {"targetInfos": [{"type": "page", "targetId": "fixture"}]}, {"sessionId": "page-session"}, {},
        {"frameTree": {"frame": {"id": "main", "url": "https://example.com"}}}, {"executionContextId": 1}, {"result": {"value": source}},
    )
    browser = connected_browser(tmp_path, channel)
    output = browser.request("snapshot", {"target_id": "fixture", "offset": 4}, threading.Event()).text
    assert len(output) <= MAX_OUTPUT
    page = decoded(output)
    assert page["more_controls"] and page["next_text_offset"] == len(cast(str, page["text"]))
    assert page["controls"]
    assert page["next_control_offset"] == 4 + len(cast(list[Json], page["controls"]))


def test_browser_bad_screenshot_is_reported_as_a_tool_error(tmp_path: Path) -> None:
    for data in ("invalid base64!", "aGVsbG8="):
        channel = Channel.results(
            {"targetInfos": [{"type": "page", "targetId": "fixture"}]}, {"sessionId": "page-session"}, {}, {"data": data},
        )
        browser = connected_browser(tmp_path, channel)
        with TestCase().assertRaisesRegex(BrowserError, "invalid"):
            browser.request("screenshot", {"target_id": "fixture"}, threading.Event())
        browser.close()


def test_closing_a_tab_does_not_require_page_scripting_access(tmp_path: Path) -> None:
    channel = Channel(
        '{"id":1,"result":{"targetInfos":[{"type":"page","targetId":"settings","url":"edge://settings"}]}}',
        '{"id":2,"result":{"success":true}}',
        '{"id":3,"result":{"targetInfos":[]}}',
    )
    browser = connected_browser(tmp_path / "unused", channel)
    try:
        closed = decoded(browser.request("close", {"target_id": "settings"}, threading.Event()).text)
        assert closed == {"target_id": "settings", "state": "closed"}
        assert [command["method"] for command in channel.sent] == ["Target.getTargets", "Target.closeTarget", "Target.getTargets"]
    finally:
        browser.close()


def test_close_waits_for_removal_without_repeating_the_request(tmp_path: Path) -> None:
    targets: dict[str, Json] = {"targetInfos": [{"type": "page", "targetId": "closing", "url": "about:blank"}]}
    replies: tuple[Json, ...] = (targets, {"success": True}, targets, {"targetInfos": []})
    channel = Channel(*(json.dumps(dict[str, Json](id=index, result=reply)) for index, reply in enumerate(replies, 1)))
    browser = connected_browser(tmp_path / "unused", channel)
    try:
        result = decoded(browser.request("close", {"target_id": "closing"}, threading.Event()).text)
        assert result["state"] == "closed"
        assert [command["method"] for command in channel.sent].count("Target.closeTarget") == 1
    finally:
        browser.close()


def test_browser_waits_for_the_root_after_job_termination_instead_of_killing_it_twice(tmp_path: Path) -> None:
    class ExitingProcess:
        __slots__ = ("job_closed",)
        pid = 42

        def __init__(self) -> None:
            self.job_closed = False

        def poll(self) -> int | None:
            return None  # Windows has not signalled the process handle yet.

        def wait(self, timeout: float) -> int:
            if not self.job_closed:
                import subprocess  # noqa: PLC0415

                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

        def kill(self) -> None:
            raise PermissionError("Windows refuses a second termination while a process is exiting.")

    process = ExitingProcess()

    class Job:
        __slots__ = ()

        def close(self) -> None:
            process.job_closed = True

    browser = connected_browser(tmp_path / "profile", Channel.results({}), owner=Managed(process, Job(), False))
    browser.close()
    assert process.job_closed
