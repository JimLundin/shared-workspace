"""Real Qt canvas controls, isolated storage, no foreground input or live integrations."""

import ctypes
import shutil
import subprocess
import sys
import threading
import unittest
from collections.abc import Callable, Generator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Native Windows canvas widgets")

from assistant import canvas_model as m
from assistant.canvas_native import Catalogue, NativeResources, TaskbarLease, file_item
from assistant.canvas_store import CanvasStore
from assistant.canvas_ui import DesktopActions, DesktopWindow
from assistant.qt import Qt, QtCore, QtGui, QtWidgets
from assistant.qt_ui import EventLoop, application
from automation.files import filesystem_path
from winapi import desktop_shell, screen
from winapi._core import fn, kernel32
from winapi.processes import Process
from winapi.shapes import Rect

from .test_canvas import RecordingBackend, selected_canvas


def pump(desktop: DesktopWindow) -> None:
    desktop.root.processEvents()
    desktop.controller.drain()
    desktop.loop.pump()


@contextmanager
def canvas_window(tmp_path: Path) -> Generator[tuple[DesktopWindow, RecordingBackend]]:
    screen.use_physical_pixels()
    root = application()
    backend = RecordingBackend()
    def nothing() -> None:
        pass
    actions = DesktopActions(nothing, nothing, nothing, lambda _: None, nothing, nothing, lambda: None)
    desktop = DesktopWindow(root, backend, actions, store=CanvasStore(tmp_path / "canvas.json"), native_shell=False)
    desktop.surface.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    desktop.surface.setGeometry(-4000, -3000, 1380, 880)
    desktop.surface.show()
    try:
        pump(desktop)
        yield desktop, backend
    finally:
        desktop.close()
        root.processEvents()


def test_native_result_updates_preserve_draft_widget_cursor_and_canvas(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        canvas, _ = m.request(selected_canvas(), "first", "result", "Compare")
        canvas = m.accept(canvas, m.Completed("first", "The original result"))
        canvas = m.draft(canvas, "result", "draft")
        canvas = m.place(canvas, m.Placement("draft", 770, 700, 560, 480))
        desktop.controller.commit(canvas)
        pump(desktop)
        card, proxy = desktop.cards["draft"]
        editor = card.editor
        assert editor is not None
        editor.setPlainText("My edited draft, with a selected phrase.")
        cursor = editor.textCursor()
        cursor.setPosition(3)
        cursor.setPosition(15, QtGui.QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        camera = desktop.view.camera()
        position = proxy.pos()
        refreshed, _ = m.request(desktop.controller.state, "second", "result", "Compare", refresh=True)
        desktop.controller.commit(refreshed)
        desktop.controller.publish(m.Completed("second", "A different result"))
        pump(desktop)
        assert desktop.cards["draft"][0] is card
        assert card.editor is editor and editor.toPlainText() == "My edited draft, with a selected phrase."
        assert editor.textCursor().selectedText() == "edited draft"
        assert proxy.pos() == position
        assert abs(desktop.view.camera().x - camera.x) < 2 and abs(desktop.view.camera().y - camera.y) < 2
        assert "preserved" in card.notice.text()


def test_card_surfaces_are_readable_with_transparent_rounded_corners(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        canvas, _ = m.request(selected_canvas(), "request", "result", "Compare")
        canvas = m.accept(canvas, m.Completed("request", "## A useful result\n\nThe complete answer."))
        desktop.controller.commit(m.draft(canvas, "result", "draft"))
        pump(desktop)
        colors: dict[str, QtGui.QColor] = {}
        for identity in ("budget", "private", "result", "draft"):
            card, _ = desktop.cards[identity]
            capture = card.grab().toImage()
            scale = capture.devicePixelRatio()
            color = capture.pixelColor(round(10 * scale), round((card.header.height() + 30) * scale))
            assert color.alpha() > 250, f"{identity} must have a solid readable surface"
            assert capture.pixelColor(0, 0).alpha() < 20, f"{identity} corners must remain transparent"
            colors[identity] = color
        assert colors["private"].lightness() < 70
        assert colors["budget"].lightness() > 200
        assert colors["result"].blue() > colors["result"].green()
        assert colors["draft"].lightness() > 200


def test_long_document_paths_are_read_without_changing_the_saved_origin_or_shell_target(tmp_path: Path) -> None:
    folder = tmp_path / "canvas-files" / ("a" * 120) / ("b" * 120)
    access = filesystem_path(folder)
    access.mkdir(parents=True)
    try:
        path = folder / "meeting-notes.md"
        assert len(str(path)) > 260
        filesystem_path(path).write_text("# Sourced notes\n\nThe complete saved document.\n", encoding="utf-8")
        item = file_item(path, "long-notes")
        assert isinstance(item.content, m.Source) and "complete saved document" in item.content.text
        assert item.content.origin == m.FileSource(str(path.resolve()))
        opened: list[str] = []
        resources = NativeResources()
        try:
            with patch("assistant.canvas_native.os.startfile", side_effect=opened.append):
                resources.open(item.content.origin)
            assert opened == [str(path.resolve())]
            refreshed = resources.refresh(item)
            assert isinstance(refreshed.content, m.Source) and refreshed.content.origin == item.content.origin
        finally:
            resources.close()
    finally:
        shutil.rmtree(access)


def test_native_context_parking_and_question_reply_are_scoped(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, backend):
        desktop.controller.commit(selected_canvas())
        desktop.cards["private"][0].use.click()
        assert "private" in desktop.controller.state.space.selection
        desktop.action("private", "park")
        assert "private" not in desktop.cards and "private" not in desktop.controller.state.space.selection
        desktop.composer.setText("Compare selected sources")
        desktop.submit()
        issued = backend.submitted[-1]
        assert tuple(value.item_id for value in issued.sources) == ("budget", "brief")
        desktop.controller.publish(m.NeedsInput(issued.id, m.Question("question", "Which period?", "")))
        pump(desktop)
        card = desktop.cards[issued.result_id][0]
        card.answer.setText("Autumn")
        card.send.click()
        assert backend.answers == [(issued.id, "question", "Autumn")]
        assert card.answer_row.isHidden()
        desktop.action(issued.result_id, "stop")
        desktop.controller.publish(m.Completed(issued.id, "Late response"))
        pump(desktop)
        item = desktop.controller.state.item(issued.result_id)
        assert item is not None and isinstance(item.content, m.Result) and item.content.state is m.Settled.STOPPED


def test_native_drag_uses_zoom_and_retains_position_when_parked(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        desktop.controller.commit(m.add(m.Canvas(), m.Item("note", "Note", m.Note("Text"))))
        desktop.set_zoom(.5)
        card, proxy = desktop.cards["note"]
        start = proxy.pos()
        for kind, point, pressed in (
            (QtCore.QEvent.Type.MouseButtonPress, QtCore.QPointF(100, 100), Qt.MouseButton.LeftButton),
            (QtCore.QEvent.Type.MouseMove, QtCore.QPointF(160, 130), Qt.MouseButton.LeftButton),
            (QtCore.QEvent.Type.MouseButtonRelease, QtCore.QPointF(160, 130), Qt.MouseButton.NoButton),
        ):
            event = QtGui.QMouseEvent(kind, QtCore.QPointF(20, 20), point,
                                     Qt.MouseButton.NoButton if kind == QtCore.QEvent.Type.MouseMove else Qt.MouseButton.LeftButton,
                                     pressed, Qt.KeyboardModifier.NoModifier)
            QtCore.QCoreApplication.sendEvent(card.header, event)
        assert proxy.x() == start.x() + 120 and proxy.y() == start.y() + 60
        saved = desktop.controller.state.space.placements[0]
        desktop.action("note", "park")
        desktop.controller.commit(m.park(desktop.controller.state, "note", parked=False))
        assert desktop.cards["note"][1].x() == saved.x


def test_native_launcher_searches_resource_contents_and_preserves_workspace_drafts(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        desktop.controller.commit(selected_canvas())
        desktop.launcher.update_entries(desktop.controller.state, Catalogue((), ()))
        desktop.launcher.search.setText("200000")
        assert tuple(entry.id for entry in desktop.launcher.filtered if isinstance(entry, m.Item)) == ("brief",)
        desktop.composer.setText("Unsubmitted work")
        desktop.controller.new_space("Another project")
        assert desktop.composer.text() == ""
        desktop.composer.setText("Different request")
        desktop.switch_space(0)
        assert desktop.composer.text() == "Unsubmitted work"
        assert len(desktop.cards) == 3
        desktop.switch_space(1)
        assert desktop.composer.text() == "Different request" and not desktop.cards


def test_canvas_and_conversation_share_qt_and_native_glass_until_last_close(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        other = EventLoop(desktop.root, lambda: None)
        assert other.app is desktop.loop.app
        assert other.composition is desktop.loop.composition
        other.close()
        desktop.controller.commit(m.add(m.Canvas(), m.Item("note", "Still alive", m.Note("Text"))))
        pump(desktop)
        assert desktop.cards["note"][0].title.text() == "Still alive"


def test_native_draft_and_table_render_without_html_or_adapters(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        source = m.Item("table", "Budget", m.Source(m.LocalSource.CLIPBOARD, "Travel\t42000", 1,
                                                    m.Table(("Cost", "SEK"), (("Travel", "42000"),))))
        desktop.controller.commit(m.add(m.Canvas(), source))
        pump(desktop)
        table = desktop.cards["table"][0].table
        assert table is not None and table.rowCount() == 1 and table.columnCount() == 2
        cell = table.item(0, 1)
        assert cell is not None and cell.text() == "42000"
        desktop.controller.commit(m.with_space(desktop.controller.state, replace(desktop.controller.state.space, camera=m.Camera(0, 0, .75))))


def test_native_approval_waits_for_visible_uncovered_preview(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, backend):
        canvas, request = m.request(m.Canvas(), "approval-run", "result", "Prepare a file")
        preview = m.Approval("preview-1", "Write the selected file?", "Destination: C:\\example\\notes.txt\nContents: Reviewed text")
        canvas = m.accept(canvas, m.NeedsInput(request.id, preview))
        canvas = m.place(canvas, m.Placement("result", 6000, 6000, 560, 540))
        desktop.controller.commit(canvas)
        pump(desktop)
        card, _ = desktop.cards["result"]
        assert not card.approve.isEnabled() and not backend.previews
        desktop.surface.hide()
        desktop.fit(("result",))
        pump(desktop)
        assert not backend.previews
        desktop.surface.show()
        cover = m.Item("cover", "Obscuring note", m.Note("Another card"))
        desktop.controller.commit(m.add(desktop.controller.state, cover, x=6000, y=6000))
        desktop.cards["cover"][1].setZValue(10)
        pump(desktop)
        assert not card.approve.isEnabled() and not backend.previews
        desktop.controller.commit(m.park(desktop.controller.state, "cover"))
        pump(desktop)
        assert card.approve.isEnabled() and backend.previews[-1] == (request.id, preview)
        card.approve.click()
        assert backend.answers == [(request.id, preview.id, "approve")]
        desktop.surface.hide()
        desktop.controller.publish(m.NeedsInput(request.id, preview, "Reply could not be saved"))
        pump(desktop)
        assert not card.approve.isEnabled() and len(backend.previews) == 1
        desktop.surface.show()
        pump(desktop)
        assert card.approve.isEnabled() and len(backend.previews) == 2


def test_native_submission_attention_retries_the_saved_request(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, backend):
        assert desktop.controller.submit("Prepare a summary")
        request = backend.submitted[-1]
        desktop.controller.publish(m.NeedsAttention(request.id, "Could not confirm saved work"))
        pump(desktop)
        card, _ = desktop.cards[request.result_id]
        assert card.secondary.text() == "Retry submission" and "Could not confirm" in card.question.toPlainText()
        card.secondary.click()
        assert backend.submitted == [request, request]
        assert card.secondary.text() == "Stop"


def test_native_dwm_preview_releases_owned_fixture_windows(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        source = QtWidgets.QWidget()
        source.setWindowTitle("Toolkit canvas preview fixture")
        source.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        source.setGeometry(-5000, -3000, 400, 300)
        source.show()
        preview = desktop_shell.Thumbnail(int(desktop.surface.winId()), int(source.winId()))
        try:
            preview.show(Rect(20, 20, 320, 200))
            pump(desktop)
            preview.show(None)
        finally:
            preview.close()
            preview.close()
            source.hide()
            source.deleteLater()
        assert preview.handle == 0


def test_native_taskbar_guard_handshake_tracks_only_fixture_parent(tmp_path: Path) -> None:
    del tmp_path
    # This fixture launches its own child, like the real assistant. It verifies
    # the wait protocol on owner termination without touching Explorer. The
    # production lease separately requires permission to leave the Windows job.
    script = (
        "import os, subprocess, sys, time\n"
        "guard = subprocess.Popen([sys.executable, '-m', 'assistant.canvas_guard', str(os.getpid())], "
        "stdout=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "ready = guard.stdout.readline(32).decode().strip()\n"
        "print(guard.pid, ready, flush=True)\n"
        "time.sleep(30)\n"
    )
    parent = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    guard: Process | None = None
    ready = threading.Event()
    messages: list[bytes] = []
    def read() -> None:
        assert parent.stdout is not None
        messages.append(parent.stdout.readline(64))
        ready.set()
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        assert ready.wait(10), "Taskbar guard did not report readiness"
        pid, acknowledgment = messages[0].split()
        assert acknowledgment == b"ready"
        open_process = cast(Callable[[int, bool, int], int],
                            fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD))
        handle = int(open_process(0x00101001, False, int(pid)) or 0)  # Wait, query exit status and terminate this owned fixture.
        assert handle, ctypes.WinError()
        guard = Process(handle, int(pid))
        assert guard.poll() is None
        parent.terminate()
        parent.wait(timeout=5)
        assert guard.wait(timeout=5) == 0
    finally:
        if parent.poll() is None:
            parent.terminate()
        parent.wait(timeout=5)
        if guard is not None:
            guard.kill()
            guard.wait(timeout=5)
        if parent.stdout is not None:
            parent.stdout.close()
        reader.join(timeout=2)


def test_taskbar_replacement_keeps_explorer_visible_without_independent_guard(tmp_path: Path) -> None:
    del tmp_path
    lease = TaskbarLease()
    with patch.object(desktop_shell, "taskbars", return_value=(101,)), patch.object(desktop_shell, "visible", return_value=True), \
            patch("assistant.canvas_native.start_guard", side_effect=OSError("Independent recovery unavailable")), \
            patch.object(desktop_shell, "taskbar_visible") as change_visibility:
        with unittest.TestCase().assertRaises(OSError):
            lease.enable()
        assert not lease.hidden and lease.guard is None
        lease.close()
        change_visibility.assert_not_called()


def test_native_canvas_can_focus_move_and_delete_distant_items(tmp_path: Path) -> None:
    with canvas_window(tmp_path) as (desktop, _):
        item = m.Item("distant", "A larger workspace", m.Note("Still reachable"))
        desktop.controller.commit(m.add(m.Canvas(), item, x=35_000, y=40_000))
        desktop.action(item.id, "focus")
        pump(desktop)
        _, proxy = desktop.cards[item.id]
        visible = desktop.view.mapFromScene(proxy.sceneBoundingRect()).boundingRect()
        assert desktop.view.viewport().rect().contains(visible)
        desktop.move_card(item.id, 120, 60)
        desktop.save_positions()
        position = desktop.controller.state.space.placements[0]
        assert (position.x, position.y) == (35_120, 40_060)
        desktop.action(item.id, "focus")
        pump(desktop)
        assert desktop.view.viewport().rect().contains(desktop.view.mapFromScene(proxy.sceneBoundingRect()).boundingRect())
        desktop.delete_item(item)
        dialog = desktop.dialogs[-1]
        dialog.move(-4000, -3000)
        next(control for control in dialog.findChildren(QtWidgets.QToolButton) if control.text() == "Cancel").click()
        pump(desktop)
        assert desktop.controller.state.item(item.id) is not None
        desktop.delete_item(item)
        dialog = desktop.dialogs[-1]
        dialog.move(-4000, -3000)
        next(control for control in dialog.findChildren(QtWidgets.QToolButton) if control.text() == "Delete saved item").click()
        pump(desktop)
        assert desktop.controller.state.item(item.id) is None and item.id not in desktop.cards
