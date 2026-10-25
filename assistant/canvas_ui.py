"""The native desktop shell and shared canvas, on the assistant's existing UI thread."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import contextlib
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from winapi import desktop_shell, screen, shell, windows
from winapi.shapes import Rect

from . import canvas_model as m
from .agent_session import Session
from .agent_window import log_view
from .browser_values import Page
from .canvas_controller import Backend, CanvasController, default_store
from .canvas_icons import icon
from .canvas_launcher import Entry, Launcher
from .canvas_native import Application, Catalogue, NativeResources, TaskbarLease, catalogue, file_item, mail_draft
from .canvas_store import CanvasStore
from .canvas_widgets import GOLD, STYLE, CanvasView, Card, MiniMap, button, label
from .qt import Qt, QtCore, QtGui, QtWidgets
from .qt_ui import EventLoop, cancel, later
from .recording_state import RecordingAction


@dataclass(frozen=True, slots=True)
class DesktopActions:
    agent: Callable[[], None]
    work: Callable[[], None]
    stop: Callable[[], None]
    record: Callable[[RecordingAction], None]
    keyboard: Callable[[], None]
    overlay: Callable[[], None]
    session: Callable[[], Session | None]


class Surface(QtWidgets.QWidget):
    def __init__(self, hide: Callable[[], None], files: Callable[[tuple[str, ...]], None]) -> None:
        super().__init__()
        self.hidden, self.files = hide, files
        self.setAcceptDrops(True)
        self.setObjectName("desktop")
        self.setStyleSheet(STYLE)
        self.setWindowTitle("Toolkit workspace")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(760, 550)
        self.resize(1380, 880)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        event.ignore()
        self.hide()
        self.hidden()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # noqa: N802
        files = tuple(url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile())
        if files:
            self.files(files[:24])
            event.acceptProposedAction()


class DesktopWindow:
    __slots__ = (
        "__weakref__",
        "_catalogue_at",
        "_catalogue_pending",
        "_geometry",
        "_native_shell",
        "_rendering",
        "_results",
        "_scope_key",
        "_shortcuts",
        "_shown",
        "_worker_error",
        "actions",
        "appbar",
        "bar",
        "bar_apps",
        "bar_clock",
        "bar_layer",
        "bar_record",
        "bar_status",
        "bar_stop",
        "bar_workspace",
        "breadcrumb",
        "cards",
        "catalogue",
        "closed",
        "compose_open",
        "composer",
        "composer_surface",
        "context",
        "controller",
        "dialogs",
        "empty",
        "footer",
        "hotkeys",
        "keys_window",
        "launcher",
        "loop",
        "map",
        "native",
        "parked",
        "previous_camera",
        "queue",
        "root",
        "scene",
        "scope_chips",
        "session",
        "source_proxy",
        "source_tray",
        "space_id",
        "spaces",
        "status",
        "surface",
        "taskbar",
        "thumbnails",
        "timer",
        "tools",
        "view",
        "view_buttons",
        "view_mode",
        "view_timer",
        "voice_active",
        "wires",
        "work_queue",
        "worker",
        "zoom",
    )

    def __init__(
        self, root: QtWidgets.QApplication, backend: Backend, actions: DesktopActions, *, store: CanvasStore | None = None, native_shell: bool = True,
    ) -> None:
        try:
            self._initialize(root, backend, actions, store=store, native_shell=native_shell)
        except Exception:
            self._abort()
            raise

    def _initialize(  # noqa: PLR0915  # One native surface layout, retained across backend updates.
        self, root: QtWidgets.QApplication, backend: Backend, actions: DesktopActions, *, store: CanvasStore | None = None, native_shell: bool = True,
    ) -> None:
        self.root, self.actions = root, actions
        self.closed = self._rendering = self._catalogue_pending = self._shown = False
        self._native_shell = native_shell
        self._geometry: Rect | None = None
        self._worker_error = ""
        self._catalogue_at = 0.0
        self.space_id = ""
        self.voice_active = False
        self.session: Session | None = None
        self.previous_camera: m.Camera | None = None
        self.cards: dict[str, tuple[Card, QtWidgets.QGraphicsProxyWidget]] = {}
        self.wires: list[QtWidgets.QGraphicsPathItem] = []
        self.dialogs: list[QtWidgets.QDialog | QtWidgets.QMenu] = []
        self.thumbnails: dict[str, desktop_shell.Thumbnail] = {}
        self.queue: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.work_queue: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=32)
        self._results: tuple[str, ...] = ()
        self._scope_key: tuple[tuple[str, str], ...] = ()
        self.view_mode = "Canvas"
        self.catalogue = Catalogue((), ())
        self.loop = EventLoop(root, self.acknowledge_previews)
        self.native = NativeResources()
        self.taskbar = TaskbarLease()
        self.appbar: desktop_shell.AppBar | None = None
        self.hotkeys: desktop_shell.Hotkeys | None = None
        self.keys_window: shell.AppWindow | None = None
        self.timer: QtCore.QTimer | None = None
        self.worker = threading.Thread(target=self._work, name="canvas resources", daemon=True)
        self.worker.start()
        self.controller = CanvasController(store or default_store(), backend, changed=self.render)
        self.surface = Surface(self.hide_previews, self.add_files)
        outer = QtWidgets.QVBoxLayout(self.surface)
        outer.setContentsMargins(0, 0, 0, 0)
        self.view = CanvasView(self.view_changed, self.escape)
        scene = self.view.scene()
        assert scene is not None
        self.scene = scene
        outer.addWidget(self.view)
        self.view_timer = QtCore.QTimer(self.surface)
        self.view_timer.setSingleShot(True)
        self.view_timer.setInterval(160)
        self.view_timer.timeout.connect(self.save_view)
        self.tools = QtWidgets.QWidget(self.view.viewport())
        self.tools.setObjectName("canvasTools")
        tools = QtWidgets.QHBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(10)
        self.spaces = QtWidgets.QComboBox()
        self.spaces.setAccessibleName("Current workspace")
        self.spaces.setMinimumWidth(170)
        self.spaces.setMaximumWidth(230)
        self.spaces.setStyleSheet("QComboBox { background: transparent; border: none; font-size: 11px; padding: 3px 8px; }")
        self.spaces.currentIndexChanged.connect(self.switch_space)
        tools.addWidget(self.spaces)
        tools.addWidget(button("+", self.new_space, tip="Create a workspace"))
        self.breadcrumb = label("", "muted")
        tools.addWidget(self.breadcrumb)
        tools.addStretch()
        tools.addWidget(button("Add source", self.add_menu, tip="Windows, documents, pages and notes"))
        switch = QtWidgets.QWidget()
        switch.setObjectName("viewSwitch")
        modes = QtWidgets.QHBoxLayout(switch)
        modes.setContentsMargins(3, 3, 3, 3)
        modes.setSpacing(2)
        self.view_buttons: dict[str, QtWidgets.QToolButton] = {}
        for name, glyph, action in (("Canvas", "canvas", self.canvas_mode), ("Focus", "expand", self.focus_selection),
                                     ("Overview", "grid", self.overview)):
            control = button(name, action)
            control.setIcon(icon(glyph, size=12))
            control.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            control.setCheckable(True)
            control.setChecked(name == "Canvas")
            self.view_buttons[name] = control
            modes.addWidget(control)
        tools.addWidget(switch)
        self.empty = QtWidgets.QWidget(self.view.viewport())
        empty_layout = QtWidgets.QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(28, 26, 28, 26)
        empty_layout.setSpacing(16)
        headline = label("Your work, connected.")
        headline.setStyleSheet("font-size: 27px; font-weight: 400; color: #e1e5eb;")
        empty_layout.addWidget(headline)
        explanation = label("Bring the parts you need onto this canvas.\nApps, documents and ideas can share a working set.", "muted")
        explanation.setStyleSheet("font-size: 12px; color: #a2b0c0;")
        empty_layout.addWidget(explanation)
        empty_actions = QtWidgets.QHBoxLayout()
        empty_actions.addWidget(button("Add a window or source", self.show_launcher, name="primary"))
        empty_actions.addWidget(button("Start a note", self.new_note))
        empty_actions.addWidget(button("Ask Toolkit", self.open_composer))
        empty_layout.addLayout(empty_actions)
        self.compose_open = False
        self.composer_surface = QtWidgets.QWidget(self.view.viewport())
        self.composer_surface.setObjectName("selectionComposer")
        compose_layout = QtWidgets.QVBoxLayout(self.composer_surface)
        compose_layout.setContentsMargins(14, 9, 14, 10)
        compose_layout.setSpacing(10)
        self.scope_chips = QtWidgets.QWidget()
        self.context = label("", "muted")
        scope = QtWidgets.QHBoxLayout(self.scope_chips)
        scope.setContentsMargins(0, 0, 0, 0)
        scope.addStretch()
        scope.addWidget(self.context)
        compose_layout.addWidget(self.scope_chips)
        compose_row = QtWidgets.QHBoxLayout()
        compose_row.setSpacing(10)
        self.composer = QtWidgets.QLineEdit()
        self.composer.setAccessibleName("Request for selected canvas context")
        self.composer.setPlaceholderText("What would you like to do with these?")
        self.composer.setMaxLength(8_000)
        self.composer.setMinimumHeight(32)
        self.composer.textChanged.connect(self.save_composer)
        self.composer.returnPressed.connect(self.submit)
        compose_row.addWidget(self.composer, 1)
        compose_row.addWidget(label("N  Speak", "muted"))
        compose_row.addWidget(button("Ask Toolkit →", self.submit, name="primary"))
        compose_layout.addLayout(compose_row)
        self.footer = QtWidgets.QWidget(self.view.viewport())
        footer_layout = QtWidgets.QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        keyboard = button("Keyboard help", self.actions.keyboard)
        keyboard.setIcon(icon("keyboard", size=13))
        keyboard.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        footer_layout.addWidget(keyboard)
        self.parked = button("Parked · 0", self.show_parked)
        footer_layout.addWidget(self.parked)
        footer_layout.addStretch()
        zoom_controls = QtWidgets.QWidget()
        zoom_controls.setObjectName("zoomBar")
        zoom_layout = QtWidgets.QHBoxLayout(zoom_controls)
        zoom_layout.setContentsMargins(3, 2, 3, 2)
        zoom_layout.setSpacing(0)
        zoom_layout.addWidget(button("\u2212", lambda: self.set_zoom(self.view.transform().m11() / 1.15), tip="Zoom out"))
        self.zoom = button("90%", lambda: self.set_zoom(1), tip="Reset zoom to 100%")
        zoom_layout.addWidget(self.zoom)
        zoom_layout.addWidget(button("+", lambda: self.set_zoom(self.view.transform().m11() * 1.15), tip="Zoom in"))
        zoom_layout.addWidget(button("⌑", self.overview, tip="Fit the working set"))
        footer_layout.addWidget(zoom_controls)
        map_toggle = button("", self.toggle_map, tip="Overview map")
        map_toggle.setIcon(icon("grid", size=13))
        footer_layout.addWidget(map_toggle)
        self.map = MiniMap(self.navigate)
        self.map.setParent(self.view.viewport())
        self.map.hide()
        self.status = label("", "status")
        self.status.setParent(self.view.viewport())
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background: #253240; border: 1px solid #51657b; border-radius: 8px; padding: 9px 14px; color: #efd5a6;")
        self.source_tray = QtWidgets.QWidget()
        self.source_tray.setObjectName("sourceTray")
        self.source_tray.setStyleSheet(STYLE)
        tray_layout = QtWidgets.QHBoxLayout(self.source_tray)
        tray_layout.setContentsMargins(18, 12, 18, 12)
        tray_layout.addWidget(label("Your working set\nKeep the useful parts in view.", "muted"), 1)
        tray_layout.addWidget(button("+  Add a window or source", self.show_launcher))
        self.source_proxy = self.scene.addWidget(self.source_tray)
        self.source_proxy.setZValue(-.5)
        self.source_proxy.hide()
        self.launcher = Launcher(self.activate, self.connect_edge)
        self.bar = QtWidgets.QWidget()
        self.bar.setObjectName("shellBar")
        self.bar.setStyleSheet(STYLE)
        self.bar.setWindowTitle("Toolkit desktop bar")
        self.bar.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.bar.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        bar_layout = QtWidgets.QHBoxLayout(self.bar)
        bar_layout.setContentsMargins(20, 3, 20, 3)
        bar_layout.setSpacing(10)
        bar_layout.addWidget(button("●  toolkit", self.show_launcher, tip="Launcher · Ctrl+Alt+Space"))
        self.bar_workspace = button("My workspace", self.show, tip="Show canvas · Ctrl+Alt+D")
        bar_layout.addWidget(self.bar_workspace)
        self.bar_apps = QtWidgets.QHBoxLayout()
        self.bar_apps.setSpacing(2)
        bar_layout.addLayout(self.bar_apps)
        bar_layout.addStretch()
        self.bar_layer = button("BASE", actions.keyboard, tip="Current keyboard layer · click for key map")
        bar_layout.addWidget(self.bar_layer)
        self.bar_status = button("Agent", actions.agent, tip="Agent session · hold N to talk")
        bar_layout.addWidget(self.bar_status)
        self.bar_stop = button("■", actions.stop, tip="Stop agent work")
        self.bar_stop.setVisible(False)
        bar_layout.addWidget(self.bar_stop)
        bar_layout.addWidget(button("All work", actions.work, tip="Open durable work overview"))
        self.bar_record = button("○ Record", self.record, tip="Record a meeting independently of agent work")
        bar_layout.addWidget(self.bar_record)
        self.bar_clock = label("", "muted")
        bar_layout.addWidget(self.bar_clock)
        bar_layout.addWidget(button("···", self.shell_menu, tip="Desktop and Windows controls"))
        self._shortcuts: list[QtGui.QShortcut] = []
        for key, action in (("Ctrl+K", self.show_launcher), ("Ctrl+0", self.overview), ("Escape", self.escape)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.surface)
            shortcut.activated.connect(action)
            self._shortcuts.append(shortcut)
        if native_shell:
            try:
                self.bar.show()
                self.appbar = desktop_shell.AppBar(int(self.bar.winId()))
                self.position_bar()
                self.keys_window = shell.AppWindow("toolkit desktop controls", self.hotkey)
                self.hotkeys = desktop_shell.Hotkeys(self.keys_window.handle)
                if len(self.hotkeys.registered) < 2:
                    self.controller.status = "A desktop shortcut is already in use. The Toolkit bar still opens the launcher and canvas."
                if self.controller.state.replace_taskbar:
                    self.background(self.taskbar.enable, self.taskbar_ready, failed=self.taskbar_problem)
            except OSError as error:
                self.controller.status = str(error)
        self.render()
        self.loop.watch(True)
        self.tick()

    def open_composer(self) -> None:
        self.compose_open = True
        self.composer_surface.show()
        self.layout_controls()
        self.composer.setFocus()

    def toggle_map(self) -> None:
        self.map.setVisible(not self.map.isVisible())
        self.layout_controls()

    def add_menu(self) -> None:
        menu = QtWidgets.QMenu(self.surface)
        menu.addAction("Find an app, window or page", self.show_launcher)
        menu.addAction("Add a document", self.choose_file)
        menu.addAction("Paste a clipboard snapshot", self.paste)
        menu.addAction("New note", self.new_note)
        menu.addAction("Ask Toolkit", self.open_composer)
        menu.addSeparator()
        menu.addAction("Arrange selected app windows", self.arrange)
        self.popup(menu)

    def layout_controls(self) -> None:
        if not hasattr(self, "status"):
            return
        width, height = self.view.viewport().width(), self.view.viewport().height()
        self.tools.setGeometry(23, 12, max(100, width - 46), 38)
        self.footer.setGeometry(23, height - 52, max(100, width - 46), 34)
        composer_width = min(600, max(350, width - 60))
        self.composer_surface.setGeometry((width - composer_width) // 2, height - (142 if width < 1000 else 113), composer_width, 88)
        self.map.move(width - self.map.width() - 24, height - self.map.height() - 65)
        self.status.setGeometry(max(20, (width - 560) // 2), 63, min(560, width - 40), 48)
        self.empty.adjustSize()
        self.empty.move(max(24, (width - self.empty.width()) // 2), max(70, (height - self.empty.height()) // 2 - 45))
        for widget in (self.tools, self.footer, self.composer_surface, self.map, self.status):
            widget.raise_()

    def update_group(self) -> None:
        sources = [proxy.sceneBoundingRect() for card, proxy in self.cards.values()
                   if isinstance(card.content, (m.Source, m.Note))]
        bounds = QtCore.QRectF()
        for rectangle in sources:
            bounds = bounds.united(rectangle)
        if sources:
            left_column = [rectangle for rectangle in sources if rectangle.left() < bounds.left() + 200]
            tray_top = max(rectangle.bottom() for rectangle in left_column) + 20
            self.view.group = bounds.adjusted(-28, -70, 28, max(24, tray_top + 84 - bounds.bottom()))
            self.view.group_count = len(sources)
            self.source_proxy.setPos(bounds.left(), tray_top)
            self.source_proxy.resize(min(410, bounds.width()), 62)
            self.source_proxy.show()
        else:
            self.view.group = QtCore.QRectF()
            self.view.group_count = 0
            self.source_proxy.hide()
        self.view.viewport().update()

    def _work(self) -> None:
        try:
            while (job := self.work_queue.get()) is not None:
                job()
        finally:
            try:
                self.native.close()
            except Exception as error:
                self._worker_error = f"Canvas resource shutdown: {error}"
                log_view(self._worker_error)

    def background[T](self, operation: Callable[[], T], finished: Callable[[T], None], *, failed: Callable[[str], None] | None = None) -> None:
        if self.closed:
            return
        def job() -> None:
            if self.closed:
                return
            try:
                value = operation()
                self.queue.put(lambda: finished(value))
            except Exception as error:
                message = str(error) or type(error).__name__
                log_view(f"Canvas: {message}")
                self.queue.put(lambda: (failed or self.background_problem)(message))
        try:
            self.work_queue.put_nowait(job)
        except queue.Full:
            self.controller.problem("The desktop is busy reading resources. Try again in a moment.")

    def background_problem(self, message: str) -> None:
        self._catalogue_pending = False
        self.controller.problem(message)
        if self.launcher.isVisible():
            self.launcher.status.setText(message)

    def tick(self) -> None:
        if self.closed:
            return
        for _ in range(64):
            try:
                self.queue.get_nowait()()
            except queue.Empty:
                break
        self.session = self.actions.session()
        self.controller.drain()
        self.voice_active = self.active_context()
        now = time.monotonic()
        if self._native_shell and now - self._catalogue_at > 3 and not self._catalogue_pending:
            self._catalogue_at = now
            self._catalogue_pending = True
            self.background(catalogue, self.catalogue_ready)
        self.bar_clock.setText(time.strftime("%a  %H:%M"))
        if self.session is not None:
            self.bar_status.setText(self.session.status if self.session.busy else "Agent")
            self.bar_stop.setVisible(self.session.busy)
            self.bar_record.setText("● Recording" if self.session.recording.capturing else "○ Record")
        self.position_bar()
        self.sync_previews()
        self.timer = later(self.root, 120, self.tick)

    def catalogue_ready(self, value: Catalogue) -> None:
        self._catalogue_pending = False
        self.catalogue = replace(value, pages=self.catalogue.pages)
        self.launcher.update_entries(self.controller.state, self.catalogue)
        titles = tuple(f"{item.id}:{item.title}" for item in value.windows)
        if titles != self._results:
            self._results = titles
            while self.bar_apps.count():
                child = self.bar_apps.takeAt(0)
                if child is not None and (widget := child.widget()) is not None:
                    widget.deleteLater()
        state = self.controller.state
        for item in value.windows:
            if state.item(item.id) is not None:
                state = m.observe(state, item)
        self.controller.commit(state)

    def position_bar(self) -> None:
        if not self._native_shell or self.appbar is None:
            return
        monitors = screen.monitors()
        monitor = next((value for value in monitors if value.primary), monitors[0])
        if monitor.area == self._geometry:
            return
        self._geometry = monitor.area
        ratio = self.bar.devicePixelRatioF()
        bounds = self.appbar.place(monitor.area, round(42 * ratio))
        self.bar.setGeometry(round(bounds.left / ratio), round(bounds.top / ratio), round(bounds.width / ratio), round(bounds.height / ratio))

    def hotkey(self, _hwnd: int, message: int, wparam: int, _lparam: int) -> int | None:
        if message == desktop_shell.WM_HOTKEY:
            if wparam == desktop_shell.LAUNCHER_KEY:
                self.show_launcher()
            elif wparam == desktop_shell.CANVAS_KEY:
                self.show()
            return 0
        return None

    def layer(self, text: str | None) -> None:
        self.bar_layer.setText((text or "BASE")[:24])

    def active_context(self) -> bool:
        if not self.surface.isVisible() or self.surface.isMinimized() or not self.controller.state.space.selection:
            return False
        if self.surface.isActiveWindow():
            return True
        foreground = windows.foreground()
        for identity in self.controller.state.space.selection:
            item = self.controller.state.item(identity)
            if (item is not None and isinstance(item.content, m.Source) and isinstance(item.content.origin, m.LiveWindow)
                    and item.content.origin.handle == foreground and windows.process_id(foreground) == item.content.origin.pid):
                return True
        return False

    def show(self) -> None:
        if not self._shown:
            self.surface.showMaximized()
            self._shown = True
        else:
            self.surface.show()
        self.surface.raise_()
        self.surface.activateWindow()
        self.view.restore(self.controller.state.space.camera)
        if self._native_shell:
            handle = int(self.surface.winId())
            self.background(lambda: windows.focus(handle), lambda _: None)

    def show_launcher(self) -> None:
        self.hide_previews()
        self.launcher.update_entries(self.controller.state, self.catalogue)
        display = self.loop.app.screenAt(QtGui.QCursor.pos()) or self.loop.app.primaryScreen()
        bounds = display.availableGeometry()
        self.launcher.move(bounds.center().x() - self.launcher.width() // 2, bounds.top() + max(35, bounds.height() // 7))
        self.launcher.reveal()
        if self._native_shell:
            handle = int(self.launcher.winId())
            self.background(lambda: windows.focus(handle), lambda _: None)

    def render(self) -> None:  # noqa: PLR0915  # Reconcile retained widgets without replacing editors.
        if self.closed or self._rendering:
            return
        self._rendering = True
        try:
            state = self.controller.state
            space = state.space
            switched = self.space_id != space.id
            self.spaces.blockSignals(True)
            self.spaces.clear()
            for value in state.spaces:
                self.spaces.addItem(value.title, value.id)
            self.spaces.setCurrentIndex(next(index for index, value in enumerate(state.spaces) if value.id == space.id))
            self.spaces.blockSignals(False)
            self.bar_workspace.setText(space.title[:28])
            self.surface.setWindowTitle(f"Toolkit · {space.title}")
            if switched:
                self.previous_camera = None
                self.view_mode = "Canvas"
                self.composer.blockSignals(True)
                self.composer.setText(space.composer)
                self.composer.blockSignals(False)
            visible = {placement.item_id for placement in space.placements if not placement.parked}
            for identity in tuple(self.cards):
                if identity not in visible:
                    card, proxy = self.cards.pop(identity)
                    self.scene.removeItem(proxy)
                    proxy.deleteLater()
                    if (thumbnail := self.thumbnails.pop(identity, None)) is not None:
                        thumbnail.close()
            for placement in space.placements:
                item = state.item(placement.item_id)
                if item is None or placement.parked:
                    continue
                if item.id not in self.cards:
                    card = Card(item, self.action, self.edit, move=self.mover(item.id), finished=self.save_positions,
                                scale=lambda: self.view.transform().m11())
                    proxy = self.scene.addWidget(card)
                    self.cards[item.id] = card, proxy
                card, proxy = self.cards[item.id]
                if card.header.previous is None:
                    proxy.setPos(placement.x, placement.y)
                    proxy.resize(placement.width, placement.height)
                card.update_item(item, selected=item.id in space.selection, stale=m.outdated(state, item.id))
            self.space_id = space.id
            if switched:
                self.view.restore(space.camera)
            count = sum(placement.parked for placement in space.placements)
            self.parked.setText(f"Parked · {count}")
            self.parked.setEnabled(count > 0)
            chosen = tuple((item.id, item.title) for identity in space.selection if (item := state.item(identity)) is not None)
            titles = [title for _, title in chosen]
            self.context.setText(f"{len(titles)} selected" if titles else "Ask about this workspace")
            if chosen != self._scope_key:
                self._scope_key = chosen
                scope = self.scope_chips.layout()
                assert isinstance(scope, QtWidgets.QHBoxLayout)
                while scope.count() > 2:
                    child = scope.takeAt(0)
                    if child is not None and (widget := child.widget()) is not None:
                        widget.deleteLater()
                for index, (identity, title) in enumerate(chosen[:4]):
                    chip = button(title[:22], partial(self.action, identity, "focus"), tip=f"Show {title}", name="chip")
                    scope.insertWidget(index, chip)
            for name, control in self.view_buttons.items():
                control.setChecked(name == self.view_mode)
            self.view_buttons["Focus"].setEnabled(bool(chosen))
            self.breadcrumb.setText(f"\u203a   {len(visible)} items")
            self.composer_surface.setVisible(bool(titles) or bool(space.composer) or self.compose_open)
            self.context.setToolTip("\n".join(titles))
            self.status.setText(self.controller.status)
            self.status.setVisible(bool(self.controller.status))
            self.empty.setVisible(not visible)
            self.empty.adjustSize()
            self.empty.move(max(24, (self.view.viewport().width() - self.empty.width()) // 2),
                            max(24, (self.view.viewport().height() - self.empty.height()) // 2 - 20))
            self.draw_connections()
            self.layout_controls()
            self.launcher.update_entries(state, self.catalogue)
        finally:
            self._rendering = False

    def draw_connections(self) -> None:
        for wire in self.wires:
            self.scene.removeItem(wire)
        self.wires.clear()
        for identity, (_, proxy) in self.cards.items():
            item = self.controller.state.item(identity)
            if item is None:
                continue
            for source in m.sources(item.content):
                if source.item_id not in self.cards:
                    continue
                source_proxy = self.cards[source.item_id][1]
                a, b = source_proxy.sceneBoundingRect(), proxy.sceneBoundingRect()
                path = QtGui.QPainterPath(QtCore.QPointF(a.right(), a.center().y()))
                distance = max(60., abs(b.left() - a.right()) * .5)
                path.cubicTo(a.right() + distance, a.center().y(), b.left() - distance, b.center().y(), b.left(), b.center().y())
                stale = m.outdated(self.controller.state, identity)
                pen = QtGui.QPen(QtGui.QColor(GOLD if stale else "#887d9d"), 1.3)
                if stale:
                    pen.setStyle(Qt.PenStyle.DashLine)
                wire = self.scene.addPath(path, pen)
                wire.setZValue(-1)
                self.wires.append(wire)
        self.update_group()
        self.update_map()

    def move_card(self, identity: str, x: float, y: float) -> None:
        proxy = self.cards[identity][1]
        proxy.setZValue(1 + max((p.zValue() for _, p in self.cards.values()), default=0))
        bounds = self.view.sceneRect()
        proxy.setPos(min(bounds.right() - proxy.size().width(), max(bounds.left(), proxy.x() + x)),
                     min(bounds.bottom() - proxy.size().height(), max(bounds.top(), proxy.y() + y)))
        self.draw_connections()
        self.sync_previews()

    def mover(self, identity: str) -> Callable[[float, float], None]:
        return lambda x, y: self.move_card(identity, x, y)

    def save_positions(self) -> None:
        state = self.controller.state
        for identity, (_, proxy) in self.cards.items():
            old = next(p for p in state.space.placements if p.item_id == identity)
            state = m.place(state, replace(old, x=proxy.x(), y=proxy.y()))
        self.controller.commit(state, render=False)

    def edit(self, identity: str, text: str) -> None:
        self.controller.commit(m.edit(self.controller.state, identity, text), render=False)
        # Reconcile derived cards after the native editor completes this signal.
        self.view_timer.start()

    def view_changed(self) -> None:
        if not hasattr(self, "view_timer") or self.closed:
            return
        self.view_timer.start()
        self.layout_controls()
        self.update_map()
        self.hide_previews()

    def save_view(self) -> None:
        if self.closed or self._rendering:
            return
        self.controller.commit(m.with_space(self.controller.state, replace(self.controller.state.space, camera=self.view.camera())), render=False)
        self.render()

    def save_composer(self, text: str) -> None:
        if not self._rendering:
            self.controller.commit(m.with_space(self.controller.state, replace(self.controller.state.space, composer=text)), render=False)

    def update_map(self) -> None:
        if not hasattr(self, "map"):
            return
        self.map.rectangles = tuple(proxy.sceneBoundingRect() for _, proxy in self.cards.values())
        self.map.viewport = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        self.map.update()
        self.zoom.setText(f"{self.view.transform().m11():.0%}")

    def set_zoom(self, zoom: float) -> None:
        current = self.view.camera()
        self.view.restore(replace(current, zoom=min(1.5, max(.25, zoom))))
        self.save_view()

    def navigate(self, x: float, y: float) -> None:
        self.view.centerOn(x, y)
        self.save_view()

    def fit(self, identities: tuple[str, ...]) -> None:
        bounds = QtCore.QRectF()
        for identity in identities:
            if identity in self.cards:
                bounds = bounds.united(self.cards[identity][1].sceneBoundingRect())
        if bounds.isEmpty():
            return
        if set(identities) == set(self.cards):
            bounds = bounds.united(self.view.group)
        bounds.adjust(-32, -12, 32, 12)
        width, height = self.view.viewport().width() - 64, self.view.viewport().height() - 190
        zoom = min(1.15, max(.25, min(width / bounds.width(), height / bounds.height())))
        self.view.restore(m.Camera(bounds.center().x(), bounds.center().y() + 28 / zoom, zoom))
        self.save_view()

    def overview(self) -> None:
        if self.view_mode == "Canvas":
            self.previous_camera = self.view.camera()
        self.view_mode = "Overview"
        self.map.show()
        self.fit(tuple(self.cards))

    def canvas_mode(self) -> None:
        self.view_mode = "Canvas"
        self.map.hide()
        if self.previous_camera is not None:
            camera, self.previous_camera = self.previous_camera, None
            self.view.restore(camera)
        self.save_view()

    def focus_selection(self) -> None:
        chosen = self.controller.state.space.selection
        if chosen:
            if self.view_mode == "Canvas":
                self.previous_camera = self.view.camera()
            self.view_mode = "Focus"
            self.map.hide()
            self.fit(chosen)

    def escape(self) -> None:
        if self.launcher.isVisible():
            self.launcher.hide()
        elif self.previous_camera is not None:
            self.canvas_mode()
        else:
            self.surface.hide()
            self.hide_previews()

    def switch_space(self, index: int) -> None:
        if not self._rendering and 0 <= index < len(self.controller.state.spaces):
            self.save_view()
            self.controller.commit(replace(self.controller.state, current=self.controller.state.spaces[index].id))

    def text_dialog(self, title: str, initial: str, accepted: Callable[[str], None]) -> None:
        dialog = QtWidgets.QDialog(self.surface)
        dialog.setWindowTitle(title)
        dialog.setStyleSheet(STYLE)
        dialog.setMinimumWidth(400)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(label(title, "title"))
        field = QtWidgets.QLineEdit(initial)
        field.setMaxLength(240)
        layout.addWidget(field)
        def save() -> None:
            if field.text().strip():
                try:
                    accepted(field.text().strip())
                    dialog.accept()
                except ValueError as error:
                    self.controller.problem(str(error))
        field.returnPressed.connect(save)
        layout.addWidget(button("Save", save, name="primary"))
        self.dialogs.append(dialog)
        dialog.finished.connect(partial(self.dismiss_dialog, dialog))
        dialog.open()
        field.selectAll()
        field.setFocus()

    def dismiss_dialog(self, dialog: QtWidgets.QDialog | QtWidgets.QMenu, _result: int = 0) -> None:
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)
        dialog.deleteLater()

    def new_space(self) -> None:
        self.text_dialog("Workspace name", "New workspace", self.controller.new_space)

    def new_note(self) -> None:
        identity = uuid.uuid4().hex
        self.controller.commit(m.add(self.controller.state, m.Item(identity, "Untitled note", m.Note(""))))
        self.show()
        self.fit((identity,))
        editor = self.cards[identity][0].editor
        if editor is not None:
            editor.setFocus()

    def choose_file(self) -> None:
        dialog = QtWidgets.QFileDialog(self.surface, "Add a document to the canvas")
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFiles)
        dialog.setNameFilter("Documents (*.txt *.md *.csv *.tsv *.pdf *.docx *.xlsx);;All files (*)")
        dialog.filesSelected.connect(self.add_file_list)
        self.dialogs.append(dialog)
        dialog.finished.connect(partial(self.dismiss_dialog, dialog))
        dialog.open()

    def add_files(self, paths: tuple[str, ...]) -> None:
        for name in paths[:24]:
            identity = uuid.uuid4().hex
            self.background(partial(file_item, Path(name), identity), self.add_item)

    def add_file_list(self, paths: list[str]) -> None:
        self.add_files(tuple(paths))

    def add_item(self, item: m.Item) -> None:
        self.controller.status = ""
        state = self.controller.state
        if isinstance(item.content, m.Source) and state.item(item.id) is not None:
            state = m.observe(state, item)
            item = state.item(item.id) or item
        self.controller.commit(m.add(state, item))
        self.show()
        self.fit(tuple(self.cards) if len(self.cards) <= 8 else (item.id,))

    def paste(self) -> None:
        text = self.loop.app.clipboard().text()
        try:
            m.bounded(text, "Clipboard text")
            self.add_item(m.Item(uuid.uuid4().hex, text.strip().splitlines()[0][:100], m.Source(m.LocalSource.CLIPBOARD, text, time.time())))
        except ValueError as error:
            self.controller.problem(str(error))

    def activate(self, entry: Entry) -> None:
        match entry:
            case m.Item():
                self.add_item(entry)
            case Application():
                self.background(lambda: self.native.launch(entry), lambda _: None)
            case Page():
                self.controller.problem("Reading the selected Edge page…")
                self.background(lambda: self.native.page(entry.id, f"page-{entry.id}"), self.add_item)

    def connect_edge(self) -> None:
        self.launcher.status.setText("Waiting for Edge. Accept its connection prompt if one appears.")
        def ready(pages: tuple[Page, ...]) -> None:
            self.catalogue = replace(self.catalogue, pages=pages)
            self.launcher.update_entries(self.controller.state, self.catalogue)
            self.launcher.scope.setCurrentText("Edge pages")
        self.background(self.native.connect_pages, ready)

    def submit(self) -> None:
        text = self.composer.text().strip()
        if text and self.controller.submit(text):
            self.composer.clear()
            result = self.controller.state.items[-1]
            self.fit((*self.controller.state.space.selection, result.id))

    def open_item(self, item: m.Item) -> None:
        if isinstance(item.content, m.Source):
            origin, identity = item.content.origin, item.id
            self.background(lambda: self.native.open(origin, identity), lambda _: None)

    def action(self, identity: str, operation: str) -> None:
        try:
            self._action(identity, operation)
        except (ValueError, OSError) as error:
            self.controller.problem(str(error))

    def _action(self, identity: str, operation: str) -> None:
        state = self.controller.state
        item = state.item(identity)
        if item is None:
            return
        if operation.startswith("answer:"):
            self.controller.answer(identity, operation.removeprefix("answer:"))
            return
        if operation.startswith("link:"):
            page = m.WebPage(operation.removeprefix("link:"))
            self.background(lambda: self.native.open(page), lambda _: None)
            return
        match operation:
            case "select":
                self.controller.commit(m.select(state, identity, multiple=True))
            case "park":
                self.controller.commit(m.park(state, identity))
            case "focus":
                if self.view_mode == "Canvas":
                    self.previous_camera = self.view.camera()
                self.view_mode = "Focus"
                self.fit((identity,))
            case "open":
                self.open_item(item)
            case "copy":
                self.loop.app.clipboard().setText(m.body(item.content))
                self.controller.problem("Copied to the clipboard.")
            case "draft":
                draft_id = uuid.uuid4().hex
                self.controller.commit(m.draft(state, identity, draft_id))
                self.fit((identity, draft_id))
            case "mail":
                self.background(lambda: mail_draft(item.title, m.body(item.content)),
                                lambda _: self.controller.problem("Draft opened in your mail app."))
            case "refresh":
                if isinstance(item.content, m.Result):
                    self.controller.submit(item.content.prompt, result_id=identity)
            case "refresh_source":
                self.background(lambda: self.native.refresh(item),
                                lambda observed: self.controller.commit(m.observe(self.controller.state, observed)))
            case "stop":
                self.controller.cancel(identity)
            case "retry":
                self.controller.retry(identity)
            case "sources":
                self.show_sources(item)
            case "menu":
                self.item_menu(item)
            case _:
                raise ValueError("This canvas action is unavailable.")

    def popup(self, menu: QtWidgets.QMenu) -> None:
        menu.setStyleSheet(STYLE)
        self.dialogs.append(menu)
        menu.aboutToHide.connect(lambda: self.dismiss_dialog(menu))
        self.hide_previews()
        menu.popup(QtGui.QCursor.pos())

    def item_menu(self, item: m.Item) -> None:
        menu = QtWidgets.QMenu(self.surface)
        def rename(title: str) -> None:
            current = self.controller.state.item(item.id)
            if current is not None:
                self.controller.commit(m.put(self.controller.state, replace(current, title=title)))
        menu.addAction("Rename…", lambda: self.text_dialog("Item title", item.title, rename))
        menu.addAction("Copy text", lambda: self.action(item.id, "copy"))
        menu.addAction("Export text…", lambda: self.export(item))
        menu.addAction("Make an editable draft", lambda: self.action(item.id, "draft"))
        menu.addSeparator()
        menu.addAction("Larger card", lambda: self.resize_card(item.id, 560, 540))
        menu.addAction("Compact card", lambda: self.resize_card(item.id, 440, 340))
        menu.addAction("Park", lambda: self.action(item.id, "park"))
        menu.addAction("Remove from this workspace", lambda: self.controller.commit(m.remove(self.controller.state, item.id)))
        menu.addSeparator()
        menu.addAction("Delete saved canvas item…", lambda: self.delete_item(item))
        self.popup(menu)

    def delete_item(self, item: m.Item) -> None:
        if isinstance(item.content, m.Result) and isinstance(item.content.state, m.Running):
            self.controller.problem("Stop this result before deleting its saved canvas item.")
            return
        dialog = QtWidgets.QDialog(self.surface)
        dialog.setWindowTitle("Delete saved canvas item")
        dialog.setStyleSheet(STYLE)
        dialog.setMinimumWidth(400)
        layout = QtWidgets.QVBoxLayout(dialog)
        heading = label(f"Delete {item.title}?", "title")
        heading.setWordWrap(True)
        heading.setMaximumWidth(520)
        layout.addWidget(heading)
        explanation = label("Delete this saved canvas item from every workspace. Existing results and drafts keep their source snapshots.", "muted")
        explanation.setWordWrap(True)
        explanation.setMaximumWidth(520)
        layout.addWidget(explanation)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Cancel", dialog.reject))
        def confirm() -> None:
            try:
                self.controller.commit(m.delete(self.controller.state, item.id))
                dialog.accept()
            except ValueError as error:
                explanation.setText(str(error))
        row.addWidget(button("Delete saved item", confirm, name="primary"))
        layout.addLayout(row)
        self.dialogs.append(dialog)
        dialog.finished.connect(partial(self.dismiss_dialog, dialog))
        dialog.open()

    def resize_card(self, identity: str, width: float, height: float) -> None:
        position = next(value for value in self.controller.state.space.placements if value.item_id == identity)
        self.controller.commit(m.place(self.controller.state, replace(position, width=width, height=height)))

    def export(self, item: m.Item) -> None:
        dialog = QtWidgets.QFileDialog(self.surface, "Export canvas text")
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("md")
        dialog.setNameFilter("Markdown (*.md);;Text (*.txt)")
        dialog.selectFile("canvas-note.md")
        def export_selected(path: str) -> None:
            self.background(lambda: Path(path).write_text(m.body(item.content), encoding="utf-8"),
                            lambda _: self.controller.problem("Exported."))
        dialog.fileSelected.connect(export_selected)
        self.dialogs.append(dialog)
        dialog.finished.connect(partial(self.dismiss_dialog, dialog))
        dialog.open()

    def show_sources(self, item: m.Item) -> None:
        dialog = QtWidgets.QDialog(self.surface)
        dialog.setWindowTitle(f"Sources · {item.title}")
        dialog.resize(760, 540)
        dialog.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(label("The observations used for this result", "title"))
        selector = QtWidgets.QComboBox()
        used = m.sources(item.content)
        for source in used:
            selector.addItem(f"{source.title} · version {source.revision}")
        layout.addWidget(selector)
        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text, 1)
        def selected(index: int) -> None:
            if 0 <= index < len(used):
                text.setPlainText(used[index].text)
        selector.currentIndexChanged.connect(selected)
        if used:
            text.setPlainText(used[0].text)
        def reveal() -> None:
            if 0 <= selector.currentIndex() < len(used):
                source = self.controller.state.item(used[selector.currentIndex()].item_id)
                if source is not None:
                    self.add_item(source)
                    dialog.accept()
        layout.addWidget(button("Show source on canvas ↗", reveal))
        self.dialogs.append(dialog)
        dialog.finished.connect(partial(self.dismiss_dialog, dialog))
        self.hide_previews()
        dialog.open()

    def show_parked(self) -> None:
        menu = QtWidgets.QMenu(self.surface)
        for placement in self.controller.state.space.placements:
            if placement.parked and (item := self.controller.state.item(placement.item_id)) is not None:
                menu.addAction(item.title, partial(self.add_item, item))
        self.popup(menu)

    def arrange(self) -> None:
        items = tuple(item for identity in self.controller.state.space.selection if (item := self.controller.state.item(identity)) is not None)
        handle = int(self.surface.winId())
        self.background(lambda: self.native.arrange(items, handle), lambda _: None)

    def record(self) -> None:
        self.actions.record("stop" if self.session is not None and self.session.recording.capturing else "start")

    def shell_menu(self) -> None:
        menu = QtWidgets.QMenu(self.bar)
        toggle = menu.addAction("Use Toolkit as the taskbar", self.toggle_taskbar)
        toggle.setCheckable(True)
        toggle.setChecked(self.controller.state.replace_taskbar)
        menu.addAction("Restore arranged app windows", lambda: self.background(self.native.restore, lambda _: None))
        menu.addAction("Keyboard overlay", lambda: self.actions.overlay())  # noqa: PLW0108  # Slotted owner; Qt needs the closure.
        menu.addAction("Key map", lambda: self.actions.keyboard())  # noqa: PLW0108
        menu.addAction("Transcripts", lambda: self.actions.record("folder"))
        menu.addSeparator()
        for title, target in (("Sound", "ms-settings:sound"), ("Network", "ms-settings:network-status"), ("Windows settings", "ms-settings:")):
            menu.addAction(title, partial(self.open_setting, target))
        menu.addSeparator()
        menu.addAction("Hide canvas", self.surface.hide)
        self.popup(menu)

    def open_setting(self, target: str) -> None:
        import os  # noqa: PLC0415

        self.background(lambda: os.startfile(target), lambda _: None)

    def toggle_taskbar(self) -> None:
        enabled = not self.controller.state.replace_taskbar
        self.background(self.taskbar.enable if enabled else self.taskbar.disable, self.taskbar_ready, failed=self.taskbar_problem)

    def taskbar_ready(self, _value: None) -> None:
        self.controller.commit(replace(self.controller.state, replace_taskbar=bool(self.taskbar.hidden)))

    def taskbar_problem(self, message: str) -> None:
        self.controller.commit(replace(self.controller.state, replace_taskbar=False))
        self.controller.problem(message)
        if self._native_shell:
            self.show()

    def hide_previews(self) -> None:
        for thumbnail in self.thumbnails.values():
            with contextlib.suppress(OSError):
                thumbnail.show(None)

    def acknowledge_previews(self) -> None:
        """A cached review is approvable only after this exact native view paints."""
        if self.closed or not self.surface.isVisible() or self.surface.isMinimized() or self.launcher.isVisible() or any(
            dialog.isVisible() for dialog in self.dialogs
        ):
            return
        for identity, (card, proxy) in self.cards.items():
            content = card.content
            if not isinstance(content, m.Result) or not isinstance(content.state, m.Running) or not isinstance(content.state.stage, m.Approval):
                continue
            if card.approve.isEnabled() or not card.question.isVisible():
                continue
            point = card.question.mapTo(card, QtCore.QPoint())
            area = proxy.mapRectToScene(QtCore.QRectF(QtCore.QPointF(point), QtCore.QSizeF(card.question.size())))
            visible = self.view.viewport().rect().contains(self.view.mapFromScene(area).boundingRect())
            visible = visible and not any(other is not proxy and other.zValue() >= proxy.zValue()
                                          and other.sceneBoundingRect().intersects(area) for _, other in self.cards.values())
            if visible and self.controller.presented(identity, content.state.stage):
                card.approve.setEnabled(True)

    def sync_previews(self) -> None:
        if not self._native_shell or not self.surface.isVisible() or self.surface.isMinimized() or self.launcher.isVisible() or any(
            dialog.isVisible() for dialog in self.dialogs
        ):
            self.hide_previews()
            return
        for identity, (card, proxy) in self.cards.items():
            if card.preview is None or not isinstance(card.content, m.Source) or not isinstance(card.content.origin, m.LiveWindow):
                continue
            origin = card.content.origin
            point = card.preview.mapTo(card, QtCore.QPoint(0, 0))
            scene_rect = proxy.mapRectToScene(QtCore.QRectF(QtCore.QPointF(point), QtCore.QSizeF(card.preview.size())))
            target = self.view.mapFromScene(scene_rect).boundingRect()
            visible = self.view.viewport().rect().contains(target) and self.view.transform().m11() >= .45
            visible = visible and not any(other is not proxy and other.zValue() > proxy.zValue()
                                          and other.sceneBoundingRect().intersects(scene_rect) for _, other in self.cards.values())
            if not visible or not windows.exists(origin.handle) or windows.process_id(origin.handle) != origin.pid:
                if identity in self.thumbnails:
                    with contextlib.suppress(OSError):
                        self.thumbnails[identity].show(None)
                continue
            try:
                if identity not in self.thumbnails:
                    if len(self.thumbnails) >= 12:
                        continue
                    self.thumbnails[identity] = desktop_shell.Thumbnail(int(self.surface.winId()), origin.handle)
                top = self.view.viewport().mapTo(self.surface, target.topLeft())
                ratio = self.surface.devicePixelRatioF()
                bounds = Rect(round(top.x() * ratio), round(top.y() * ratio), round((top.x() + target.width()) * ratio),
                              round((top.y() + target.height()) * ratio))
                self.thumbnails[identity].show(bounds)
            except OSError:
                if (failed := self.thumbnails.pop(identity, None)) is not None:
                    failed.close()

    def _abort(self) -> None:
        """Release partially built native state before the tray offers a retry."""
        self.closed = True
        for name in ("hotkeys", "appbar", "taskbar", "controller"):
            owner = getattr(self, name, None)
            if owner is not None:
                with contextlib.suppress(Exception):
                    owner.close()
        if hasattr(self, "native"):
            self.native.cancel.set()
        if hasattr(self, "work_queue"):
            with contextlib.suppress(queue.Empty):
                while True:
                    self.work_queue.get_nowait()
            self.work_queue.put_nowait(None)
        if hasattr(self, "worker") and self.worker.is_alive():
            self.worker.join(timeout=3)
        if hasattr(self, "keys_window") and self.keys_window is not None:
            self.keys_window.destroy()
        for name in ("launcher", "surface", "bar"):
            widget = getattr(self, name, None)
            if isinstance(widget, QtWidgets.QWidget):
                widget.hide()
                widget.deleteLater()
        if hasattr(self, "loop"):
            self.loop.close()

    def close(self) -> None:
        if self.closed:
            return
        self.save_positions()
        self.save_view()
        self.closed = True
        self.voice_active = False
        if self.timer is not None:
            cancel(self.timer)
        self.view_timer.stop()
        self.hide_previews()
        for thumbnail in self.thumbnails.values():
            thumbnail.close()
        self.thumbnails.clear()
        if self.hotkeys is not None:
            self.hotkeys.close()
        if self.keys_window is not None:
            self.keys_window.destroy()
        if self.appbar is not None:
            self.appbar.close()
        self.native.cancel.set()
        with contextlib.suppress(queue.Empty):
            while True:
                self.work_queue.get_nowait()
        self.work_queue.put_nowait(None)
        errors: list[str] = []
        try:
            self.taskbar.close()
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(f"Taskbar recovery shutdown: {error}")
        self.worker.join(timeout=3)
        if self.worker.is_alive():
            errors.append("The canvas resource worker did not finish before shutdown.")
        elif self._worker_error:
            errors.append(self._worker_error)
        try:
            self.controller.close()
        except OSError as failure:
            errors.append(str(failure))
        for dialog in tuple(self.dialogs):
            dialog.close()
        for widget in (self.launcher, self.surface, self.bar):
            widget.hide()
            widget.deleteLater()
        self.loop.close()
        if errors:
            raise OSError(" ".join(errors))
