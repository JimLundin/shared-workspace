"""Actual WebEngine layout and bridge interactions, with isolated canvas storage."""

import json
import shutil
import sys
import time
import unittest
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import cast

if sys.platform != "win32":
    raise unittest.SkipTest("Windows WebEngine canvas")

from assistant import canvas_model as m
from assistant import canvas_web_contract as contract
from assistant.canvas_store import CanvasStore
from assistant.canvas_ui import DesktopActions
from assistant.canvas_web import DesktopWindow
from assistant.llm import Json
from assistant.qt import Qt, QtCore, QTest, QtGui, QtWidgets, shiboken6
from assistant.qt_ui import application
from automation.files import filesystem_path
from winapi import screen

from .test_canvas import RecordingBackend, selected_canvas


def until(root: QtWidgets.QApplication, condition: Callable[[], bool], *, timeout: float = 12) -> None:
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("The canvas did not reach the expected state.")
        root.processEvents()
        time.sleep(.01)


def js(desktop: DesktopWindow, expression: str) -> Json:
    results: list[Json] = []
    def received(value: object) -> None:
        if not isinstance(value, str):
            results.append(None)
        else:
            results.append(cast(Json, json.loads(value)))
    desktop.page.runJavaScript("JSON.stringify((" + expression + "))", received)
    until(desktop.root, lambda: bool(results))
    return results[0]


def rendered(desktop: DesktopWindow, condition: str) -> None:
    until(desktop.root, lambda: js(desktop, condition) is True)


def click(desktop: DesktopWindow, selector: str) -> None:
    # Deliver a real Qt pointer click to the Chromium child, using DOM geometry.
    value = js(desktop, "(()=>{const r=document.querySelector(" + json.dumps(selector) + ").getBoundingClientRect();"
               "return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
    assert isinstance(value, dict) and isinstance(value["x"], (int, float)) and isinstance(value["y"], (int, float))
    target = desktop.view.focusProxy()
    assert target is not None
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QtCore.QPoint(round(value["x"]), round(value["y"])))
    desktop.root.processEvents()


def drag_pointer(desktop: DesktopWindow, selector: str, dx: int, dy: int) -> None:
    value = js(desktop, "(()=>{const r=document.querySelector(" + json.dumps(selector) + ").getBoundingClientRect();"
               "return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
    assert isinstance(value, dict) and isinstance(value["x"], (int, float)) and isinstance(value["y"], (int, float))
    start = QtCore.QPoint(round(value["x"]), round(value["y"]))
    end = start + QtCore.QPoint(dx, dy)
    target = desktop.view.focusProxy()
    assert target is not None
    QTest.mousePress(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    desktop.root.processEvents()
    event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(end), QtCore.QPointF(target.mapToGlobal(end)),
                             Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(target, event)
    desktop.root.processEvents()
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    desktop.root.processEvents()


@contextmanager
def window(tmp_path: Path) -> Generator[tuple[DesktopWindow, RecordingBackend]]:
    screen.use_physical_pixels()
    root = application()
    backend = RecordingBackend()
    def nothing() -> None:
        pass
    actions = DesktopActions(nothing, nothing, nothing, lambda _: None, nothing, nothing, lambda: None)
    desktop = DesktopWindow(root, backend, actions, store=CanvasStore(tmp_path / "canvas.json"), native_shell=False)
    desktop._shown = True  # pyright: ignore[reportPrivateUsage]  # Keep test windows off the user's desktop.
    desktop.surface.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    desktop.surface.setGeometry(-4000, -3000, 1544, 836)
    desktop.surface.show()
    try:
        rendered(desktop, "!!window.canvasUI?.ready && !!window.canvasUI.state()")
        yield desktop, backend
    finally:
        desktop.close()
        root.processEvents()
        assert not desktop.worker.is_alive()
        assert desktop.controller.closed and not shiboken6.isValid(desktop.view)
        assert not shiboken6.isValid(desktop.profile)


def test_web_canvas_reference_layout_and_selected_source_submit(tmp_path: Path) -> None:
    with window(tmp_path) as (desktop, backend):
        desktop.load_example()
        rendered(desktop, "window.canvasUI.state().space.id==='canvas-example' && document.querySelectorAll('[data-node]').length===4")
        geometry = js(desktop, "(()=>{const group=document.getElementById('group-heading'), doc=document.querySelector('.card-body.doc');"
                      "return {groupHeight:group.getBoundingClientRect().height,heading:doc.querySelectorAll('h2').length,"
                      "overflow:doc.scrollHeight-doc.clientHeight,metric:document.querySelector('.result-copy h1').textContent,"
                      "composer:!document.getElementById('composer').hidden,wires:document.querySelectorAll('.wire').length};})()")
        assert isinstance(geometry, dict)
        assert isinstance(geometry["groupHeight"], (int, float)) and geometry["groupHeight"] < 25
        assert geometry["heading"] == 2 and geometry["metric"] == "40,000 kr"
        assert isinstance(geometry["overflow"], (int, float)) and geometry["overflow"] <= 1, geometry
        assert geometry["composer"] is True and geometry["wires"] == 2
        click(desktop, '[data-item="canvas-example-message"][data-item-action="select"]')
        until(desktop.root, lambda: len(desktop.controller.state.space.selection) == 3)
        click(desktop, '#composer button[type="submit"]')
        until(desktop.root, lambda: bool(backend.submitted))
        assert {source.item_id for source in backend.submitted[-1].sources} == {
            "canvas-example-budget", "canvas-example-brief", "canvas-example-message",
        }
        rendered(desktop, "document.getElementById('request').value===''")


def test_web_draft_edit_survives_result_refresh_and_reopen(tmp_path: Path) -> None:
    with window(tmp_path) as (desktop, _):
        state, request = m.request(selected_canvas(), "first", "result", "Compare")
        state = m.accept(state, m.Completed(request.id, "Original result"))
        state = m.draft(state, "result", "draft")
        desktop.controller.commit(state)
        rendered(desktop, "!!document.querySelector('[data-node=\"draft\"] textarea')")
        js(desktop, "(()=>{const e=document.querySelector('[data-node=\"draft\"] textarea');window.keptEditor=e;"
           "e.focus();e.value='My own wording, still mine.';e.dispatchEvent(new Event('input',{bubbles:true}));"
           "e.setSelectionRange(3,14);return true;})()")
        until(desktop.root, lambda: (item := desktop.controller.state.item("draft")) is not None
              and m.body(item.content) == "My own wording, still mine.")
        state, request = m.request(desktop.controller.state, "second", "result", "Compare", refresh=True)
        desktop.controller.commit(m.accept(state, m.Completed(request.id, "Recomputed result")))
        rendered(desktop, "window.canvasUI.state().items.find(x=>x.id==='result').text==='Recomputed result'")
        assert js(desktop, "(()=>{const e=document.querySelector('[data-node=\"draft\"] textarea');return "
                  "e===window.keptEditor && e.value==='My own wording, still mine.' && e.selectionStart===3 && e.selectionEnd===14;})()") is True
    with window(tmp_path) as (desktop, _):
        rendered(desktop, "!!document.querySelector('[data-node=\"draft\"] textarea')")
        assert js(desktop, "document.querySelector('[data-node=\"draft\"] textarea').value") == "My own wording, still mine."


def test_web_zoomed_drag_resize_and_workspace_camera_persist(tmp_path: Path) -> None:
    with window(tmp_path) as (desktop, _):
        desktop.controller.commit(selected_canvas())
        rendered(desktop, "!!document.querySelector('[data-node=\"budget\"]')")
        original = next(p for p in desktop.controller.state.space.placements if p.item_id == "budget")
        zoom = js(desktop, "window.canvasUI.camera().zoom")
        assert isinstance(zoom, (int, float))
        drag_pointer(desktop, '[data-node="budget"] .surface-head strong', 80, 50)
        until(desktop.root, lambda: next(p for p in desktop.controller.state.space.placements if p.item_id == "budget") != original)
        moved = next(p for p in desktop.controller.state.space.placements if p.item_id == "budget")
        assert abs(moved.x-original.x-80/zoom) < 2 and abs(moved.y-original.y-50/zoom) < 2
        rendered(desktop, f"Math.abs(window.canvasUI.state().placements.find(p=>p.id==='budget').x-{moved.x})<1")
        drag_pointer(desktop, '[data-node="budget"] .resize-handle', 40, 30)
        until(desktop.root, lambda: next(p for p in desktop.controller.state.space.placements if p.item_id == "budget").width > moved.width)
        placement = next(p for p in desktop.controller.state.space.placements if p.item_id == "budget")
        assert abs(placement.width-moved.width-40/zoom) < 2 and abs(placement.height-moved.height-30/zoom) < 2
        desktop.dispatch(contract.ViewCamera("main", m.Camera(320, 410, .6)))
        old = desktop.controller.state.space
        desktop.dispatch(contract.CreateSpace("Another space"))
        desktop.dispatch(contract.MoveItem("main", m.Placement("budget", 999, 999, 500, 360)))
        assert next(space for space in desktop.controller.state.spaces if space.id == "main") == old
        desktop.dispatch(contract.SwitchSpace("main"))
        rendered(desktop, "window.canvasUI.state().space.id==='main'")
    saved = CanvasStore(tmp_path / "canvas.json").load()
    assert placement in saved.space.placements and saved.space.camera == m.Camera(320, 410, .6)


def test_web_launcher_modal_focus_and_inert_canvas(tmp_path: Path) -> None:
    with window(tmp_path) as (desktop, _):
        state = selected_canvas()
        for index in range(40):
            state = m.put(state, m.Item(f"library-{index}", f"Library note {index}", m.Note("Saved text")))
        desktop.controller.commit(state)
        rendered(desktop, "window.canvasUI.state().items.length===43")
        desktop.show_launcher()
        rendered(desktop, "!document.getElementById('scrim').hidden")
        assert js(desktop, "document.activeElement.id==='resource-search' && document.getElementById('viewport').inert") is True
        for width, height in ((1544, 836), (820, 560)):
            desktop.surface.resize(width, height)
            rendered(desktop, f"innerWidth==={width} && innerHeight==={height}")
            geometry = js(desktop, """(()=>{
                const scrim=document.getElementById('scrim'), bounds=scrim.getBoundingClientRect();
                const dialog=document.getElementById('dialog').getBoundingClientRect();
                const body=document.querySelector('.dialog-body'), list=document.getElementById('search-results');
                const points=[[2,2],[innerWidth-2,2],[2,innerHeight-2],[innerWidth-2,innerHeight-2]];
                return {covers:bounds.x===0&&bounds.y===0&&bounds.right===innerWidth&&bounds.bottom===innerHeight,
                    intercepts:points.every(([x,y])=>document.elementFromPoint(x,y)===scrim),
                    dialogVisible:dialog.top>=0&&dialog.bottom<=innerHeight,
                    bodyScrolls:body.scrollHeight>body.clientHeight+1,listScrolls:list.scrollHeight>list.clientHeight+1};
            })()""")
            assert geometry == {"covers": True, "intercepts": True, "dialogVisible": True, "bodyScrolls": False, "listScrolls": True}
        target = desktop.view.focusProxy()
        assert target is not None
        QTest.mouseClick(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QtCore.QPoint(2, 2))
        rendered(desktop, "document.getElementById('scrim').hidden && !document.getElementById('viewport').inert")
        desktop.show_launcher()
        rendered(desktop, "!document.getElementById('scrim').hidden && document.activeElement.id==='resource-search'")
        js(desktop, "(()=>{const e=document.getElementById('resource-search');e.value='Budget';"
           "e.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
        assert js(desktop, "document.querySelectorAll('[data-launch-index]').length") == 1
        click(desktop, '[data-action="close"]')
        rendered(desktop, "document.getElementById('scrim').hidden && !document.getElementById('viewport').inert")


def test_web_late_edits_and_invalid_native_commands_do_not_change_state(tmp_path: Path) -> None:
    with window(tmp_path) as (desktop, _):
        desktop.controller.commit(selected_canvas())
        before = desktop.controller.state
        desktop.bridge.command('{"kind":"camera","space":"old-space","x":999,"y":999,"zoom":1}')
        desktop.bridge.command('{"kind":"move","space":"old-space","id":"budget","x":999,"y":999,"width":400,"height":300}')
        desktop.bridge.command('{"kind":"link","url":"file:///C:/Windows/System32/cmd.exe"}')
        assert desktop.controller.state is before
        assert desktop.controller.status


def test_web_meeting_cards_edit_the_existing_package_and_wait_for_receipts(tmp_path: Path) -> None:
    from assistant.meeting_canvas import DraftCard, PackageRef, project  # noqa: PLC0415

    from .test_meeting_canvas import completed_meeting_canvas  # noqa: PLC0415

    with ExitStack() as lifetime:
        # Native package adapters use long paths; fixture cleanup must do so too.
        meeting_files = tmp_path / "meeting"
        meeting_files.mkdir(parents=True)
        lifetime.callback(shutil.rmtree, filesystem_path(meeting_files))
        rig, published = lifetime.enter_context(completed_meeting_canvas(meeting_files))
        desktop, backend = lifetime.enter_context(window(tmp_path / "canvas"))
        desktop.attach_meetings(rig.workflow)
        desktop.open_meeting(PackageRef(published.task_id, published.version))
        rendered(desktop, "window.canvasUI.state().meeting?.cards.length===7")
        original = next(card for card in project(rig.workflow.snapshot(), published.task_id, published.version).cards if isinstance(card, DraftCard))
        assert original.value.revision == 0
        js(desktop, "window.canvasUI.focus(" + json.dumps(original.item.id) + ") || true")
        click(desktop, '[data-meeting-edit="' + original.item.id + '"]')
        rendered(desktop, "!!document.getElementById('meeting-edit-form')")
        body = "My reviewed draft.\n\nÅäö 日本語 — keep the full text.\n" * 160
        js(desktop, "(()=>{document.getElementById('meeting-subject').value='Reviewed subject';"
           "document.getElementById('meeting-body').value=" + json.dumps(body) + ";return true;})()")
        click(desktop, '#meeting-edit-form button[type="submit"]')
        until(desktop.root, lambda: bool(rig.workflow.snapshot().changes))
        snapshot = rig.refresh()
        desktop.meetings_changed(snapshot)
        rendered(desktop, "document.getElementById('meeting-save-status').textContent==='Saved revision 1 · unsent'")
        saved = rig.followups.draft_revision(published.task_id, published.version, original.reference.draft_id, 1)
        assert saved.content.subject == "Reviewed subject" and saved.content.body == body
        assert backend.submitted == []
        assert js(desktop, "document.getElementById('meeting-body').value") == body
        click(desktop, '#meeting-edit-form [data-action="close"]')
        desktop.open_meeting(PackageRef(published.task_id, published.version))
        assert len(desktop.controller.state.space.placements) == 7


def test_web_source_reselection_and_late_reads_preserve_revisions_and_workspace(tmp_path: Path) -> None:
    from dataclasses import replace  # noqa: PLC0415

    with window(tmp_path) as (desktop, _):
        state, request = m.request(selected_canvas(), "first", "result", "Compare")
        state = m.accept(state, m.Completed(request.id, "Original result"))
        desktop.controller.commit(state)
        original = state.item("budget")
        assert original is not None and isinstance(original.content, m.Source)
        observed = replace(original, content=replace(original.content, text="260000"))
        desktop.add_item(observed)
        current = desktop.controller.state.item("budget")
        assert current is not None and current.revision == original.revision + 1
        assert m.outdated(desktop.controller.state, "result")
        desktop.source_refreshed(original, replace(observed, content=replace(original.content, text="Late old read")))
        assert desktop.controller.state.item("budget") == current
        desktop.controller.commit(m.delete(desktop.controller.state, "budget"))
        desktop.source_refreshed(current, observed)
        assert desktop.controller.state.item("budget") is None
        desktop.controller.new_space("Other work")
        current_space = desktop.controller.state.current
        imported = m.Item("import", "A delayed import", m.Note("Content"))
        desktop.add_item(imported, space_id="main")
        assert desktop.controller.state.current == current_space
        assert not desktop.controller.state.space.placements
        assert any(p.item_id == imported.id for space in desktop.controller.state.spaces if space.id == "main" for p in space.placements)
