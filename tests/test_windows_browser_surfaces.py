"""Native browser cards through public commands, capture and real Qt input."""

import base64
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast

if sys.platform != "win32":
    raise unittest.SkipTest("Windows browser surfaces")

from assistant import browser_surface_operations as ops
from assistant import browser_surface_values as v
from assistant import canvas_model as m
from assistant.browser_surfaces import BrowserSurfaces
from assistant.page_source_values import BrowserKind, Complete, Excerpt
from assistant.qt import Qt, QtCore, QTest, QtWidgets, shiboken6
from assistant.qt_ui import application

_PAGE = b"""<!doctype html><meta charset="utf-8"><title>Browser surface fixture</title>
<style>body{margin:0;font:16px sans-serif}input{position:absolute;left:20px;top:20px;width:220px;height:36px}
output{position:absolute;left:20px;top:90px}p{position:absolute;left:20px;top:140px}</style>
<input id="name" aria-label="Name"><output id="typed">Waiting for input</output><p id="isolation"></p>
<button id="guard" style="position:absolute;left:20px;top:200px">Protect unsaved edit</button>
<button id="store" style="position:absolute;left:250px;top:200px">Save session</button>
<button id="popup" style="position:absolute;left:20px;top:240px">Open popup</button>
<p id="stored" style="top:260px"></p>
<script>
document.querySelector('#name').addEventListener('input',e=>document.querySelector('#typed').textContent='Typed: '+e.target.value);
document.querySelector('#isolation').textContent=(typeof qt==='undefined'||!qt.webChannelTransport)?'Shell bridge absent':'Unexpected shell bridge';
document.querySelector('#guard').onclick=()=>{window.onbeforeunload=e=>{e.preventDefault();e.returnValue='';};
    document.querySelector('#typed').textContent='Protected unsaved edit';};
const stored=()=>document.querySelector('#stored').textContent='Stored: '+localStorage.getItem('surface-value')+'; '+document.cookie;
stored();
document.querySelector('#store').onclick=()=>{localStorage.setItem('surface-value',document.querySelector('#name').value);
    document.cookie='surface_session=retained; Max-Age=3600; SameSite=Lax';stored();};
document.querySelector('#popup').onclick=()=>window.open('/popup','toolkit-owned-popup','width=460,height=400');
</script>"""

_POPUP = _PAGE.replace(b"Browser surface fixture", b"Owned app popup").replace(
    b"<script>", b"<p style='top:320px' id='opener'></p><script>"
    b"document.querySelector('#opener').textContent=window.opener?'Opener retained':'No opener';",
)

_DOWNLOAD = b"Toolkit browser export\nAll requested lines.\n"
_LONG_PAGE = ("<!doctype html><meta charset='utf-8'><title>Long source</title><pre>"
              + "A" * 63_999 + "\U0001f642" + "Z" * 1_000 + "</pre>").encode("utf-8")
_FILES_PAGE = b"""<!doctype html><meta charset="utf-8"><title>Browser file fixture</title>
<style>body{margin:0;font:16px sans-serif}a,input,button,output{position:absolute;left:20px}
a{top:20px}input{top:80px;width:360px;height:36px}output{top:260px}</style>
<a href="/download" download>Export document</a><input type="file" id="upload" aria-label="Upload file">
<button id="notifications" style="top:140px">Request notifications</button>
<button id="filesystem" style="top:200px">Edit selected file</button><output id="status">Waiting for a chosen file</output>
<script>
const status=document.querySelector('#status');
document.querySelector('#upload').onchange=async e=>{const f=e.target.files[0];if(f)status.textContent='Chosen file: '+f.name+' / '+await f.text();};
document.querySelector('#notifications').onclick=async()=>status.textContent='Notifications: '+await Notification.requestPermission();
document.querySelector('#filesystem').onclick=async()=>{try{
    const [handle]=await showOpenFilePicker();const decision=await handle.requestPermission({mode:'readwrite'});
    if(decision!=='granted'){status.textContent='File access: '+decision;return;}
    const writer=await handle.createWritable();await writer.write('Saved through the chosen browser file.');await writer.close();
    status.textContent='Chosen file saved';
}catch(error){status.textContent='File access failed: '+error.name+': '+error.message;}};
</script>"""


@contextmanager
def site() -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = {"/download": _DOWNLOAD, "/files": _FILES_PAGE, "/popup": _POPUP, "/long": _LONG_PAGE}.get(self.path, _PAGE)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream" if self.path == "/download" else "text/html; charset=utf-8")
            if self.path == "/download":
                self.send_header("Content-Disposition", 'attachment; filename="export.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def until(root: QtWidgets.QApplication, condition: Callable[[], bool], *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("The browser surface did not reach the expected state.")
        root.processEvents()
        QTest.qWait(5)


def captured(root: QtWidgets.QApplication, owner: BrowserSurfaces, target: v.BrowserTarget) -> v.CaptureResult:
    results: list[v.CaptureResult] = []
    owner.capture(target, results.append)
    until(root, lambda: bool(results))
    return results[0]


def contains(root: QtWidgets.QApplication, owner: BrowserSurfaces, target: v.BrowserTarget, expected: str) -> bool:
    value = captured(root, owner, target)
    return isinstance(value, v.CapturedPage) and expected in value.text


def click(host: QtWidgets.QWidget, x: int, y: int) -> None:
    point = QtCore.QPoint(x, y)
    widget = host.childAt(point)
    assert widget is not None
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget.mapFrom(host, point))


def controlled(root: QtWidgets.QApplication, owner: BrowserSurfaces, operation: ops.AppOperation) -> ops.AppResult:
    results: list[ops.AppResult] = []
    owner.control(operation, threading.Event(), results.append)
    until(root, lambda: bool(results))
    return results[0]


def response(result: ops.AppResult) -> dict[str, ops.Json]:
    assert isinstance(result, ops.AppSucceeded), result
    value = cast(ops.Json, json.loads(result.text))
    assert isinstance(value, dict)
    return value


@dataclass(frozen=True, slots=True)
class Decision:
    text: str
    text_format: Qt.TextFormat
    default: QtWidgets.QMessageBox.StandardButton
    choice: QtWidgets.QMessageBox.StandardButton


@contextmanager
def decisions(
    root: QtWidgets.QApplication, host: QtWidgets.QWidget, choices: tuple[QtWidgets.QMessageBox.StandardButton, ...],
) -> Generator[list[Decision]]:
    pending = list(choices)
    answered: list[Decision] = []

    def answer() -> None:
        dialog = root.activeModalWidget()
        if isinstance(dialog, QtWidgets.QMessageBox) and pending:
            choice = pending.pop(0)
            answered.append(Decision(dialog.text(), dialog.textFormat(), dialog.standardButton(dialog.defaultButton()), choice))
            button = dialog.button(choice)
            assert button is not None
            button.click()

    timer = QtCore.QTimer(host)
    timer.setInterval(5)
    timer.timeout.connect(answer)
    timer.start()
    try:
        yield answered
    finally:
        timer.stop()


@contextmanager
def file_choices(
    root: QtWidgets.QApplication, host: QtWidgets.QWidget, paths: tuple[Path, ...],
) -> Generator[list[str]]:
    attribute = Qt.ApplicationAttribute.AA_DontUseNativeDialogs
    previous = root.testAttribute(attribute)
    root.setAttribute(attribute, True)
    pending = list(paths)
    selected: list[str] = []

    def choose() -> None:
        dialog = root.activeModalWidget()
        if isinstance(dialog, QtWidgets.QFileDialog) and pending:
            path = str(pending.pop(0))
            selected.append(path)
            dialog.selectFile(path)
            dialog.accept()

    timer = QtCore.QTimer(host)
    timer.setInterval(5)
    timer.timeout.connect(choose)
    timer.start()
    try:
        yield selected
    finally:
        timer.stop()
        root.setAttribute(attribute, previous)


@contextmanager
def window(tmp_path: Path) -> Generator[tuple[QtWidgets.QApplication, QtWidgets.QWidget, BrowserSurfaces]]:
    root = application()
    host = QtWidgets.QWidget()
    host.setWindowTitle("Toolkit browser surface test")
    host.setGeometry(80, 80, 800, 600)
    owner = BrowserSurfaces(host, tmp_path / "profiles", lambda: None)
    try:
        host.show()
        yield root, host, owner
    finally:
        owner.close()
        host.hide()
        shiboken6.delete(host)
        root.processEvents()


def test_browser_card_accepts_real_input_and_captures_the_same_isolated_document(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, host, owner):
        owner.sync((v.SurfaceSpec("app", m.WebApp(url + "one", "default")),))
        rect = v.CssRect(100, 100, 500, 350)
        owner.present(v.ViewportFrame("main", "layout-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        entry = owner.snapshot().entries[0]
        assert entry.target is not None and entry.url == url + "one"
        target = entry.target
        initial = captured(root, owner, target)
        assert isinstance(initial, v.CapturedPage) and "Shell bridge absent" in initial.text
        click(host, 160, 140)
        focus = root.focusWidget()
        assert focus is not None
        QTest.keyClicks(focus, "Real keyboard input")
        until(root, lambda: isinstance(value := captured(root, owner, target), v.CapturedPage)
              and "Typed: Real keyboard input" in value.text)
        result = captured(root, owner, target)
        assert isinstance(result, v.CapturedPage) and "Typed: Real keyboard input" in result.text
        assert result.target == entry.target and result.url == entry.url


def test_registered_page_operations_reject_stale_controls_documents_and_cancelled_work(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, _, owner):
        owner.sync((v.SurfaceSpec("app", m.WebApp(url + "one", "default")),))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        entry = owner.snapshot().entries[0]
        assert entry.target is not None
        listing = response(controlled(root, owner, ops.ListApps()))
        apps = listing["apps"]
        assert isinstance(apps, list) and len(apps) == 1
        app = apps[0]
        assert isinstance(app, dict) and app["item_id"] == "app" and app["url"] == url + "one"
        target_id = app["target_id"]
        assert isinstance(target_id, str)
        snapshot = response(controlled(root, owner, ops.SnapshotApp(target_id)))
        assert snapshot["target_id"] == target_id and snapshot["item_id"] == "app"
        controls = snapshot["controls"]
        assert isinstance(controls, list) and isinstance(controls[0], dict)
        control_id = controls[0]["id"]
        assert isinstance(control_id, str)
        assert isinstance(controlled(root, owner, ops.FillApp(target_id, control_id, "Agent field text")), ops.AppSucceeded)
        readback = captured(root, owner, entry.target)
        assert isinstance(readback, v.CapturedPage) and "Typed: Agent field text" in readback.text
        response(controlled(root, owner, ops.SnapshotApp(target_id)))
        assert isinstance(controlled(root, owner, ops.FillApp(target_id, control_id, "Stale edit")), ops.AppFailed)
        owner.dispatch(v.Reload("app"))
        until(root, lambda: any(item.target is not None and item.target != entry.target for item in owner.snapshot().entries))
        assert isinstance(controlled(root, owner, ops.FillApp(target_id, control_id, "Wrong document")), ops.AppFailed)
        cancelled = threading.Event()
        results: list[ops.AppResult] = []
        owner.control(ops.ListApps(), cancelled, results.append)
        cancelled.set()
        until(root, lambda: bool(results))
        assert results == [ops.AppCancelled.REQUEST]


def test_cancelled_beforeunload_retires_only_its_own_close_request(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, host, owner):
        owner.sync((v.SurfaceSpec("app", m.WebApp(url + "one", "default")),))
        rect = v.CssRect(100, 100, 500, 350)
        owner.present(v.ViewportFrame("main", "layout-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        original = owner.snapshot().entries[0].target
        assert original is not None
        click(host, 180, 310)
        until(root, lambda: isinstance(value := captured(root, owner, original), v.CapturedPage) and "Protected unsaved edit" in value.text)
        first: list[v.CloseResult] = []
        second: list[v.CloseResult] = []
        with decisions(root, host, (QtWidgets.QMessageBox.StandardButton.No, QtWidgets.QMessageBox.StandardButton.Yes)) as answered:
            cancelled = owner.request_close("app", first.append)
            until(root, lambda: bool(first))
            assert first == [v.CloseCancelled("app", cancelled)]
            assert owner.snapshot().entries[0].target == original
            accepted = owner.request_close("app", second.append)
            until(root, lambda: bool(second))
            assert second == [v.Closed("app", accepted)] and accepted != cancelled
            assert first == [v.CloseCancelled("app", cancelled)] and len(answered) == 2
            assert isinstance(captured(root, owner, original), v.CaptureFailed)


def test_owned_popup_preserves_opener_profile_and_real_input_across_canvas_repaint(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, host, owner):
        specs = (v.SurfaceSpec("app", m.WebApp(url + "one", "default")),)
        owner.sync(specs)
        rect = v.CssRect(100, 100, 500, 400)
        owner.present(v.ViewportFrame("main", "layout-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        parent_target = owner.snapshot().entries[0].target
        assert parent_target is not None
        click(host, 160, 140)
        focus = root.focusWidget()
        assert focus is not None
        QTest.keyClicks(focus, "Parent session")
        click(host, 400, 310)
        until(root, lambda: isinstance(value := captured(root, owner, parent_target), v.CapturedPage) and "Stored: Parent session" in value.text)
        click(host, 170, 350)
        until(root, lambda: len(owner.snapshot().entries) == 2 and all(entry.target is not None for entry in owner.snapshot().entries))
        popup = next(entry for entry in owner.snapshot().entries if entry.item_id != "app")
        assert popup.target is not None
        initial = captured(root, owner, popup.target)
        assert isinstance(initial, v.CapturedPage)
        assert "Opener retained" in initial.text and "Stored: Parent session" in initial.text
        assert "surface_session=retained" in initial.text and "Shell bridge absent" in initial.text
        owner.sync(specs)
        owner.present(v.ViewportFrame("main", "layout-one", 2, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        popup_window = next(widget for widget in root.topLevelWidgets() if widget.windowTitle() == "Owned app popup")
        click(popup_window, 60, 40)
        focus = root.focusWidget()
        assert focus is not None
        QTest.keyClicks(focus, "Popup keyboard input")
        popup_target = popup.target
        until(root, lambda: isinstance(value := captured(root, owner, popup_target), v.CapturedPage)
              and "Typed: Popup keyboard input" in value.text)
        closed: list[v.CloseResult] = []
        request = owner.request_close(popup.item_id, closed.append)
        until(root, lambda: bool(closed))
        assert closed == [v.Closed(popup.item_id, request)]
        assert owner.snapshot().entries[0].target == parent_target and len(owner.snapshot().entries) == 1


def test_parent_close_respects_a_refusing_edited_popup_before_closing_the_app(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, host, owner):
        owner.sync((v.SurfaceSpec("app", m.WebApp(url + "one", "default")),))
        rect = v.CssRect(100, 100, 500, 400)
        owner.present(v.ViewportFrame("main", "layout-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        parent = owner.snapshot().entries[0].target
        assert parent is not None
        click(host, 170, 350)
        until(root, lambda: len(owner.snapshot().entries) == 2 and all(entry.target is not None for entry in owner.snapshot().entries))
        popup = next(entry for entry in owner.snapshot().entries if entry.item_id != "app").target
        assert popup is not None
        popup_window = next(widget for widget in root.topLevelWidgets() if widget.windowTitle() == "Owned app popup")
        click(popup_window, 80, 210)
        until(root, lambda: isinstance(value := captured(root, owner, popup), v.CapturedPage) and "Protected unsaved edit" in value.text)
        refused: list[v.CloseResult] = []
        accepted: list[v.CloseResult] = []
        with decisions(root, host, (QtWidgets.QMessageBox.StandardButton.No, QtWidgets.QMessageBox.StandardButton.Yes)) as answered:
            first = owner.request_close("app", refused.append)
            until(root, lambda: bool(refused))
            assert refused == [v.CloseCancelled("app", first)]
            assert {entry.target for entry in owner.snapshot().entries} == {parent, popup}
            second = owner.request_close("app", accepted.append)
            until(root, lambda: bool(accepted))
            assert accepted == [v.Closed("app", second)] and second != first
            assert len(answered) == 2 and refused == [v.CloseCancelled("app", first)]
            assert isinstance(captured(root, owner, popup), v.CaptureFailed)


def test_clipping_and_stale_modal_frames_preserve_the_live_page_and_bound_its_transient_preview(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, host, owner):
        underlay = QtWidgets.QPushButton("Canvas beneath the browser", host)
        underlay.setGeometry(0, 0, 800, 600)
        underlay.setStyleSheet("background:#173e50;border:0")
        underlay.show()
        hits: list[int] = []
        underlay.clicked.connect(lambda: hits.append(1))
        specs = (v.SurfaceSpec("app", m.WebApp(url + "one", "default")),)
        owner.sync(specs)
        rect, viewport = v.CssRect(100, 100, 500, 400), v.CssRect(0, 0, 800, 600)
        owner.present(v.ViewportFrame("main", "layout-one", 1, viewport, v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        target = owner.snapshot().entries[0].target
        assert target is not None
        click(host, 160, 140)
        focus = root.focusWidget()
        assert focus is not None
        QTest.keyClicks(focus, "Retain this editor")
        until(root, lambda: isinstance(value := captured(root, owner, target), v.CapturedPage) and "Typed: Retain this editor" in value.text)
        clips = (v.CssRect(100, 100, 100, 400), v.CssRect(400, 100, 200, 400))
        owner.present(v.ViewportFrame("main", "layout-one", 2, viewport, v.Visible((v.SurfaceFrame("app", rect, clips, 1),))))
        click(host, 260, 140)
        assert hits == [1], "A browser clipped behind HTML must not receive that pointer."
        pixel = host.grab().toImage().pixelColor(260, 140)
        assert (pixel.red(), pixel.green(), pixel.blue()) == (23, 62, 80)
        host.resize(1400, 1000)
        large = v.CssRect(100, 100, 1100, 800)
        owner.present(v.ViewportFrame("main", "layout-one", 3, v.CssRect(0, 0, 1400, 1000),
                                      v.Visible((v.SurfaceFrame("app", large, (large,), 1),))))
        previews = owner.previews()
        assert len(previews) == 1 and previews[0][0] == "app" and previews[0][1].startswith("data:image/png;base64,")
        assert sum(len(data) for _, data in previews) <= 3 * 1024 * 1024
        png = base64.b64decode(previews[0][1].split(",", 1)[1])
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and png[12:16] == b"IHDR"
        assert max(int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")) == 720
        owner.present(v.ViewportFrame("main", "modal-two", 0, viewport, v.Suspended(v.Suspension.MODAL)))
        assert owner.previews() == ()
        owner.present(v.ViewportFrame("main", "layout-one", 99, viewport, v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        click(host, 160, 140)
        assert hits == [1, 1], "A stale frame cannot reveal the page over a modal."
        owner.sync(specs)
        owner.present(v.ViewportFrame("main", "modal-two", 1, viewport, v.Visible((v.SurfaceFrame("app", rect, (rect,), .75),))))
        result = captured(root, owner, target)
        assert isinstance(result, v.CapturedPage) and "Typed: Retain this editor" in result.text
        assert owner.snapshot().entries[0].target == target


def test_upload_and_download_use_the_files_chosen_in_the_browser_dialogs(tmp_path: Path) -> None:
    source, destination = tmp_path / "chosen.txt", tmp_path / "export.txt"
    source.write_text("Selected upload content.", encoding="utf-8")
    with site() as url, window(tmp_path) as (root, host, owner):
        owner.sync((v.SurfaceSpec("files", m.WebApp(url + "files", "default")),))
        rect = v.CssRect(0, 0, 760, 560)
        owner.present(v.ViewportFrame("main", "files-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("files", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        target = owner.snapshot().entries[0].target
        assert target is not None
        with file_choices(root, host, (source, destination)) as selected:
            click(host, 100, 100)
            until(root, lambda: isinstance(value := captured(root, owner, target), v.CapturedPage)
                  and "Chosen file: chosen.txt / Selected upload content." in value.text)
            click(host, 80, 28)
            until(root, lambda: destination.is_file() and destination.read_bytes() == _DOWNLOAD)
            assert selected == [str(source), str(destination)]


def test_site_permissions_follow_the_choice_in_an_origin_labelled_default_no_dialog(tmp_path: Path) -> None:
    for profile, choice, expected in (
        ("allow", QtWidgets.QMessageBox.StandardButton.Yes, "granted"),
        ("refuse", QtWidgets.QMessageBox.StandardButton.No, "denied"),
    ):
        with site() as url, window(tmp_path / profile) as (root, host, owner):
            owner.sync((v.SurfaceSpec("files", m.WebApp(url + "files", profile)),))
            rect = v.CssRect(0, 0, 760, 560)
            owner.present(v.ViewportFrame("main", "permission-one", 1, v.CssRect(0, 0, 800, 600),
                                          v.Visible((v.SurfaceFrame("files", rect, (rect,), 1),))))
            until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
            target = owner.snapshot().entries[0].target
            assert target is not None
            with decisions(root, host, (choice,)) as answered:
                click(host, 100, 150)
                until(root, partial(contains, root, owner, target, "Notifications: " + expected))
                assert len(answered) == 1 and answered[0].choice == choice
                assert url.rstrip("/") in answered[0].text and "notifications" in answered[0].text
                assert answered[0].text_format == Qt.TextFormat.PlainText
                assert answered[0].default == QtWidgets.QMessageBox.StandardButton.No


def test_navigation_retires_a_permission_dialog_without_deciding_for_the_next_document(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, host, owner):
        owner.sync((v.SurfaceSpec("files", m.WebApp(url + "files", "default")),))
        rect = v.CssRect(0, 0, 760, 560)
        owner.present(v.ViewportFrame("main", "permission-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("files", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        original = owner.snapshot().entries[0].target
        assert original is not None
        retired: list[str] = []

        def navigate() -> None:
            dialog = root.activeModalWidget()
            if isinstance(dialog, QtWidgets.QMessageBox) and not retired:
                retired.append(dialog.text())
                owner.dispatch(v.Navigate("files", url + "files"))

        timer = QtCore.QTimer(host)
        timer.setInterval(5)
        timer.timeout.connect(navigate)
        timer.start()
        try:
            click(host, 100, 150)
            until(root, lambda: any(entry.target is not None and entry.target != original for entry in owner.snapshot().entries))
        finally:
            timer.stop()
        assert len(retired) == 1 and url.rstrip("/") in retired[0]
        assert root.activeModalWidget() is None
        current = owner.snapshot().entries[0].target
        assert current is not None
        with decisions(root, host, (QtWidgets.QMessageBox.StandardButton.Yes,)) as answered:
            click(host, 100, 150)
            until(root, partial(contains, root, owner, current, "Notifications: granted"))
            assert len(answered) == 1, "The retired question must neither grant nor deny the next document's permission."


def file_access_choice(
    folder: Path, choice: QtWidgets.QMessageBox.StandardButton, expected_status: str, expected_content: str,
) -> None:
    # Chromium protects AppData/Temp from File System Access. Exercise a private
    # Downloads file without disabling that browser restriction.
    downloads = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.DownloadLocation)
    with (
        tempfile.TemporaryDirectory(prefix="toolkit-browser-file-test-", dir=downloads) as selected_folder,
        site() as url,
        window(folder) as (root, host, owner),
    ):
        source = Path(selected_folder) / "chosen.txt"
        source.write_text("Original selected file.", encoding="utf-8")
        owner.sync((v.SurfaceSpec("files", m.WebApp(url + "files", "default")),))
        rect = v.CssRect(0, 0, 760, 560)
        owner.present(v.ViewportFrame("main", "filesystem-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("files", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        target = owner.snapshot().entries[0].target
        assert target is not None
        with file_choices(root, host, (source,)) as selected, decisions(
            root, host, (choice,) * 2,
        ) as answered:
            click(host, 100, 210)
            until(root, lambda: isinstance(value := captured(root, owner, target), v.CapturedPage)
                  and ("Chosen file saved" in value.text or "File access failed:" in value.text or "File access: denied" in value.text))
            assert selected == [str(source)]
            assert answered and all(url.rstrip("/") in item.text and str(source) in item.text for item in answered), (
                answered, captured(root, owner, target),
            )
            assert all(item.default == QtWidgets.QMessageBox.StandardButton.No and item.text_format == Qt.TextFormat.PlainText for item in answered)
            assert contains(root, owner, target, expected_status), captured(root, owner, target)
            assert source.read_text(encoding="utf-8") == expected_content


def test_chosen_browser_file_is_written_only_after_the_origin_and_path_are_approved(tmp_path: Path) -> None:
    file_access_choice(tmp_path / "refuse", QtWidgets.QMessageBox.StandardButton.No, "File access: denied", "Original selected file.")
    file_access_choice(tmp_path / "allow", QtWidgets.QMessageBox.StandardButton.Yes, "Chosen file saved", "Saved through the chosen browser file.")


def test_source_capture_bounds_utf16_without_splitting_text_and_rejects_a_navigation_race(tmp_path: Path) -> None:
    with site() as url, window(tmp_path) as (root, _, owner):
        owner.sync((v.SurfaceSpec("app", m.WebApp(url + "long", "default")),))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        target = owner.snapshot().entries[0].target
        assert target is not None
        excerpt = captured(root, owner, target)
        assert isinstance(excerpt, v.CapturedPage)
        assert excerpt.text == "A" * 63_999 and excerpt.scope == Excerpt(0, 63_999, 65_001)
        assert excerpt.observation.reference.browser is BrowserKind.TOOLKIT
        pending: list[v.CaptureResult] = []
        owner.capture(target, pending.append)
        owner.dispatch(v.Navigate("app", url + "one"))
        until(root, lambda: bool(pending))
        assert len(pending) == 1 and isinstance(pending[0], v.CaptureFailed) and pending[0].target == target
        until(root, lambda: any(entry.target is not None and entry.target != target for entry in owner.snapshot().entries))
        current = owner.snapshot().entries[0].target
        assert current is not None
        complete = captured(root, owner, current)
        assert isinstance(complete, v.CapturedPage) and complete.url == url + "one"
        assert complete.scope is Complete.MAIN_DOCUMENT and "Shell bridge absent" in complete.text
        assert len(pending) == 1


def profile_session(folder: Path, url: str, action: Literal["save", "restore", "isolated"]) -> None:
    with window(folder) as (root, host, owner):
        owner.sync((v.SurfaceSpec("app", m.WebApp(url + "one", "isolated" if action == "isolated" else "default")),))
        rect = v.CssRect(0, 0, 760, 560)
        owner.present(v.ViewportFrame("main", "profile-one", 1, v.CssRect(0, 0, 800, 600),
                                      v.Visible((v.SurfaceFrame("app", rect, (rect,), 1),))))
        until(root, lambda: any(entry.target is not None for entry in owner.snapshot().entries))
        target = owner.snapshot().entries[0].target
        assert target is not None
        if action == "save":
            click(host, 80, 40)
            focus = root.focusWidget()
            assert focus is not None
            QTest.keyClicks(focus, "Session across processes")
            click(host, 300, 210)
            until(root, partial(contains, root, owner, target, "Stored: Session across processes"))
        result = captured(root, owner, target)
        assert isinstance(result, v.CapturedPage) and "Shell bridge absent" in result.text
        if action == "isolated":
            assert "Stored: null;" in result.text and "surface_session=retained" not in result.text
        else:
            assert "Stored: Session across processes; surface_session=retained" in result.text
    print("Browser profile " + action + " passed", flush=True)


def test_profiles_restore_local_storage_and_cookies_in_a_fresh_owner_process(tmp_path: Path) -> None:
    script = (
        "import sys\nfrom pathlib import Path\n"
        "from runtime import activate\nactivate()\n"
        "from tests.test_windows_browser_surfaces import profile_session\n"
        "profile_session(Path(sys.argv[1]), sys.argv[2], sys.argv[3])\n"
    )
    with site() as url:
        for action in ("save", "restore", "isolated"):
            checked = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", "-c", script, str(tmp_path), url, action],
                cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=45, check=False,
            )
            output = (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
            assert checked.returncode == 0, f"Profile {action} exited {checked.returncode & 0xffffffff:#x}: {output}"
            assert "Browser profile " + action + " passed" in output
