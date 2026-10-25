"""Real browser cards remain interactive inside the sketch's HTML canvas."""

import json
import sys
import threading
import unittest
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows embedded canvas applications")

from assistant import browser_surface_operations as ops
from assistant import browser_surface_values as browser
from assistant import canvas_model as m
from assistant import canvas_web_contract as contract
from assistant.browser_surfaces import BrowserSurfaces
from assistant.canvas_web import DesktopWindow
from assistant.llm import Json
from assistant.page_source_values import BrowserKind
from assistant.qt import Qt, QtCore, QTest, QtGui, QtWidgets

from .test_canvas_persistence import GatedStore
from .test_windows_canvas_web import click, drag_pointer, js, rendered, until, window

# isort: split
from PySide6 import QtWebEngineCore, QtWebEngineWidgets

_PAGE = b'''<!doctype html><meta charset="utf-8"><title>Working document</title>
<style>body{margin:0;font:16px sans-serif}input{position:absolute;left:20px;top:20px;width:230px;height:36px}
output{position:absolute;left:20px;top:90px}</style>
<input aria-label="Working text"><output>Waiting</output>
<script>document.querySelector('input').oninput=e=>document.querySelector('output').textContent='Edited: '+e.target.value;</script>'''


@contextmanager
def site() -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/document")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PAGE)))
            self.end_headers()
            self.wfile.write(_PAGE)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def ready_app(desktop: DesktopWindow, identity: str) -> browser.BrowserTarget:
    until(desktop.root, lambda: any(entry.item_id == identity and entry.target is not None for entry in desktop.applications.snapshot().entries))
    target = next(entry.target for entry in desktop.applications.snapshot().entries if entry.item_id == identity)
    assert target is not None
    rendered(desktop, "!!document.querySelector('[data-application-viewport]')")
    return target


def browser_view(desktop: DesktopWindow) -> QtWebEngineWidgets.QWebEngineView:
    views = [view for view in desktop.surface.findChildren(QtWebEngineWidgets.QWebEngineView) if view is not desktop.view]
    assert len(views) == 1
    until(desktop.root, views[0].isVisible)
    return views[0]


def page_text(desktop: DesktopWindow, target: browser.BrowserTarget) -> str:
    results: list[browser.CaptureResult] = []
    desktop.applications.capture(target, results.append)
    until(desktop.root, lambda: bool(results))
    result = results[0]
    assert isinstance(result, browser.CapturedPage), result
    return result.text


def child_view(child: QtWidgets.QWidget | None) -> QtWebEngineWidgets.QWebEngineView | None:
    while child is not None:
        if isinstance(child, QtWebEngineWidgets.QWebEngineView):
            return child
        child = child.parentWidget()
    return None


def pan_before_release(desktop: DesktopWindow, view: QtWebEngineWidgets.QWebEngineView) -> None:
    before = view.geometry()
    start, end = QtCore.QPoint(8, 400), QtCore.QPoint(88, 440)
    target = desktop.view.focusProxy()
    assert target is not None
    QTest.mousePress(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    desktop.root.processEvents()
    event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(end), QtCore.QPointF(target.mapToGlobal(end)),
                             Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(target, event)
    try:
        until(desktop.root, lambda: view.x() != before.x())
        assert abs(view.x() - before.x() - 80) <= 2 and abs(view.y() - before.y() - 40) <= 2
    finally:
        QTest.mouseRelease(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)


def test_canvas_application_accepts_input_and_captures_a_separate_source(tmp_path: Path) -> None:
    with site() as address, window(tmp_path) as (desktop, _):
        js(desktop, "(()=>{openWebAppDialog();document.querySelector('#web-app-url').value=" + json.dumps(address + "/document")
           + ";document.querySelector('#web-app-form').requestSubmit();return true;})()")
        until(desktop.root, lambda: any(isinstance(item.content, m.WebApp) for item in desktop.controller.state.items))
        item = next(item for item in desktop.controller.state.items if isinstance(item.content, m.WebApp))
        target = ready_app(desktop, item.id)
        view = browser_view(desktop)
        rendered(desktop, "document.getElementById('view-description').textContent.startsWith('1 app')")
        initial_selection = desktop.controller.state.space.selection
        assert initial_selection == ()
        point = QtCore.QPoint(view.x() + round(60 * view.zoomFactor()), view.y() + round(40 * view.zoomFactor()))
        child = desktop.surface.childAt(point)
        assert child is not None and child_view(child) is view
        QTest.mouseClick(child, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, child.mapFrom(desktop.surface, point))
        focus = desktop.root.focusWidget()
        assert focus is not None
        QTest.keyClicks(focus, "Actual canvas input")
        until(desktop.root, lambda: "Edited: Actual canvas input" in page_text(desktop, target))
        click(desktop, '[data-application-action="capture"]')
        until(desktop.root, lambda: any(isinstance(value.content, m.Source) for value in desktop.controller.state.items))
        source = next(value for value in desktop.controller.state.items if isinstance(value.content, m.Source))
        assert isinstance(source.content, m.Source) and isinstance(source.content.origin, m.WebPage)
        provenance = source.content.origin.provenance
        assert provenance is not None and provenance.reference.browser is BrowserKind.TOOLKIT
        assert provenance.reference.binding_id == target.instance_id and provenance.reference.target_id == target.document_id
        assert "Edited: Actual canvas input" in source.content.text
        assert desktop.controller.state.space.selection == (source.id,)
        retained = desktop.controller.state.item(item.id)
        assert retained is not None and isinstance(retained.content, m.WebApp)
        assert not js(desktop, "document.querySelector('[data-node=" + json.dumps(source.id) + "] [data-item-action=refresh]')!==null")
        agent_calls: list[str] = []
        desktop.actions = replace(desktop.actions, agent=lambda: agent_calls.append(item.id))
        click(desktop, '[data-application-action="ask_agent"]')
        until(desktop.root, lambda: agent_calls == [item.id])


def test_app_bounds_follow_canvas_and_suspend_under_the_full_modal(tmp_path: Path) -> None:
    with site() as address, window(tmp_path) as (desktop, _):
        identity = desktop.create_web_app(m.WebApp(address + "/document", "default"))
        target = ready_app(desktop, identity)
        view = browser_view(desktop)
        before = view.geometry()
        drag_pointer(desktop, '[data-drag="' + identity + '"] .title', 100, 45)
        until(desktop.root, lambda: view.x() != before.x())
        assert abs(view.x() - before.x() - 100) <= 2 and abs(view.y() - before.y() - 45) <= 2
        js(desktop, "(()=>{zoom(.75);return true;})()")
        until(desktop.root, lambda: view.width() < before.width() * .8)
        pan_before_release(desktop, view)
        stale = js(desktop, "({kind:'app_layout',space:canvasUI.state().space.id,epoch:canvasUI.state().application_epoch,"
                   "sequence:100000,slots:applicationSlots()})")
        assert isinstance(stale, dict)
        js(desktop, "(()=>{runAction('launcher');return true;})()")
        until(desktop.root, view.isHidden)
        coverage = js(desktop, "(()=>{const r=document.getElementById('scrim').getBoundingClientRect();"
                      "return r.x===0&&r.y===0&&r.width===innerWidth&&r.height===innerHeight;})()")
        assert coverage is True
        assert child_view(desktop.surface.childAt(1, 1)) is desktop.view
        rendered(desktop, "!document.querySelector('.application-snapshot').hidden&&"
                 "document.querySelector('.application-snapshot').src.startsWith('data:image/png;base64,')")
        assert "data:image" not in json.dumps(contract.snapshot(desktop.controller.state))
        js(desktop, "(()=>{closeDialog();return true;})()")
        until(desktop.root, view.isVisible)
        assert js(desktop, "document.querySelector('.application-snapshot').hasAttribute('src')") is False
        first_space = desktop.controller.state.current
        desktop.controller.new_space("Other work")
        until(desktop.root, view.isHidden)
        desktop.dispatch(contract.SwitchSpace(first_space))
        until(desktop.root, view.isVisible)
        before_stale = view.geometry()
        desktop.dispatch(contract.command(json.dumps(stale)))
        desktop.root.processEvents()
        assert view.geometry() == before_stale
        desktop.surface.hide()
        assert not view.isVisible()
        desktop.show()
        until(desktop.root, view.isVisible)
        assert browser.current_target(desktop.applications.snapshot(), target) is not None


def test_agent_open_receipt_waits_for_save_and_accepts_the_login_redirect(tmp_path: Path) -> None:
    with site() as address, window(tmp_path) as (desktop, _):
        store = GatedStore(desktop.controller.store.path)
        desktop.controller.store = store
        replies: list[ops.AppResult] = []
        requested = address + "/redirect"
        try:
            desktop.browser_request(ops.OpenApp(requested), threading.Event(), replies.append)
            assert store.entered.wait(2)
            item = next(value for value in desktop.controller.state.items if isinstance(value.content, m.WebApp))
            ready_app(desktop, item.id)
            until(desktop.root, lambda: any(isinstance(value.content, m.WebApp) and value.content.url == address + "/document"
                                           for value in desktop.controller.state.items))
            assert not replies
            store.release.set()
            until(desktop.root, lambda: bool(replies))
            result = replies[0]
            assert isinstance(result, ops.AppSucceeded), result
            receipt = cast(Json, json.loads(result.text))
            assert receipt == {"operation": "open", "item_id": item.id, "requested_url": requested, "saved": True}
            assert len(replies) == 1
        finally:
            store.release.set()


def test_open_cancellation_never_recreates_or_claims_an_unsaved_card(tmp_path: Path) -> None:
    with site() as address, window(tmp_path) as (desktop, _):
        cancelled = threading.Event()
        cancelled.set()
        before = desktop.controller.state
        replies: list[ops.AppResult] = []
        desktop.browser_request(ops.OpenApp(address + "/document"), cancelled, replies.append)
        assert replies == [ops.AppCancelled.REQUEST] and desktop.controller.state == before
        store = GatedStore(desktop.controller.store.path)
        desktop.controller.store = store
        running = threading.Event()
        try:
            desktop.browser_request(ops.OpenApp(address + "/document"), running, replies.append)
            assert store.entered.wait(2)
            running.set()
            until(desktop.root, lambda: len(replies) == 2)
            assert isinstance(replies[1], ops.AppFailed) and "could not be confirmed" in replies[1].reason
            assert sum(isinstance(value.content, m.WebApp) for value in desktop.controller.state.items) == 1
            store.release.set()
            desktop.tick()
            assert len(replies) == 2
        finally:
            store.release.set()


def test_failed_application_save_reports_the_created_card_without_claiming_persistence(tmp_path: Path) -> None:
    with site() as address, window(tmp_path) as (desktop, _):
        store = GatedStore(desktop.controller.store.path, failure=True)
        store.release.set()
        desktop.controller.store = store
        replies: list[ops.AppResult] = []
        try:
            desktop.browser_request(ops.OpenApp(address + "/document"), threading.Event(), replies.append)
            until(desktop.root, lambda: bool(replies))
            result = replies[0]
            assert isinstance(result, ops.AppFailed) and "created" in result.reason and "save failed" in result.reason
            app = next(value for value in desktop.controller.state.items if isinstance(value.content, m.WebApp))
            assert app.id in result.reason
            saved = desktop.controller.saved_state
            assert saved is not None and saved.item(app.id) is None
            assert len(replies) == 1
        finally:
            store.failure = False
            desktop.controller.commit(m.with_space(desktop.controller.state, replace(desktop.controller.state.space, composer="Recovered save")))


def test_cancelled_close_intent_cannot_delete_a_card_on_a_later_close(tmp_path: Path) -> None:
    with site() as address, window(tmp_path) as (desktop, _):
        identity = desktop.create_web_app(m.WebApp(address + "/document", "default"))
        ready_app(desktop, identity)
        replies: list[Callable[[browser.CloseResult], None]] = []
        def request_close(_owner: BrowserSurfaces, item_id: str, finished: Callable[[browser.CloseResult], None]) -> str:
            assert item_id == identity
            replies.append(finished)
            return f"attempt-{len(replies)}"
        with patch.object(BrowserSurfaces, "request_close", request_close):
            desktop.item_action(identity, contract.ItemAction.DELETE)
            assert desktop.controller.state.item(identity) is not None
            replies[0](browser.CloseCancelled(identity, "attempt-1"))
            desktop.item_action(identity, contract.ItemAction.REMOVE)
            replies[0](browser.Closed(identity, "attempt-1"))
            assert any(value.item_id == identity for value in desktop.controller.state.space.placements)
            replies[1](browser.Closed(identity, "attempt-2"))
        assert not any(value.item_id == identity for value in desktop.controller.state.space.placements)
        assert desktop.controller.state.item(identity) is not None


def test_a_self_closed_application_leaves_layout_and_reloads_into_the_same_card(tmp_path: Path) -> None:
    with site() as address, window(tmp_path) as (desktop, _):
        identity = desktop.create_web_app(m.WebApp(address + "/document", "default"))
        original = ready_app(desktop, identity)
        view = browser_view(desktop)
        view.page().triggerAction(QtWebEngineCore.QWebEnginePage.WebAction.RequestClose)
        until(desktop.root, lambda: any(entry.item_id == identity and entry.state is browser.Dormant.CLOSED
                                       for entry in desktop.applications.snapshot().entries))
        rendered(desktop, "canvasUI.state().items.find(item=>item.kind==='application').application.live===false")
        assert js(desktop, "applicationSlots().length") == 0
        click(desktop, '[data-application-action="reload"]')
        reopened = ready_app(desktop, identity)
        browser_view(desktop)
        assert reopened.instance_id != original.instance_id and reopened.item_id == original.item_id
        assert len(desktop.controller.state.items) == 1
