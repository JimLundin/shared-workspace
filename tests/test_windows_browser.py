"""Installed Edge runs real DOM actions in an isolated persistent profile."""

import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from unittest import TestCase

if sys.platform != "win32":
    raise unittest.SkipTest("Native Edge browser automation")

import msvcrt

from assistant.browser import MAX_OUTPUT, Browser, BrowserError, InterruptedError, Product, edge_path, read_endpoint
from assistant.browser_session import NativeSessions
from assistant.browser_values import Group, Tab, group_of, tab_of
from assistant.llm import Json
from assistant.qt import QImage

from .browser_fixture import fixture
from .test_browser import decoded


def request(browser: Browser, operation: str, **args: Json) -> dict[str, Json]:
    return decoded(browser.request(operation, args, threading.Event()).text)


def control(page: dict[str, Json], name: str) -> str:
    controls = page["controls"]
    assert isinstance(controls, list)
    row = next(row for row in controls if isinstance(row, dict) and row["name"] == name)
    return cast(str, row["id"])


def act(browser: Browser, target: str, page: dict[str, Json], name: str, action: str, **args: Json) -> None:
    browser.request("action", {"target_id": target, "id": control(page, name), "action": action, **args}, threading.Event())


def installed() -> None:
    if edge_path() is None:
        raise unittest.SkipTest("Microsoft Edge is not installed")


def test_endpoint_publication_recovers_after_a_native_file_lock(tmp_path: Path) -> None:
    with (tmp_path / "DevToolsActivePort").open("w+b") as stream:
        stream.write(b"12345\n/devtools/browser/fixture\n")
        stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            with TestCase().assertRaises(BrowserError):
                read_endpoint(tmp_path)
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    assert read_endpoint(tmp_path) == "ws://127.0.0.1:12345/devtools/browser/fixture"


def test_background_edge_controls_frames_shadow_roots_and_saved_profile(tmp_path: Path) -> None:
    installed()
    with fixture() as url:
        browser = Browser(tmp_path / "profile")
        try:
            page = request(browser, "open", url=url)
            target = cast(str, page["target_id"])
            assert "fixture-private" not in json.dumps(page)
            act(browser, target, page, "Name", "fill", value='Åäö "literal"')
            act(browser, target, page, "Choice", "select", value="beta")
            act(browser, target, page, "Enabled", "check", checked=True)
            act(browser, target, page, "Save", "click")
            page = request(browser, "snapshot", target_id=target)
            assert 'Åäö "literal":beta:true' in cast(str, page["text"])
            act(browser, target, page, "Shadow action", "click")
            page = request(browser, "snapshot", target_id=target)
            assert page["title"] == "Shadow clicked"
            frames = page["frames"]
            assert isinstance(frames, list)
            frame = next(row for row in frames if isinstance(row, dict) and row["name"] == "embedded")
            inner = request(browser, "snapshot", target_id=target, frame_id=frame["id"])
            act(browser, target, inner, "Frame action", "click", frame_id=frame["id"])
            assert "Frame clicked" in cast(str, request(browser, "snapshot", target_id=target, frame_id=frame["id"])["text"])
            shot = browser.request("screenshot", {"target_id": target}, threading.Event()).image
            assert shot is not None and shot.startswith(b"\x89PNG")
            with TestCase().assertRaisesRegex(BrowserError, "TrustedHTML"):
                browser.request("evaluate", {"target_id": target, "expression": "document.body.innerHTML = '<p>blocked</p>'"}, threading.Event())
            process = browser.process
            browser.close()
            assert process is not None and process.poll() is not None
            page = request(browser, "open", url=url)
            saved = browser.request("evaluate", {"target_id": page["target_id"], "expression": "localStorage.getItem('saved')"}, threading.Event())
            assert cast(Json, json.loads(saved.text)) == 'Åäö "literal":beta:true'
        finally:
            browser.close()


def test_stale_controls_and_snapshot_pagination_with_large_dom(tmp_path: Path) -> None:
    installed()
    browser = Browser(tmp_path / "profile")
    try:
        with fixture() as url:
            page = request(browser, "open", url=url)
            target = cast(str, page["target_id"])
            original = control(page, "Save")
            request(browser, "snapshot", target_id=target)
            with TestCase().assertRaisesRegex(BrowserError, "stale"):
                browser.request("action", {"target_id": target, "id": original, "action": "click"}, threading.Event())
            expression = """(() => {
                const text = document.createElement('p'); text.textContent = 'long content '.repeat(2000); document.body.append(text);
                for (let i = 0; i < 150; i++) {
                    const b = document.createElement('button'); b.textContent = 'Button ' + i + ' x'.repeat(150); document.body.append(b);
                }
                return true;
            })()"""
            browser.request("evaluate", {"target_id": target, "expression": expression}, threading.Event())
            output = browser.request("snapshot", {"target_id": target, "limit": 100}, threading.Event()).text
            assert len(output) <= MAX_OUTPUT
            first = decoded(output)
            second = request(browser, "snapshot", target_id=target, offset=first["next_control_offset"], text_offset=first["next_text_offset"])
            assert second["offset"] == first["next_control_offset"] and second["text_offset"] == first["next_text_offset"]
            assert first["controls"] != second["controls"]
    finally:
        browser.close()


def test_stopping_a_pending_javascript_promise_reaps_edge(tmp_path: Path) -> None:
    installed()
    browser = Browser(tmp_path / "profile")
    cancel = threading.Event()
    try:
        page = request(browser, "open", url="about:blank")
        process = browser.process
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(browser.request, "evaluate", {"target_id": page["target_id"], "expression": "new Promise(() => {})"}, cancel)
            time.sleep(.15)
            started = time.monotonic()
            cancel.set()
            with TestCase().assertRaises(InterruptedError):
                pending.result(timeout=8)
            assert time.monotonic() - started < 6
        assert process is not None and process.poll() is not None and browser.connection is None
    finally:
        cancel.set()
        browser.close()


def test_visible_sign_in_handoff_reuses_only_the_dedicated_profile(tmp_path: Path) -> None:
    installed()
    browser = Browser(tmp_path / "profile")
    try:
        with fixture() as url:
            visible = request(browser, "sign_in", url=url)
            assert visible["mode"] == "sign_in"
            result = request(browser, "open", url=url, target_id=visible["target_id"])
            assert result["title"] == "Toolkit browser fixture"
            assert request(browser, "tabs")["mode"] == "background"
    finally:
        browser.close()


def test_attached_browser_shares_fixture_session_without_reusing_or_closing_personal_tabs(tmp_path: Path) -> None:
    installed()
    personal = Browser(tmp_path / "fixture-personal")
    attached = Browser(
        tmp_path / "unused", source=Product.EDGE,
        opener=NativeSessions(tmp_path / "unused", profiles=lambda _: personal.profile),
    )
    try:
        with fixture() as url:
            original = request(personal, "open", url=url)
            personal.request("evaluate", {"target_id": original["target_id"],
                                          "expression": "localStorage.setItem('fixture-session', 'already-signed-in')"}, threading.Event())
            blank = request(personal, "open", url="about:blank")
            assert request(attached, "attach")["mode"] == "attached"
            connection = attached.connection
            page = request(attached, "open", url=url)
            target = cast(str, page["target_id"])
            assert target not in (original["target_id"], blank["target_id"]) and attached.connection is connection
            value = attached.request("evaluate", {"target_id": target, "expression": "localStorage.getItem('fixture-session')"}, threading.Event())
            assert cast(Json, json.loads(value.text)) == "already-signed-in"
            act(attached, target, page, "Name", "fill", value="Attached fixture")
            act(attached, target, page, "Save", "click")
            assert "Attached fixture" in cast(str, request(attached, "snapshot", target_id=target)["text"])
            shot = attached.request("screenshot", {"target_id": target}, threading.Event()).image
            assert shot is not None and shot.startswith(b"\x89PNG")
            assert "remain open" in attached.request("close", {}, threading.Event()).text
            rows = request(personal, "tabs")["tabs"]
            assert isinstance(rows, list)
            tabs = [tab for tab in rows if isinstance(tab, dict)]
            assert {cast(str, original["target_id"]), cast(str, blank["target_id"]), target} <= {cast(str, tab["id"]) for tab in tabs}
            assert next(tab for tab in tabs if tab["id"] == blank["target_id"])["url"] == "about:blank"
            assert personal.process is not None and personal.process.poll() is None
            assert not attached.profile.exists()
    finally:
        attached.close()
        personal.close()


def test_grouped_screenshots_repaint_the_requested_inactive_tab(tmp_path: Path) -> None:
    installed()
    personal = Browser(tmp_path / "fixture-personal")
    attached = Browser(
        tmp_path / "unused", source=Product.EDGE,
        opener=NativeSessions(tmp_path / "unused", profiles=lambda _: personal.profile),
    )
    try:
        with fixture() as url:
            original = request(personal, "open", url=url)
            request(attached, "attach")
            page = request(attached, "open", url=url)
            target, identity = page["target_id"], page["tab_id"]
            assert isinstance(target, str) and isinstance(identity, int)
            assert target != original["target_id"]

            def state() -> tuple[frozenset[int], Tab, Group]:
                view = request(attached, "tabs", view="strip", limit=40)
                rows, groups = view["tabs"], view["groups"]
                assert isinstance(rows, list) and isinstance(groups, list)
                tabs = tuple(tab_of(row) for row in rows)
                current = next(tab for tab in tabs if tab.id == identity)
                native_groups = tuple(group_of(row) for row in groups)
                group = next(group for group in native_groups if group.id == current.group_id)
                return frozenset(tab.id for tab in tabs if tab.active), current, group

            active, before, group = state()
            assert active and not before.active and group.collapsed
            for color in ((255, 0, 0), (0, 0, 255)):
                css = f"rgb({color[0]}, {color[1]}, {color[2]})"
                expression = """(() => {
                    document.body.replaceChildren();
                    document.documentElement.style.backgroundColor = COLOR;
                    document.body.style.cssText = 'margin:0;min-height:100vh;background:' + COLOR;
                    return document.visibilityState;
                })()""".replace("COLOR", json.dumps(css))
                painted = attached.request("evaluate", {"target_id": target, "expression": expression}, threading.Event())
                visibility = cast(Json, json.loads(painted.text))
                assert visibility in ("hidden", "visible")
                shot = attached.request("screenshot", {"target_id": target}, threading.Event()).image
                assert shot is not None
                restored = attached.request("evaluate", {"target_id": target, "expression": "document.visibilityState"}, threading.Event())
                assert cast(Json, json.loads(restored.text)) == visibility
                image = QImage()
                assert image.loadFromData(shot)
                assert not image.isNull() and image.width() >= 4 and image.height() >= 4
                for x, y in ((1, 1), (3, 1), (2, 2), (1, 3), (3, 3)):
                    pixel = image.pixelColor(image.width() * x // 4, image.height() * y // 4).getRgb()
                    assert pixel == (*color, 255), (color, x, y, pixel)
                current_active, current, current_group = state()
                assert current_active == active and not current.active
                assert current.group_id == before.group_id and current_group == group
    finally:
        attached.close()
        personal.close()


def test_stopping_an_attached_action_detaches_without_terminating_the_browser(tmp_path: Path) -> None:
    installed()
    personal = Browser(tmp_path / "fixture-personal")
    attached = Browser(
        tmp_path / "unused", source=Product.EDGE,
        opener=NativeSessions(tmp_path / "unused", profiles=lambda _: personal.profile),
    )
    cancel = threading.Event()
    try:
        page = request(personal, "open", url="about:blank")
        request(attached, "attach")
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(attached.request, "evaluate",
                                    {"target_id": page["target_id"], "expression": "new Promise(() => {})"}, cancel)
            time.sleep(.15)
            cancel.set()
            with TestCase().assertRaises(InterruptedError):
                pending.result(timeout=3)
        assert attached.connection is None and personal.process is not None and personal.process.poll() is None
        assert request(personal, "snapshot", target_id=page["target_id"])["url"] == "about:blank"
    finally:
        cancel.set()
        attached.close()
        personal.close()
