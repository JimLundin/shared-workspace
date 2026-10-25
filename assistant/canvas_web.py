"""The sketch's HTML/CSS canvas in a native Qt WebEngine shell.

Qt owns the window, appbar, hotkeys and renderer lifetime. The local page owns
layout and interaction. CanvasController retains state, evidence and persistence.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import contextlib
import json
import math
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import assert_never

from winapi import desktop_shell, screen, shell, windows
from winapi.shapes import Rect

from . import browser_surface_operations as app_ops
from . import browser_surface_values as browser
from . import canvas_model as m
from . import canvas_web_contract as contract
from . import meeting_canvas, meeting_web
from .agent_session import Session
from .agent_window import log_view
from .browser_surfaces import MAX_PAGES, BrowserSurfaces
from .browser_values import Page as BrowserPage
from .canvas_controller import Backend, CanvasController, default_store
from .canvas_drafts import CanvasDrafts, DraftSnapshot
from .canvas_native import Catalogue, NativeResources, TaskbarLease, catalogue, file_item, resolve
from .canvas_store import CanvasStore
from .canvas_ui import DesktopActions
from .canvas_work import PinStatus, catalog, pin_completed
from .llm import Json
from .meeting_workflow import ActionChange, DraftChange, MeetingSnapshot, MeetingWorkflow, package_values
from .outlook import OutlookDrafts
from .page_source_values import BrowserKind, web_app_url
from .qt import Qt, QtCore, QtGui, QtWidgets, shiboken6
from .work_window import WorkSnapshot

# Resolve the Qt/Python initialization order through assistant.qt first.
# isort: split
from PySide6 import QtWebChannel, QtWebEngineCore, QtWebEngineWidgets

ASSETS = Path(__file__).with_name("canvas_assets").resolve()


@dataclass(frozen=True, slots=True)
class PendingAppOpen:
    item_id: str
    profile_id: str
    requested_url: str
    deadline: float
    cancel: threading.Event
    finished: Callable[[app_ops.AppResult], None]


@dataclass(frozen=True, slots=True)
class PendingAppClose:
    item_id: str
    request_id: str
    space_id: str
    action: contract.ItemAction


def saved_application(state: m.Canvas | None, identity: str, profile_id: str) -> bool:
    item = state.item(identity) if state is not None else None
    return item is not None and isinstance(item.content, m.WebApp) and item.content.profile_id == profile_id


def place_application(state: m.Canvas, item: m.Item) -> m.Canvas:
    existing = next((p for p in state.space.placements if p.item_id == item.id), None)
    if existing is not None:
        return m.place(m.put(state, item), replace(existing, parked=False))
    visible = tuple(value for value in state.space.placements if not value.parked)
    x = min(m.CANVAS_EXTENT - 1_000, max((value.x + value.width for value in visible), default=-64) + 64)
    y = min((value.y for value in visible), default=80)
    return m.place(m.put(state, item), m.Placement(item.id, x, y, 1_000, 700))


class SurfaceEvents(QtCore.QObject):
    def __init__(self, owner: DesktopWindow) -> None:
        super().__init__(owner.surface)
        self.owner = owner

    def eventFilter(self, _watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        if event.type() in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show, QtCore.QEvent.Type.Hide,
                            QtCore.QEvent.Type.WindowStateChange):
            self.owner.application_geometry_changed()
        return False


class Page(QtWebEngineCore.QWebEnginePage):
    """A local application page cannot navigate to an untrusted document."""

    def acceptNavigationRequest(self, url: QtCore.QUrl | str, _kind: QtWebEngineCore.QWebEnginePage.NavigationType, _main: bool) -> bool:  # noqa: N802
        url = QtCore.QUrl(url)
        return url.isLocalFile() and Path(url.toLocalFile()).resolve() == ASSETS / "index.html"


class LocalFiles(QtWebEngineCore.QWebEngineUrlRequestInterceptor):
    """No remote requests, tracking pixels or arbitrary local-file reads."""

    def interceptRequest(self, info: QtWebEngineCore.QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        url = info.requestUrl()
        allowed = url.scheme() == "qrc" and url.path() == "/qtwebchannel/qwebchannel.js"
        if url.isLocalFile():
            allowed = Path(url.toLocalFile()).resolve().is_relative_to(ASSETS)
        if url.scheme() == "data" and info.resourceType() == QtWebEngineCore.QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage:
            allowed = True
        if not allowed:
            info.block(True)


class Bridge(QtCore.QObject):
    changed = QtCore.Signal(str)
    display = QtCore.Signal(str)

    def __init__(self, owner: DesktopWindow) -> None:
        super().__init__()
        self.owner = owner

    @QtCore.Slot(str)
    def ready(self, view: str) -> None:
        if not self.owner.closed:
            if view == "canvas":
                self.owner.canvas_ready()
            self.owner.render(force=True)

    @QtCore.Slot(str)
    def command(self, text: str) -> None:
        if self.owner.closed:
            return
        try:
            self.owner.dispatch(contract.command(text, meetings=self.owner.meeting_snapshot))
        except (ValueError, OSError, KeyError) as error:
            self.owner.controller.problem(str(error))
            self.owner.display(json.dumps({"panel": "problem", "message": str(error)}))


class Surface(QtWidgets.QWidget):
    def __init__(self, hidden: Callable[[], None], files: Callable[[tuple[str, ...]], None]) -> None:
        super().__init__()
        self.hidden, self.files = hidden, files
        self.setWindowTitle("Toolkit · connected work")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(760, 550)
        self.resize(1380, 880)
        self.setAcceptDrops(True)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        event.ignore()
        self.hide()
        self.hidden()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # noqa: N802
        paths = tuple(url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile())
        if paths:
            self.files(paths[:24])
            event.acceptProposedAction()


class DesktopWindow:
    __slots__ = (
        "__weakref__",
        "_app_epoch",
        "_app_frame_sequence",
        "_app_space",
        "_app_specs",
        "_app_suspension",
        "_app_wire_sequence",
        "_canvas_ready",
        "_catalogue_at",
        "_catalogue_pending",
        "_draft_observation",
        "_geometry",
        "_last_json",
        "_meeting_views",
        "_pending_app_closes",
        "_pending_app_opens",
        "_pending_display",
        "_queue",
        "_resources",
        "_results",
        "_shown",
        "_status_key",
        "_work_observation",
        "_worker_error",
        "actions",
        "appbar",
        "applications",
        "bar",
        "bar_channel",
        "bar_page",
        "bar_view",
        "bridge",
        "catalogue",
        "channel",
        "closed",
        "controller",
        "dialogs",
        "drafts",
        "hotkeys",
        "interceptor",
        "keys_window",
        "layer_text",
        "meeting_snapshot",
        "meetings",
        "native",
        "native_shell",
        "page",
        "profile",
        "root",
        "session",
        "surface",
        "surface_events",
        "taskbar",
        "thumbnails",
        "timer",
        "view",
        "voice_active",
        "work_snapshot",
        "worker",
    )

    def __init__(
        self, root: QtWidgets.QApplication, backend: Backend, actions: DesktopActions, *,
        store: CanvasStore | None = None, native_shell: bool = True, work_snapshot: Callable[[], WorkSnapshot] | None = None,
    ) -> None:
        self.root, self.actions, self.native_shell = root, actions, native_shell
        self.work_snapshot = work_snapshot
        self.closed = False
        self.voice_active = False
        self.session: Session | None = None
        self.layer_text = "BASE"
        self.native = NativeResources()
        self.taskbar = TaskbarLease()
        self.appbar: desktop_shell.AppBar | None = None
        self.keys_window: shell.AppWindow | None = None
        self.hotkeys: desktop_shell.Hotkeys | None = None
        self.thumbnails: dict[str, desktop_shell.Thumbnail] = {}
        self.catalogue = Catalogue((), ())
        self._catalogue_pending = False
        self._catalogue_at = 0.0
        self._status_key: tuple[str, str, bool, bool] | None = None
        self._last_json = ""
        self._shown = False
        self._geometry: tuple[Rect, float] | None = None
        self._worker_error = ""
        self._work_observation: WorkSnapshot | None = None
        self._draft_observation = DraftSnapshot()
        self.meetings: MeetingWorkflow | None = None
        self.meeting_snapshot: MeetingSnapshot | None = None
        self._meeting_views: dict[str, dict[str, Json]] = {}
        self._canvas_ready = False
        self._pending_display: list[str] = []
        self._app_epoch = uuid.uuid4().hex
        self._app_frame_sequence = 0
        self._app_wire_sequence = 0
        self._app_space = ""
        self._app_specs: tuple[browser.SurfaceSpec, ...] = ()
        self._app_suspension: browser.Suspension | None = None
        self._pending_app_opens: dict[str, PendingAppOpen] = {}
        self._pending_app_closes: dict[str, PendingAppClose] = {}
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=32)
        self._results: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.worker = threading.Thread(target=self._work, name="canvas resources", daemon=True)
        self.dialogs: list[QtWidgets.QFileDialog] = []
        self._resources = contextlib.ExitStack()
        try:
            self.create(backend, store)
        except Exception:
            self.close()
            raise

    def create(self, backend: Backend, store: CanvasStore | None) -> None:
        self.controller = CanvasController(store or default_store(), backend, changed=self.render)
        self._resources.callback(self.controller.close)
        self.drafts = CanvasDrafts(
            OutlookDrafts(self.controller.store.path.parent / "mail"), lambda: self.controller.state,
            publish=lambda _: None, report_error=log_view,
        )
        self._resources.callback(self.close_drafts)
        self.drafts.start()
        self.bridge = Bridge(self)
        self._resources.callback(shiboken6.delete, self.bridge)
        self.profile = QtWebEngineCore.QWebEngineProfile(self.root)
        self._resources.callback(shiboken6.delete, self.profile)
        self.interceptor = LocalFiles(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(self.cancel_download)
        self.profile.setPersistentCookiesPolicy(QtWebEngineCore.QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.surface = Surface(self.hide_previews, self.add_files)
        self._resources.callback(shiboken6.delete, self.surface)
        self.view, self.page, self.channel = self.make_view(self.surface)
        layout = QtWidgets.QVBoxLayout(self.surface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.applications = BrowserSurfaces(self.surface, self.controller.store.path.parent / "browser-profiles", self.applications_changed)
        self._resources.callback(self.applications.close)
        self.surface_events = SurfaceEvents(self)
        self.surface.installEventFilter(self.surface_events)
        self.view.installEventFilter(self.surface_events)
        self.suspend_applications(browser.Suspension.HIDDEN)
        self.bar = QtWidgets.QWidget()
        self._resources.callback(shiboken6.delete, self.bar)
        self.bar.setWindowTitle("Toolkit desktop bar")
        self.bar.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.bar.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.bar.setFixedHeight(42)
        self.bar_view, self.bar_page, self.bar_channel = self.make_view(self.bar, bar=True)
        bar_layout = QtWidgets.QVBoxLayout(self.bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.addWidget(self.bar_view)
        self.worker.start()
        self.timer = QtCore.QTimer(self.surface)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        if self.native_shell:
            self.bar.show()
            self.appbar = desktop_shell.AppBar(int(self.bar.winId()))
            self.position_bar()
            self.keys_window = shell.AppWindow("toolkit desktop controls", self.hotkey)
            self.hotkeys = desktop_shell.Hotkeys(self.keys_window.handle)
            if self.controller.state.replace_taskbar:
                self.background(self.taskbar.enable, lambda _: None, self.taskbar_problem)
        self.tick()

    def cancel_download(self, download: QtWebEngineCore.QWebEngineDownloadRequest) -> None:
        download.cancel()

    def display(self, text: str) -> None:
        if self._canvas_ready:
            self.bridge.display.emit(text)
        else:
            self._pending_display = [*self._pending_display[-7:], text]

    def canvas_ready(self) -> None:
        self._canvas_ready = True
        self._app_wire_sequence = 0
        self.suspend_applications(browser.Suspension.WORKSPACE, force=True)
        self.render(force=True)
        for text in self._pending_display:
            self.bridge.display.emit(text)
        self._pending_display.clear()

    def canvas_loading(self) -> None:
        self._canvas_ready = False
        self.hide_previews()
        if hasattr(self, "applications"):
            self.suspend_applications(browser.Suspension.HIDDEN, force=True)

    def make_view(self, parent: QtWidgets.QWidget, *, bar: bool = False) -> tuple[
        QtWebEngineWidgets.QWebEngineView, Page, QtWebChannel.QWebChannel,
    ]:
        view = QtWebEngineWidgets.QWebEngineView(parent)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        page = Page(self.profile, view)
        page.setBackgroundColor(QtGui.QColor("#20262e"))
        page.settings().setAttribute(QtWebEngineCore.QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        page.settings().setAttribute(QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        channel = QtWebChannel.QWebChannel(page)
        channel.registerObject("canvas", self.bridge)
        page.setWebChannel(channel)
        view.setPage(page)
        if not bar:
            page.loadStarted.connect(self.canvas_loading)
        url = QtCore.QUrl.fromLocalFile(str(ASSETS / "index.html"))
        query = QtCore.QUrlQuery()
        query.addQueryItem("view", "bar" if bar else "canvas")
        query.addQueryItem("hosted", "true" if self.native_shell else "false")
        url.setQuery(query)
        view.load(url)
        return view, page, channel

    def _work(self) -> None:
        try:
            while (job := self._queue.get()) is not None:
                job()
        finally:
            try:
                self.native.close()
            except Exception as error:
                self._worker_error = str(error)

    def background[T](
        self, operation: Callable[[], T], finished: Callable[[T], None], failed: Callable[[str], None] | None = None,
    ) -> None:
        def run() -> None:
            try:
                value = operation()
            except Exception as error:
                message = str(error)
                self._results.put(lambda: (failed or self.controller.problem)(message))
            else:
                self._results.put(lambda: finished(value))
        try:
            self._queue.put_nowait(run)
        except queue.Full:
            self.controller.problem("Canvas resources are busy. Please try again in a moment.")

    def tick(self) -> None:
        if self.closed:
            return
        while True:
            try:
                self._results.get_nowait()()
            except queue.Empty:
                break
        self.controller.drain()
        self.finish_app_opens()
        self.session = self.actions.session()
        self.voice_active = self.active_context()
        drafts = self.drafts.snapshot()
        if drafts != self._draft_observation:
            self._draft_observation = drafts
            self.render()
        if self.work_snapshot is not None:
            observed = self.work_snapshot()
            if observed != self._work_observation:
                self._work_observation = observed
                self.render()
        busy = self.session is not None and self.session.busy
        recording = self.session is not None and self.session.recording.capturing
        key = time.strftime("%a %d %b  %H:%M"), self.layer_text, busy, recording
        if key != self._status_key:
            self._status_key = key
            self.render()
        if self.native_shell:
            self.position_bar()
            if not self._catalogue_pending and time.monotonic() - self._catalogue_at > 3:
                self._catalogue_pending = True
                self.background(catalogue, self.catalogue_ready, self.catalogue_failed)
        if not self.surface.isVisible() or self.surface.isMinimized():
            self.hide_previews()

    def catalogue_ready(self, value: Catalogue) -> None:
        self._catalogue_pending = False
        self._catalogue_at = time.monotonic()
        value = replace(value, pages=self.catalogue.pages)
        if value != self.catalogue:
            self.catalogue = value
            self.render()

    def catalogue_failed(self, _message: str) -> None:
        self._catalogue_pending = False
        self._catalogue_at = time.monotonic()

    def render(self, *, force: bool = False) -> None:
        if self.closed or not hasattr(self, "bridge"):
            return
        if hasattr(self, "applications"):
            self.sync_applications()
        apps = self.applications.snapshot() if hasattr(self, "applications") else browser.BrowserSnapshot()
        value = contract.snapshot(self.controller.state, self.controller.status, applications=apps)
        value["application_epoch"] = self._app_epoch
        value["layer"] = self.layer_text
        value["clock"] = time.strftime("%a %d %b  %H:%M")
        busy = self.session is not None and self.session.busy
        value["busy"] = busy
        value["agent"] = "Agent working" if busy else "Agent ready"
        value["recording"] = self.session is not None and self.session.recording.capturing
        catalogue_rows: list[Json] = [
            {"id": item.id, "title": item.title, "detail": m.origin_label(item.content.origin), "kind": "window", "icon": "canvas"}
            for item in self.catalogue.windows if isinstance(item.content, m.Source)
        ]
        catalogue_rows.extend({"id": f"app:{index}", "title": app.name, "detail": "Application", "kind": "app", "icon": "canvas"}
                              for index, app in enumerate(self.catalogue.apps))
        catalogue_rows.extend({"id": f"page:{page.id}", "title": page.title, "detail": page.url, "kind": "page", "icon": "globe"}
                              for page in self.catalogue.pages)
        value["catalogue"] = catalogue_rows
        value["draft_receipts"] = contract.draft_receipts(self._draft_observation)
        if self.meeting_snapshot is not None:
            value["meetings"] = meeting_web.catalog(self.meeting_snapshot)
        meeting = self._meeting_views.get(self.controller.state.current)
        if meeting is not None:
            value["meeting"] = meeting
            cards, items = meeting["cards"], value["items"]
            if isinstance(cards, list) and isinstance(items, list):
                by_id = {str(card["id"]): card for card in cards if isinstance(card, dict)}
                for item in items:
                    if isinstance(item, dict) and str(item["id"]) in by_id:
                        card = by_id[str(item["id"])]
                        item["meeting"] = card
                        item["icon"] = "agent" if card["kind"] in ("notes", "task") else "mail" if card["kind"] == "draft" else "doc"
                        item["origin"] = "Meeting · " + str(card["kind"])
        if self._work_observation is not None:
            work = catalog(self._work_observation)
            value["work"] = {
                "status": work.status,
                "entries": [{"task_id": entry.task_id, "title": entry.title, "phase": entry.phase,
                             "preview": entry.preview, "can_pin": entry.pin is PinStatus.AVAILABLE} for entry in work.entries],
            }
        if self.controller.state.space.id == "canvas-example":
            raw_items = value["items"]
            if isinstance(raw_items, list):
                for raw_item in raw_items:
                    if isinstance(raw_item, dict) and str(raw_item.get("id", "")).startswith("canvas-example-"):
                        raw_item["example"] = True
                        raw_item["origin"] = "Example · " + str(raw_item.get("origin", ""))
        text = json.dumps(value, ensure_ascii=False)
        if force or text != self._last_json:
            self._last_json = text
            self.bridge.changed.emit(text)

    def dispatch(self, command: contract.Command) -> None:  # noqa: PLR0915  # Exhaustive bridge command variants.
        state = self.controller.state
        match command:
            case contract.Control():
                self.control(command)
            case contract.ActOnItem(identity, operation):
                self.item_action(identity, operation)
            case contract.CreateWebApp(app):
                self.create_web_app(app)
            case contract.ActOnWebApp(space_id, identity, operation) if space_id == state.current:
                self.web_app_action(identity, operation)
            case contract.NavigateWebApp(space_id, identity, page) if space_id == state.current:
                if self.current_application(identity) is not None:
                    self.applications.dispatch(browser.Navigate(identity, page.url))
            case contract.AppLayout():
                self.application_layout(command)
            case contract.WriteText(identity, text):
                self.controller.commit(m.edit(state, identity, text))
            case contract.MoveItem(space_id, placement) if space_id == state.current:
                if any(value.item_id == placement.item_id and not value.parked for value in state.space.placements):
                    self.controller.commit(m.place(state, placement))
            case contract.ViewCamera(space_id, camera) if space_id == state.current:
                self.controller.commit(m.with_space(state, replace(state.space, camera=camera)), render=False)
            case contract.Compose(space_id, text) if space_id == state.current:
                self.controller.commit(m.with_space(state, replace(state.space, composer=text)), render=False)
            case contract.Submit(space_id, text) if space_id == state.current:
                if self.controller.submit(text):
                    self.controller.commit(m.with_space(self.controller.state, replace(self.controller.state.space, composer="")))
                    self.display(json.dumps({"panel": "submitted", "space": space_id, "text": text}))
            case contract.SwitchSpace(identity):
                if any(space.id == identity for space in state.spaces):
                    self.controller.commit(replace(state, current=identity))
            case contract.CreateSpace(title):
                self.controller.new_space(title)
            case contract.Activate(identity):
                self.activate(identity)
            case contract.Answer(identity, question_id, text):
                item = state.item(identity)
                if (item is not None and isinstance(item.content, m.Result) and isinstance(item.content.state, m.Running)
                        and isinstance(item.content.state.stage, m.Question) and item.content.state.stage.id == question_id):
                    self.controller.answer(identity, text)
            case contract.OpenLink(page):
                self.background(lambda: self.native.open_link(page.url), lambda _: None)
            case contract.PinTask(identity):
                self.pin_task(identity)
            case contract.SaveDraft(identity, revision):
                self.drafts.save_draft(identity, revision, uuid.uuid4().hex)
                self.tick()
            case contract.OpenMeeting(reference):
                self.open_meeting(reference)
            case DraftChange() | ActionChange():
                self.save_meeting(command)
            case contract.Previews(space_id, entries) if space_id == state.current:
                self.show_previews(entries)
            case (contract.MoveItem() | contract.ViewCamera() | contract.Compose() | contract.Submit() | contract.Previews()
                  | contract.ActOnWebApp() | contract.NavigateWebApp()):
                pass  # A late edit from a previous workspace cannot affect the current one.
            case _:
                assert_never(command)

    def sync_applications(self) -> None:
        state = self.controller.state
        if self._app_space != state.current:
            self.suspend_applications(browser.Suspension.WORKSPACE, force=True)
        placed = {placement.item_id for space in state.spaces for placement in space.placements}
        specs = tuple(browser.SurfaceSpec(item.id, item.content) for item in state.items
                      if isinstance(item.content, m.WebApp) and item.id in placed)[:MAX_PAGES]
        if specs != self._app_specs:
            self.applications.sync(specs)
            self._app_specs = specs

    def application_viewport(self) -> browser.CssRect:
        return browser.CssRect(0, 0, max(1, self.view.width()), max(1, self.view.height()))

    def suspend_applications(self, reason: browser.Suspension, *, force: bool = False) -> None:
        if not hasattr(self, "applications") or self.closed:
            return
        if not force and self._app_space == self.controller.state.current and self._app_suspension is reason:
            return
        self._app_epoch = uuid.uuid4().hex
        self._app_space = self.controller.state.current
        self._app_suspension = reason
        self._app_frame_sequence = 0
        self.applications.present(browser.ViewportFrame(
            self._app_space, self._app_epoch, 0, self.application_viewport(), browser.Suspended(reason),
        ))

    def application_geometry_changed(self) -> None:
        if self.closed or not hasattr(self, "applications"):
            return
        hidden = not self.surface.isVisible() or self.surface.isMinimized()
        reason = browser.Suspension.HIDDEN if hidden else browser.Suspension.MODAL if self.dialogs else browser.Suspension.MOVING
        self.suspend_applications(reason, force=True)
        self.render()
        if not hidden:
            self.display(json.dumps({"panel": "app_layout"}))

    def application_layout(self, layout: contract.AppLayout) -> None:
        if (layout.space_id != self.controller.state.current or layout.epoch != self._app_epoch
                or layout.sequence <= self._app_wire_sequence):
            return
        if (not self._canvas_ready or not self.surface.isVisible() or self.surface.isMinimized() or self.dialogs):
            return
        allowed = {placement.item_id for placement in self.controller.state.space.placements if not placement.parked}
        allowed &= {spec.item_id for spec in self._app_specs}
        allowed &= {entry.item_id for entry in self.applications.snapshot().entries if not isinstance(entry.state, browser.Dormant)}
        viewport = self.application_viewport()
        entries = layout.mode.entries if isinstance(layout.mode, browser.Visible) else ()
        if any(entry.item_id not in allowed or any(not viewport.contains(clip) for clip in entry.clips) for entry in entries):
            return
        self._app_wire_sequence = layout.sequence
        previews: tuple[tuple[str, str], ...] = ()
        if (isinstance(layout.mode, browser.Suspended) and layout.mode.reason is browser.Suspension.MODAL
                and self._app_suspension is not browser.Suspension.MODAL):
            previews = self.applications.previews()
        self._app_frame_sequence += 1
        self._app_suspension = layout.mode.reason if isinstance(layout.mode, browser.Suspended) else None
        self.applications.present(browser.ViewportFrame(
            layout.space_id, self._app_epoch, self._app_frame_sequence, viewport, layout.mode,
        ))
        if previews:
            self.display(json.dumps({"panel": "app_previews", "epoch": self._app_epoch,
                                     "previews": [{"id": identity, "image": data} for identity, data in previews]}))

    def applications_changed(self) -> None:
        if self.closed:
            return
        state = self.controller.state
        for observation in self.applications.snapshot().entries:
            item = state.item(observation.item_id)
            if item is None or not isinstance(item.content, m.WebApp):
                continue
            try:
                url = web_app_url(observation.url)
                title = m.bounded(observation.title.strip()[:240] or item.title, "Application title", 240)
            except ValueError:
                continue
            content = m.WebApp(url, item.content.profile_id)
            if item.title != title or item.content != content:
                state = m.put(state, replace(item, title=title, content=content, revision=item.revision + 1))
        if state != self.controller.state:
            self.controller.commit(state)
        else:
            self.render()

    def current_application(self, identity: str) -> m.Item | None:
        item = self.controller.state.item(identity)
        placed = any(value.item_id == identity and not value.parked for value in self.controller.state.space.placements)
        return item if item is not None and isinstance(item.content, m.WebApp) and placed else None

    def create_web_app(self, app: m.WebApp, *, title: str | None = None) -> str:
        if self.closed:
            raise ValueError("The canvas is closed.")
        if len(self._app_specs) >= MAX_PAGES:
            raise ValueError("Close an application before opening another. The canvas can keep 32 apps open.")
        identity = uuid.uuid4().hex
        item = m.Item(identity, title or m.origin_label(m.WebPage(app.url)), app)
        self.controller.status = ""
        self.controller.commit(place_application(self.controller.state, item))
        self.show()
        self.display(json.dumps({"panel": "focus", "id": identity}))
        return identity

    def web_app_action(self, identity: str, operation: contract.WebAppAction) -> None:
        item = self.current_application(identity)
        if item is None:
            return
        match operation:
            case contract.WebAppAction.BACK:
                self.applications.dispatch(browser.Back(identity))
            case contract.WebAppAction.FORWARD:
                self.applications.dispatch(browser.Forward(identity))
            case contract.WebAppAction.RELOAD:
                self.applications.dispatch(browser.Reload(identity))
                self.display(json.dumps({"panel": "app_layout"}))
            case contract.WebAppAction.CAPTURE:
                self.capture_application(identity)
            case contract.WebAppAction.EXTERNAL:
                assert isinstance(item.content, m.WebApp)
                url = item.content.url
                self.background(lambda: self.native.open_link(url), lambda _: None)
            case contract.WebAppAction.ASK_AGENT:
                self.actions.agent()
            case _:
                assert_never(operation)

    def capture_application(self, identity: str) -> None:
        target = next((entry.target for entry in self.applications.snapshot().entries if entry.item_id == identity), None)
        if target is None:
            raise ValueError("Wait for this application to finish loading before using its page as a source.")
        space_id = self.controller.state.current
        self.applications.capture(target, partial(self.application_captured, space_id, target))

    def application_captured(self, space_id: str, target: browser.BrowserTarget, captured: browser.CaptureResult) -> None:
        if (self.closed or captured.target != target or self.controller.state.current != space_id
                or self.current_application(target.item_id) is None or browser.current_target(self.applications.snapshot(), target) is None):
            return
        match captured:
            case browser.CaptureFailed(reason=reason):
                self.controller.problem(reason)
            case browser.CapturedPage():
                observed = captured.observation
                item = m.Item(uuid.uuid4().hex, (captured.title.strip() or "Page capture")[:240], m.Source(
                    m.WebPage(observed.reference.url, provenance=observed.provenance), observed.text, observed.observed_at,
                ))
                state = self.controller.state
                app = next(value for value in state.space.placements if value.item_id == target.item_id)
                state = m.add(state, item, x=min(m.CANVAS_EXTENT - 440, app.x + app.width + 48), y=app.y)
                self.controller.commit(m.select(state, item.id))
                self.display(json.dumps({"panel": "frame", "ids": [target.item_id, item.id]}))
            case _:
                assert_never(captured)

    def browser_request(
        self, operation: app_ops.AppOperation, cancel: threading.Event, finished: Callable[[app_ops.AppResult], None],
    ) -> None:
        """Called on the UI thread by the ordinary agent's registered-app broker."""
        if cancel.is_set():
            finished(app_ops.AppCancelled.REQUEST)
            return
        if self.closed:
            finished(app_ops.AppFailed("The canvas is unavailable."))
            return
        if isinstance(operation, app_ops.OpenApp):
            try:
                identity = self.create_web_app(m.WebApp(operation.url, "default"), title=operation.title)
            except (OSError, ValueError) as error:
                finished(app_ops.AppFailed(str(error)))
                return
            self._pending_app_opens[identity] = PendingAppOpen(identity, "default", operation.url, time.monotonic() + 15, cancel, finished)
            self.finish_app_opens()
        else:
            self.applications.control(operation, cancel, finished)

    def finish_app_opens(self) -> None:
        saved = self.controller.saved_state
        failure = self.controller.save_failure
        for identity, pending in tuple(self._pending_app_opens.items()):
            result: app_ops.AppResult
            if saved_application(saved, identity, pending.profile_id):
                result = app_ops.AppSucceeded(json.dumps({
                    "operation": "open", "item_id": identity, "requested_url": pending.requested_url, "saved": True,
                }))
            elif failure is not None and saved_application(failure.state, identity, pending.profile_id):
                result = app_ops.AppFailed(f"Application card {identity} was created, but its save failed: {failure.reason}")
            elif self.closed or pending.cancel.is_set() or time.monotonic() >= pending.deadline:
                result = app_ops.AppFailed(
                    f"Application card {identity} was created, but saving could not be confirmed before the request ended. "
                    "Inspect the canvas before opening it again.",
                )
            elif not saved_application(self.controller.state, identity, pending.profile_id):
                result = app_ops.AppFailed(f"Application card {identity} was removed before its save could be confirmed.")
            else:
                continue
            self._pending_app_opens.pop(identity)
            pending.finished(result)

    def close_application(self, identity: str, action: contract.ItemAction) -> None:
        if identity in self._pending_app_closes:
            return
        state = self.controller.state
        if not any(entry.item_id == identity for entry in self.applications.snapshot().entries):
            self.controller.commit(m.delete(state, identity) if action is contract.ItemAction.DELETE else m.remove(state, identity))
            return
        if action is contract.ItemAction.REMOVE and any(
            space.id != state.current and any(value.item_id == identity for value in space.placements) for space in state.spaces
        ):
            self.controller.commit(m.remove(state, identity))
            return
        request_id = self.applications.request_close(identity, self.application_closed)
        self._pending_app_closes[identity] = PendingAppClose(identity, request_id, state.current, action)

    def application_closed(self, result: browser.CloseResult) -> None:
        pending = self._pending_app_closes.get(result.item_id)
        if self.closed or pending is None or result.request_id != pending.request_id:
            return
        self._pending_app_closes.pop(result.item_id)
        if not browser.accepts_close(result, item_id=pending.item_id, request_id=pending.request_id):
            self.controller.problem("The application stayed open. Your card has been kept.")
            return
        state = self.controller.state
        item = state.item(pending.item_id)
        if item is None or not isinstance(item.content, m.WebApp):
            return
        if pending.action is contract.ItemAction.DELETE:
            self.controller.commit(m.delete(state, item.id))
        elif any(space.id == pending.space_id for space in state.spaces):
            self.controller.commit(replace(m.remove(replace(state, current=pending.space_id), item.id), current=state.current))

    def show_captured_application(self, page: m.WebPage) -> None:
        assert page.provenance is not None
        reference = page.provenance.reference
        target = next((entry.target for entry in self.applications.snapshot().entries
                       if entry.target is not None and entry.target.instance_id == reference.binding_id), None)
        if target is None:
            self.controller.problem("This capture's application is no longer open. The saved page text is still available here.")
            return
        state = self.controller.state
        space = next((space for space in (state.space, *state.spaces) if any(p.item_id == target.item_id for p in space.placements)), None)
        if space is not None:
            self.controller.commit(m.park(replace(state, current=space.id), target.item_id, parked=False))
            self.show()
            self.display(json.dumps({"panel": "focus", "id": target.item_id}))

    def pin_task(self, identity: str) -> None:
        if self._work_observation is None:
            raise ValueError("Saved work is not available yet.")
        self.controller.commit(pin_completed(self.controller.state, self._work_observation, identity))
        self.show()
        self.display(json.dumps({"panel": "fit"}))

    def save_meeting(self, change: DraftChange | ActionChange) -> None:
        if self.meetings is None:
            raise ValueError("Meeting work is not available yet.")
        if isinstance(change, DraftChange):
            self.meetings.save_draft(change)
        else:
            self.meetings.save_action(change)
        self.display(json.dumps({"panel": "meeting_queued", "attempt_id": change.attempt_id}))

    def control(self, command: contract.Control) -> None:
        match command:
            case contract.Control.CANVAS:
                self.show()
            case contract.Control.LAUNCHER | contract.Control.SPACES | contract.Control.SETTINGS | contract.Control.WORK:
                self.show_panel(command.value)
            case contract.Control.NATIVE_WORK:
                self.actions.work()
            case contract.Control.AGENT:
                self.actions.agent()
            case contract.Control.STOP:
                self.actions.stop()
            case contract.Control.RECORD:
                self.actions.record("stop" if self.session and self.session.recording.capturing else "start")
            case contract.Control.KEYBOARD:
                self.actions.keyboard()
            case contract.Control.OVERLAY:
                self.actions.overlay()
            case contract.Control.FILE:
                self.choose_file()
            case contract.Control.PASTE:
                text = self.root.clipboard().text()
                m.bounded(text, "Clipboard text")
                self.add_item(m.Item(uuid.uuid4().hex, text.strip().splitlines()[0][:100], m.Source(m.LocalSource.CLIPBOARD, text, time.time())))
            case contract.Control.NOTE:
                identity = uuid.uuid4().hex
                self.add_item(m.Item(identity, "Untitled note", m.Note("")))
                self.display(json.dumps({"panel": "focus", "id": identity}))
            case contract.Control.CONNECT_EDGE:
                self.controller.problem("Waiting for Edge to connect…")
                self.background(self.native.connect_pages, self.pages_ready)
            case contract.Control.ARRANGE:
                items = tuple(item for identity in self.controller.state.space.selection
                              if (item := self.controller.state.item(identity)) is not None)
                hwnd = int(self.surface.winId())
                self.background(lambda: self.native.arrange(items, hwnd), lambda _: None)
            case contract.Control.RESTORE_APPS:
                self.background(self.native.restore, lambda _: None)
            case contract.Control.TASKBAR:
                self.toggle_taskbar()
            case contract.Control.EXAMPLE:
                self.load_example()
            case contract.Control.HIDE:
                self.surface.hide()
                self.hide_previews()
            case _:
                assert_never(command)

    def item_action(self, identity: str, action: contract.ItemAction) -> None:
        state = self.controller.state
        item = state.item(identity)
        if item is None:
            return
        if isinstance(item.content, m.WebApp) and action in (contract.ItemAction.REMOVE, contract.ItemAction.DELETE):
            self.close_application(identity, action)
            return
        match action:
            case contract.ItemAction.SELECT:
                if not isinstance(item.content, m.WebApp):
                    self.controller.commit(m.select(state, identity, multiple=True))
            case contract.ItemAction.PARK | contract.ItemAction.RESTORE:
                self.controller.commit(m.park(state, identity, parked=action is contract.ItemAction.PARK))
            case contract.ItemAction.OPEN:
                if isinstance(item.content, m.Source):
                    origin = item.content.origin
                    if (isinstance(origin, m.WebPage) and origin.provenance is not None
                            and origin.provenance.reference.browser is BrowserKind.TOOLKIT):
                        self.show_captured_application(origin)
                    else:
                        self.background(lambda: self.native.open(origin, identity), lambda _: None)
            case contract.ItemAction.REFRESH:
                if isinstance(item.content, m.Source):
                    origin = item.content.origin
                    if (isinstance(origin, m.WebPage) and origin.provenance is not None
                            and origin.provenance.reference.browser is BrowserKind.TOOLKIT):
                        self.show_captured_application(origin)
                        self.controller.problem("Choose 'Use page as source' on the app to save a new capture. This observation stays unchanged.")
                    else:
                        self.background(lambda: self.native.refresh(item), partial(self.source_refreshed, item))
                elif isinstance(item.content, m.Result):
                    self.controller.submit(item.content.prompt, result_id=identity)
            case contract.ItemAction.DRAFT:
                draft_id = uuid.uuid4().hex
                state = m.draft(state, identity, draft_id)
                position = next(value for value in state.space.placements if value.item_id == draft_id)
                self.controller.commit(m.place(state, replace(position, width=470, height=520)))
            case contract.ItemAction.COPY:
                self.root.clipboard().setText(m.body(item.content))
                self.controller.problem("Copied to the clipboard.")
            case contract.ItemAction.EXPORT:
                self.export(item)
            case contract.ItemAction.REMOVE:
                self.controller.commit(m.remove(state, identity))
            case contract.ItemAction.DELETE:
                self.controller.commit(m.delete(state, identity))
            case contract.ItemAction.STOP:
                self.controller.cancel(identity)
            case contract.ItemAction.RETRY:
                self.controller.retry(identity)
            case _:
                assert_never(action)

    def source_refreshed(self, original: m.Item, observed: m.Item) -> None:
        # A read started before deletion or reconnection cannot recreate its old source.
        if self.controller.state.item(original.id) == original:
            self.controller.commit(m.observe(self.controller.state, observed))

    def attach_meetings(self, workflow: MeetingWorkflow) -> None:
        self.meetings = workflow
        self.meetings_changed(workflow.snapshot())

    def meetings_changed(self, snapshot: MeetingSnapshot) -> None:
        self.meeting_snapshot = snapshot
        state = self.controller.state
        spaces = {space.id for space in state.spaces}
        observed: set[str] = set()
        for progress in snapshot.preparations:
            for package in package_values(progress.packages):
                reference = meeting_canvas.PackageRef(progress.request.task_id, package.publication.version)
                identity = contract.meeting_space(reference)
                if identity not in spaces:
                    continue
                observed.add(identity)
                projected = meeting_canvas.project(snapshot, reference.task_id, reference.version)
                self._meeting_views[identity] = meeting_web.payload(projected)
                for item in projected.items:
                    if state.item(item.id) is not None:
                        state = m.observe(state, item)
        for identity, previous in self._meeting_views.items():
            if identity not in observed:
                self._meeting_views[identity] = {**previous, "readiness": {
                    "kind": "unconfirmed", "reason": "This meeting is not in the current observation. Its last saved view is retained.",
                }}
        self.controller.commit(state, render=False)
        self.render()

    def open_meeting(self, reference: meeting_canvas.PackageRef) -> None:
        if self.meeting_snapshot is None:
            raise ValueError("Meeting work is not available yet.")
        projected = meeting_canvas.project(self.meeting_snapshot, reference.task_id, reference.version)
        identity = contract.meeting_space(reference)
        state = self.controller.state
        exists = any(space.id == identity for space in state.spaces)
        core: list[str] = []
        if not exists:
            if len(state.spaces) >= 16:
                raise ValueError("The desktop supports up to 16 workspaces.")
            placements: list[m.Placement] = []
            source_index = action_index = draft_index = 0
            for card in projected.cards:
                item = card.item
                match card:
                    case meeting_canvas.SourceCard():
                        source_positions = ((82, 141, 408, 285), (530, 141, 420, 285), (530, 449, 420, 245))
                        x, y, width, height = source_positions[source_index % 3]
                        y += (source_index // 3) * 600
                        source_index += 1
                        core.append(item.id)
                    case meeting_canvas.NotesCard():
                        x, y, width, height = 1060, 133, 453, 620
                        core.append(item.id)
                    case meeting_canvas.TaskCard():
                        x, y, width, height = 82, 452, 408, 242
                        core.append(item.id)
                    case meeting_canvas.DraftCard():
                        x, y, width, height = 1583 + draft_index * 520, 141, 470, 520
                        draft_index += 1
                    case meeting_canvas.ActionCard():
                        x, y, width, height = 1583 + action_index * 520, 693, 470, 250
                        action_index += 1
                    case _:
                        assert_never(card)
                placements.append(m.Placement(item.id, x, y, width, height))
                state = m.put(state, item)
            selected = tuple(card.item.id for card in projected.cards if isinstance(card, meeting_canvas.SourceCard))
            space = m.Space(identity, projected.title[:80], tuple(placements), selected)
            state = replace(state, spaces=(*state.spaces, space))
        self._meeting_views[identity] = meeting_web.payload(projected)
        self.controller.commit(replace(state, current=identity))
        self.show()
        if not exists:
            self.display(json.dumps({"panel": "frame", "ids": core}))

    def show(self) -> None:
        if not self._shown:
            self.surface.showMaximized()
            self._shown = True
        else:
            self.surface.show()
        self.surface.raise_()
        self.surface.activateWindow()

    def show_panel(self, name: str) -> None:
        self.show()
        self.display(json.dumps({"panel": name}))

    def show_launcher(self) -> None:
        self.show_panel("launcher")

    def layer(self, text: str | None) -> None:
        self.layer_text = text or "BASE"

    def active_context(self) -> bool:
        if not self.surface.isVisible() or self.surface.isMinimized() or not self.controller.state.space.selection:
            return False
        if self.surface.isActiveWindow():
            return True
        active = windows.foreground()
        for identity in self.controller.state.space.selection:
            item = self.controller.state.item(identity)
            if item and isinstance(item.content, m.Source) and isinstance(item.content.origin, m.LiveWindow):
                origin = item.content.origin
                if origin.handle == active and windows.process_id(active) == origin.pid:
                    return True
        return False

    def position_bar(self) -> None:
        if self.appbar is None:
            return
        monitors = screen.monitors()
        monitor = next((value for value in monitors if value.primary), monitors[0])
        ratio = self.bar.devicePixelRatioF()
        if self._geometry == (monitor.area, ratio):
            return
        self._geometry = monitor.area, ratio
        area = self.appbar.place(monitor.area, round(42 * ratio))
        desired = QtCore.QRect(round(area.left / ratio), round(area.top / ratio), round(area.width / ratio), round(area.height / ratio))
        if self.bar.geometry() != desired:
            self.bar.setGeometry(desired)

    def hotkey(self, _hwnd: int, message: int, wparam: int, _lparam: int) -> int | None:
        if message == desktop_shell.WM_HOTKEY:
            if wparam == desktop_shell.CANVAS_KEY:
                self.show()
            elif wparam == desktop_shell.LAUNCHER_KEY:
                self.show_launcher()
            return 0
        return None

    def add_item(self, item: m.Item, *, space_id: str | None = None) -> None:
        state = self.controller.state
        destination = space_id or state.current
        if not any(space.id == destination for space in state.spaces):
            return
        self.controller.status = ""
        existing = state.item(item.id)
        if existing is not None and isinstance(existing.content, m.Source) and isinstance(item.content, m.Source):
            state = m.observe(state, item)
            item = state.item(item.id) or item
        placed = (place_application if isinstance(item.content, m.WebApp) else m.add)(replace(state, current=destination), item)
        self.controller.commit(replace(placed, current=state.current))
        if destination == state.current:
            self.show()
            self.display(json.dumps({"panel": "fit"}))

    def add_files(self, paths: tuple[str, ...]) -> None:
        for name in paths[:24]:
            identity = uuid.uuid4().hex
            self.background(partial(file_item, Path(name), identity), partial(self.add_item, space_id=self.controller.state.current))

    def choose_file(self) -> None:
        dialog = QtWidgets.QFileDialog(self.surface, "Add a document")
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFiles)
        dialog.filesSelected.connect(self.files_selected)
        self.retain_dialog(dialog)

    def files_selected(self, names: list[str]) -> None:
        self.add_files(tuple(names))

    def retain_dialog(self, dialog: QtWidgets.QFileDialog) -> None:
        self.dialogs.append(dialog)
        def finished(_result: int) -> None:
            self.finish_dialog(dialog)
        dialog.finished.connect(finished)
        dialog.open()
        self.hide_previews()

    def finish_dialog(self, dialog: QtWidgets.QFileDialog) -> None:
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)
        dialog.deleteLater()
        self.application_geometry_changed()

    def export(self, item: m.Item) -> None:
        dialog = QtWidgets.QFileDialog(self.surface, "Export canvas text")
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
        dialog.selectFile("canvas-draft.md")
        def selected(name: str) -> None:
            text = m.body(item.content)
            self.background(lambda: Path(name).write_text(text, encoding="utf-8"), lambda _: self.controller.problem("Exported."))
        dialog.fileSelected.connect(selected)
        self.retain_dialog(dialog)

    def activate(self, identity: str) -> None:
        if identity.startswith("app:"):
            index = int(identity.removeprefix("app:"))
            if not 0 <= index < len(self.catalogue.apps):
                raise ValueError("This application is no longer in the launcher.")
            application = self.catalogue.apps[index]
            self.background(lambda: self.native.launch(application), lambda _: None)
            return
        if identity.startswith("page:"):
            page_id = identity.removeprefix("page:")
            if not any(page.id == page_id for page in self.catalogue.pages):
                raise ValueError("This page is no longer in the connected browser.")
            self.background(lambda: self.native.page(page_id, f"page-{page_id}"), partial(self.add_item, space_id=self.controller.state.current))
            return
        item = self.controller.state.item(identity) or next((item for item in self.catalogue.windows if item.id == identity), None)
        if item is not None:
            self.add_item(item)

    def pages_ready(self, pages: tuple[BrowserPage, ...]) -> None:
        self.catalogue = replace(self.catalogue, pages=pages)
        self.controller.status = ""
        self.render()
        self.show_launcher()

    def toggle_taskbar(self) -> None:
        enabled = self.controller.state.replace_taskbar
        def ready(_: None) -> None:
            self.controller.commit(replace(self.controller.state, replace_taskbar=not enabled))
        self.background(self.taskbar.disable if enabled else self.taskbar.enable, ready, self.taskbar_problem)

    def taskbar_problem(self, message: str) -> None:
        self.controller.commit(replace(self.controller.state, replace_taskbar=False))
        self.controller.problem(message)

    def hide_previews(self) -> None:
        for thumbnail in self.thumbnails.values():
            with contextlib.suppress(OSError):
                thumbnail.show(None)
        if hasattr(self, "surface") and (not self.surface.isVisible() or self.surface.isMinimized() or not self._canvas_ready or self.dialogs):
            self.suspend_applications(browser.Suspension.MODAL if self.dialogs else browser.Suspension.HIDDEN)

    def show_previews(self, entries: tuple[contract.Preview, ...]) -> None:
        if not self.native_shell or not self.surface.isVisible() or self.surface.isMinimized() or self.dialogs:
            self.hide_previews()
            return
        used: set[str] = set()
        width, height = self.view.width(), self.view.height()
        ratio = self.surface.devicePixelRatioF()
        for entry in entries:
            if (not all(math.isfinite(number) for number in (entry.x, entry.y, entry.width, entry.height))
                    or entry.x < 0 or entry.y < 0 or entry.width <= 0 or entry.height <= 0
                    or entry.x + entry.width > width + 1 or entry.y + entry.height > height + 1):
                continue
            item = self.controller.state.item(entry.identity)
            if item is None or not isinstance(item.content, m.Source) or not isinstance(item.content.origin, m.LiveWindow):
                continue
            try:
                hwnd = resolve(item.content.origin)
                if entry.identity not in self.thumbnails:
                    self.thumbnails[entry.identity] = desktop_shell.Thumbnail(int(self.surface.winId()), hwnd)
                bounds = Rect(round(entry.x*ratio), round(entry.y*ratio), round((entry.x+entry.width)*ratio), round((entry.y+entry.height)*ratio))
                self.thumbnails[entry.identity].show(bounds)
                used.add(entry.identity)
            except OSError:
                pass
        for identity in tuple(self.thumbnails):
            if identity not in used:
                self.thumbnails.pop(identity).close()

    def load_example(self) -> None:
        if any(space.id == "canvas-example" for space in self.controller.state.spaces):
            self.controller.commit(replace(self.controller.state, current="canvas-example"))
            self.show()
            return
        def read() -> tuple[m.Item, m.Item]:
            return (file_item(ASSETS / "examples" / "launch-budget.csv", "canvas-example-budget"),
                    file_item(ASSETS / "examples" / "launch-brief.md", "canvas-example-brief"))
        def loaded(sources: tuple[m.Item, m.Item]) -> None:
            if len(self.controller.state.spaces) >= 16:
                self.controller.problem("Open an existing workspace before adding another.")
                return
            budget, brief = sources
            budget = replace(budget, title="Launch budget.csv")
            brief = replace(brief, title="Autumn launch brief")
            note = m.Item("canvas-example-message", "Venue decision", m.Source(
                m.LocalSource.CLIPBOARD, "Maya · Today, 09:28\n\nThe venue is already booked.  \n"
                "**That amount is fixed.** We can still revisit travel before Friday.", 0,
            ))
            text = ("## The plan is over budget.\n\n# 40,000 *kr*\n\nTo resolve · 20% above the agreed cap\n\n---\n\n"
                    "240,000 kr in the workbook.\n\n200,000 kr is the agreed cap.\n\n"
                    "> **Suggested next step**\n>\n> Check the venue and travel assumptions first. "
                    "Confirm which costs can move before committing the plan.")
            result = m.Item("canvas-example-result", "Launch recommendation", m.Result(
                "Compare the plan with the budget", text, (m.evidence(budget), m.evidence(brief)), m.Settled.COMPLETE,
            ))
            placements = (
                m.Placement(budget.id, 82, 141, 408, 415), m.Placement(brief.id, 530, 141, 420, 285),
                m.Placement(note.id, 530, 449, 420, 225), m.Placement(result.id, 1060, 133, 453, 620),
            )
            space = m.Space("canvas-example", "Autumn launch", placements, (budget.id, brief.id), composer="Compare the plan with the budget")
            state = replace(self.controller.state, spaces=(*self.controller.state.spaces, space), current=space.id)
            for item in (budget, brief, note, result):
                state = m.put(state, item)
            self.controller.commit(state)
            self.show()
            self.display(json.dumps({"panel": "fit"}))
        self.background(read, loaded)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.voice_active = False
        if hasattr(self, "controller"):
            self.finish_app_opens()
        self._pending_app_closes.clear()
        if hasattr(self, "timer"):
            self.timer.stop()
        errors: list[Exception] = []
        def release(operation: Callable[[], object]) -> None:
            try:
                operation()
            except Exception as error:
                errors.append(error)
        for thumbnail in self.thumbnails.values():
            release(thumbnail.close)
        self.thumbnails.clear()
        if self.hotkeys is not None:
            release(self.hotkeys.close)
        if self.keys_window is not None:
            release(self.keys_window.destroy)
        if self.appbar is not None:
            release(self.appbar.close)
        release(self.taskbar.close)
        self.native.cancel.set()
        with contextlib.suppress(queue.Empty):
            while True:
                self._queue.get_nowait()
        self._queue.put_nowait(None)
        if self.worker.ident is not None:
            self.worker.join(timeout=3)
        else:
            release(self.native.close)
        for dialog in tuple(self.dialogs):
            release(dialog.close)
        release(self._resources.close)
        if self.worker.is_alive():
            errors.append(OSError("The canvas resource worker did not finish before shutdown."))
        if self._worker_error:
            errors.append(OSError(self._worker_error))
        if errors:
            raise ExceptionGroup("Canvas shutdown failed", errors)

    def close_drafts(self) -> None:
        self.drafts.close()
        if not self.drafts.wait_closed(timeout=4):
            raise OSError("Canvas draft saving did not finish closing.")

