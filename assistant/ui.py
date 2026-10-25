"""Qt owns the main thread; callers enqueue work or await explicit input.

No GUI objects cross the keyboard process. Domain work remains off the UI thread.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import queue
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, cast

from miryoku.layout import Layer, Scancode
from winapi import inputs, screen, windows
from winapi.shapes import Rect

from .agent_session import Session
from .agent_toasts import AgentToasts
from .agent_window import AgentWindow
from .browser_surface_operations import AppCancelled, AppFailed, AppOperation, AppResult
from .canvas_agent import WorkBackend
from .canvas_icons import icon
from .canvas_model import Request as CanvasRequest
from .canvas_ui import DesktopActions
from .canvas_web import DesktopWindow
from .canvas_widgets import STYLE, button, label
from .geometry import region
from .keymap import PHYSICAL_NAMES, Cap, Kind, keymap
from .meeting_panel import MeetingPanel
from .meeting_workflow import MeetingSnapshot, MeetingWorkflow
from .overlay import KeyHighlights, fit_position, load_position, nearest_work_area, save_position
from .qt import Qt, QtCore, QtGui, QtWidgets, shiboken6
from .qt_ui import application, cancel, later
from .recording_state import RecordingAction
from .theme import ACCENT, BACKGROUND, FOREGROUND, MUTED
from .widgets import Editor, Window, column, row, show_window
from .work_panel import WorkPanel
from .work_window import WorkCommand, WorkSnapshot, WorkWindow

__all__ = ["AgentWindow", "Ui"]

type Job = Callable[[QtWidgets.QApplication], None]


@dataclass(frozen=True, slots=True)
class MeetingConnection:
    workflow: MeetingWorkflow
    open_file: Callable[[Path], None]

def _bring_forward(window: QtWidgets.QWidget, focus: QtWidgets.QWidget) -> None:
    show_window(window, focus=True, target=focus)


def _near_pointer(window: QtWidgets.QWidget, width: int, height: int) -> None:
    point = QtGui.QCursor.pos()
    display = QtWidgets.QApplication.screenAt(point) or window.screen()
    area = display.availableGeometry()
    window.resize(width, height)
    window.move(max(area.left(), min(point.x() - width // 2, area.right() - width)),
                max(area.top(), min(point.y() - height // 3, area.bottom() - height)))


class Ui:
    __slots__ = (
        "_agent",
        "_agent_lock",
        "_agent_waiting",
        "_browser_closed",
        "_closed",
        "_desktop",
        "_dialogs",
        "_indicator",
        "_input_lock",
        "_inputs_closed",
        "_jobs",
        "_keyboard_overlay",
        "_keymap",
        "_meetings",
        "_pending_browser",
        "_pending_inputs",
        "_poll_timer",
        "_recording",
        "_root",
        "_thread",
        "_work",
        "_work_lock",
        "_work_view",
    )

    def __init__(
        self, *, on_agent_waiting: Callable[[bool], None] | None = None, on_recording: Callable[[RecordingAction], None] | None = None,
    ) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Qt must be created on the process's main thread.")
        self._agent: AgentWindow | None = None
        self._desktop: DesktopWindow | None = None
        self._agent_lock = threading.Lock()
        self._work: WorkWindow | None = None
        self._work_view: WorkPanel | None = None
        self._meetings: MeetingConnection | None = None
        self._work_lock = threading.Lock()
        self._agent_waiting = on_agent_waiting
        self._recording = on_recording
        self._indicator: Indicator | None = None
        """Made on first use, on the Qt thread."""
        self._keymap: KeymapWindow | None = None
        self._keyboard_overlay: KeymapOverlay | None = None
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._closed = threading.Event()
        self._input_lock = threading.Lock()
        self._inputs_closed = False
        self._browser_closed = False
        self._pending_browser: set[Callable[[], None]] = set()
        self._pending_inputs: set[Callable[[], None]] = set()
        self._dialogs: list[QtWidgets.QWidget] = []
        self._thread = threading.current_thread()
        self._root = application()
        self._root.setStyleSheet(STYLE)
        self._poll_timer = QtCore.QTimer(self._root)
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(lambda: self._poll(self._root))
        self._poll_timer.start()

    def configure_controls(
        self, *, on_agent_waiting: Callable[[bool], None], on_recording: Callable[[RecordingAction], None],
    ) -> None:
        """Connect the application before it creates any views."""
        self._agent_waiting, self._recording = on_agent_waiting, on_recording

    def run(self) -> None:
        """Run and dispose Qt on the main thread, including binding-owned deletions."""
        if threading.current_thread() is not self._thread:
            raise RuntimeError("Qt must run on the thread that created it.")
        try:
            self._root.exec()
        finally:
            try:
                self._close_inputs()
                try:
                    self._close_views()
                finally:
                    self._poll_timer.stop()
                    for window in self._root.topLevelWidgets():
                        window.hide()
                        window.deleteLater()
                    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
                    icon.cache_clear()
                    shiboken6.delete(self._root)
            finally:
                self._closed.set()

    def _close_views(self) -> None:
        try:
            self.close_desktop()
        finally:
            self.close_agent()
            self.close_work()
            self._work_view = None
            while ((self._agent is not None and not self._agent.closed_ready.is_set())
                   or (self._work is not None and not self._work.closed_ready.is_set())):
                self._poll(self._root)

    def _close_inputs(self) -> None:
        with self._input_lock:
            self._inputs_closed = True
            pending = tuple(self._pending_inputs)
        for cancel_input in pending:
            cancel_input()
        self._close_browser_apps()

    def _close_browser_apps(self) -> None:
        with self._input_lock:
            self._browser_closed = True
            pending = tuple(self._pending_browser)
        for cancel_request in pending:
            cancel_request()

    def _poll(self, root: QtWidgets.QApplication) -> None:
        for _ in range(64):
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            try:
                job(root)
            except Exception:
                traceback.print_exc()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._close_inputs()
        self.post(lambda root: root.quit())
        if threading.current_thread() is not self._thread and not self._closed.wait(timeout=5):
            raise OSError("The Qt UI did not finish closing.")

    def post(self, job: Job) -> None:
        """Runs a job on the Qt thread, soon; doesn't wait."""
        if not self._closed.is_set():
            self._jobs.put(job)

    def _ask[T](
        self, build: Callable[[QtWidgets.QApplication, Callable[[T], None]], None], *, on_close: Callable[[], T] | None = None,
    ) -> T:
        """Builds a window on the Qt thread and waits for the answer it gives."""
        answers: queue.Queue[tuple[T | None, Exception | None]] = queue.Queue()

        def finish(value: T | None, error: Exception | None) -> None:
            with self._input_lock:
                if cancel_input not in self._pending_inputs:
                    return
                self._pending_inputs.remove(cancel_input)
            answers.put((value, error))

        def cancel_input() -> None:
            if on_close is not None:
                finish(on_close(), None)
            else:
                finish(None, OSError("The Qt UI is closed."))

        def job(root: QtWidgets.QApplication) -> None:
            with self._input_lock:
                if cancel_input not in self._pending_inputs:
                    return
            try:
                build(root, lambda value: finish(value, None))
            except Exception as error:
                finish(None, error)

        with self._input_lock:
            if self._inputs_closed:
                if on_close is not None:
                    return on_close()
                raise OSError("The Qt UI is closed.")
            self._pending_inputs.add(cancel_input)
        self.post(job)
        try:
            answer, error = answers.get()
        finally:
            with self._input_lock:
                self._pending_inputs.discard(cancel_input)
        if error is not None:
            raise error
        return cast("T", answer)

    # ---- a text box --------------------------------------------------------------------

    def prompt(self, title: str) -> str | None:
        def build(root: QtWidgets.QApplication, done: Callable[[str | None], None]) -> None:
            dialog = QtWidgets.QInputDialog()
            dialog.setWindowTitle("Toolkit")
            dialog.setLabelText(title)
            dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
            dialog.setStyleSheet(STYLE)
            self._dialogs.append(dialog)
            def finish(result: int) -> None:
                done(dialog.textValue().strip() if result == QtWidgets.QDialog.DialogCode.Accepted else None)
                self._dialogs.remove(dialog)
                dialog.deleteLater()
            dialog.finished.connect(finish)
            _near_pointer(dialog, 640, 130)
            dialog.open()
            _bring_forward(dialog, dialog)
        return self._ask(build, on_close=lambda: None)

    def choose_region(self, screen: Rect) -> Rect | None:
        def build(root: QtWidgets.QApplication, done: Callable[[Rect | None], None]) -> None:
            picker = RegionPicker(screen, lambda value: later(root, 150, lambda: done(value)))
            self._dialogs.append(picker)
            picker.destroyed.connect(lambda: self._dialogs.remove(picker))
            show_window(picker, focus=True)
        return self._ask(build, on_close=lambda: None)

    # ---- an answer, with follow-ups ----------------------------------------------------

    def answer(self, title: str, follow_up: Callable[[str], None]) -> AnswerWindow:
        def build(root: QtWidgets.QApplication, done: Callable[[AnswerWindow], None]) -> None:
            done(AnswerWindow(self, root, title, follow_up))

        return self._ask(build)

    # ---- the key map and the layer indicator ------------------------------------------

    def show_keymap(self) -> None:
        def job(root: QtWidgets.QApplication) -> None:
            if self._keymap is None or not shiboken6.isValid(self._keymap.window):
                self._keymap = KeymapWindow(root)
            else:
                _bring_forward(self._keymap.window, self._keymap.canvas)

        self.post(job)

    def show_keyboard_overlay(
        self, layer: Layer | None, title: str, work: Rect, *, visible: bool, pressed: frozenset[Scancode] = frozenset(),
    ) -> None:
        """A faded key map that follows the current layer without taking keyboard focus."""
        def job(root: QtWidgets.QApplication) -> None:
            if self._keyboard_overlay is None:
                if not visible:
                    return
                self._keyboard_overlay = KeymapOverlay(root)
            self._keyboard_overlay.show(layer, title, work, visible=visible, pressed=pressed)

        self.post(job)

    def show_pressed_keys(self, pressed: frozenset[Scancode], *, reset: bool = False) -> None:
        """Update feedback on the Qt thread without opening a hidden overlay."""
        def job(root: QtWidgets.QApplication) -> None:
            if self._keyboard_overlay is not None:
                self._keyboard_overlay.set_pressed(pressed, reset=reset)

        self.post(job)

    def _overlay(self, root: QtWidgets.QApplication) -> Indicator:
        if self._indicator is None:
            self._indicator = Indicator(root)
        return self._indicator

    def indicate(self, text: str | None, work: Rect) -> None:
        """What the layer indicator shows from now on (None: nothing), at the bottom of `work`."""
        self.post(lambda root: self._overlay(root).set(text, work))
        self.post(lambda _: self._desktop.layer(text) if self._desktop is not None else None)

    def flash(self, text: str, work: Rect, seconds: float = 1.2) -> None:
        """Shows a message in the indicator for a moment."""
        self.post(lambda root: self._overlay(root).flash(text, work, seconds))

    def review(self, summary: str, diff: str) -> bool:
        """Shows a change to the toolkit; True if the user chooses Install."""

        def build(root: QtWidgets.QApplication, done: Callable[[bool], None]) -> None:
            ReviewWindow(root, summary, diff, done)

        return self._ask(build)

    def agent(
        self, task: str | None, stop: Callable[[], None], *, on_problem: Callable[[str], None] | None = None,
        on_reset: Callable[[], bool] | None = None, on_submit: Callable[[str], None] | None = None,
        on_work: Callable[[], None] | None = None,
        focus: bool = False, reveal: bool = True, queued: bool = False,
    ) -> AgentWindow:
        with self._agent_lock:
            if self._agent is None:
                self._agent = AgentWindow(
                    stop, post=lambda job: self.post(lambda root: job()), create_view=lambda agent: AgentToasts(self._root, agent),
                    on_problem=on_problem, on_reset=on_reset, on_submit=on_submit, on_waiting=self._agent_waiting, on_recording=self._recording,
                    on_work=on_work,
                )
            panel = self._agent
        if task is not None:
            panel.begin(task, stop, queued=queued)
        if reveal:
            panel.show(focus=focus)
        return panel

    def agent_input(
        self, title: str, detail: str = "", *, question: bool = False, cancel: threading.Event | None = None,
    ) -> str | None:
        """Wait on the agent's inline request, without displaying or focusing a window."""
        if self._agent is None:
            return None
        return self._agent.input(title, detail, question=question, cancel=cancel)

    def close_agent(self) -> None:
        if self._agent is not None:
            self._agent.close()
            if self._closed.is_set():
                self._agent.closed_ready.set()
            elif threading.current_thread() is not self._thread:
                self._agent.closed_ready.wait(timeout=3)

    def work(
        self, on_command: Callable[[WorkCommand], None], *, on_problem: Callable[[str], None] | None = None,
        focus: bool = True, reveal: bool = True,
    ) -> WorkWindow:
        with self._work_lock:
            if self._work is None:
                self._work = WorkWindow(
                    on_command, post=lambda job: self.post(lambda root: job()),
                    create_view=self._create_work, on_problem=on_problem,
                )
            work = self._work
        if reveal:
            work.show(focus=focus)
        return work

    def _create_work(self, work: WorkWindow) -> WorkPanel:
        self._work_view = WorkPanel(self._root, work)
        self._attach_meetings()
        return self._work_view

    def _attach_meetings(self) -> None:
        connection, view = self._meetings, self._work_view
        if connection is not None and view is not None and view.meetings is None:
            view.attach_meetings(MeetingPanel(connection.workflow, open_file=connection.open_file, add_to_canvas=self.add_canvas_document))

    def configure_meetings(self, workflow: MeetingWorkflow, open_file: Callable[[Path], None]) -> None:
        def configure(_: QtWidgets.QApplication) -> None:
            self._meetings = MeetingConnection(workflow, open_file)
            self._attach_meetings()
            if self._desktop is not None:
                self._desktop.attach_meetings(workflow)
        self.post(configure)

    def meetings_changed(self, snapshot: MeetingSnapshot) -> None:
        def render(_: QtWidgets.QApplication) -> None:
            if self._work_view is not None and self._work_view.meetings is not None:
                self._work_view.meetings.render_snapshot(snapshot)
            if self._desktop is not None:
                self._desktop.meetings_changed(snapshot)
        self.post(render)

    def show_meetings(self) -> None:
        def reveal(_: QtWidgets.QApplication) -> None:
            if self._work_view is not None:
                self._work_view.show_meetings()
        self.post(reveal)

    def add_canvas_document(self, path: Path) -> None:
        def add(_: QtWidgets.QApplication) -> None:
            if self._desktop is not None:
                self._desktop.add_files((str(path),))
            elif self._agent is not None:
                self._agent.problem("The desktop canvas is disabled. Open the saved notes from Meetings instead.")
        self.post(add)

    def close_work(self) -> None:
        if self._work is not None:
            self._work.close()
            if self._closed.is_set():
                self._work.closed_ready.set()
            elif threading.current_thread() is not self._thread:
                self._work.closed_ready.wait(timeout=3)

    def desktop(
        self, submit: Callable[[CanvasRequest], None], command: Callable[[WorkCommand], None], snapshot: Callable[[], WorkSnapshot], *,
        watch: Callable[[tuple[str, ...]], None],
        show_agent: Callable[[], None], show_work: Callable[[], None], stop: Callable[[], None], overlay: Callable[[], None],
        on_problem: Callable[[str], None],
    ) -> None:
        """Start the native shell without changing focus or the keyboard process."""
        def session() -> Session | None:
            agent = self._agent
            if agent is None:
                return None
            with agent.lock:
                return agent.state

        def create(root: QtWidgets.QApplication) -> None:
            with self._input_lock:
                if self._inputs_closed:
                    return
            if self._desktop is not None:
                return
            try:
                backend = WorkBackend(submit, command, snapshot, session, watch=watch)
                actions = DesktopActions(show_agent, show_work, stop, self._recording or (lambda _: None), self.show_keymap, overlay, backend.poll)
                self._desktop = DesktopWindow(root, backend, actions, work_snapshot=backend.work_snapshot)
                if self._meetings is not None:
                    self._desktop.attach_meetings(self._meetings.workflow)
                with self._input_lock:
                    if not self._inputs_closed:
                        self._browser_closed = False
            except Exception:
                traceback.print_exc()
                on_problem("The desktop canvas could not start. Details are in the assistant log; the tray remains available.")
        self.post(create)

    def show_desktop(self, *, launcher: bool = False) -> None:
        def reveal(_: QtWidgets.QApplication) -> None:
            if self._desktop is not None:
                if launcher:
                    self._desktop.show_launcher()
                else:
                    self._desktop.show()
        self.post(reveal)

    def browser_app(self, operation: AppOperation, cancel: threading.Event, finished: Callable[[AppResult], None]) -> None:
        """Queue an owned app operation; completion never waits for Qt or performs observer I/O."""
        def finish(result: AppResult) -> None:
            with self._input_lock:
                if cancel_request not in self._pending_browser:
                    return
                self._pending_browser.remove(cancel_request)
            try:
                finished(result)
            except Exception:
                traceback.print_exc()

        def cancel_request() -> None:
            cancel.set()
            finish(AppFailed("Browser apps are closing; the operation's outcome could not be confirmed."))

        def dispatch(_: QtWidgets.QApplication) -> None:
            with self._input_lock:
                if cancel_request not in self._pending_browser:
                    return
                closing = self._inputs_closed or self._browser_closed
            if cancel.is_set():
                finish(AppCancelled.REQUEST)
                return
            if closing:
                cancel_request()
                return
            desktop = self._desktop
            if desktop is None or desktop.closed:
                finish(AppFailed("Browser apps are unavailable."))
                return
            try:
                if cancel.is_set():
                    finish(AppCancelled.REQUEST)
                    return
                desktop.browser_request(operation, cancel, finish)
            except Exception:
                traceback.print_exc()
                finish(AppFailed("The browser app operation failed; its outcome could not be confirmed."))

        with self._input_lock:
            self._pending_browser.add(cancel_request)
            closing = self._inputs_closed or self._browser_closed
        if cancel.is_set():
            finish(AppCancelled.REQUEST)
        elif closing:
            cancel_request()
        else:
            self.post(dispatch)

    def submit_canvas_voice(self, text: str, fallback: Callable[[str], None]) -> bool:
        """Only immutable owner-published availability is read off the UI thread."""
        desktop = self._desktop
        if desktop is None or not desktop.voice_active:
            return False
        def route(_: QtWidgets.QApplication) -> None:
            agent = self._agent
            reply: Callable[[str], bool] = agent.reply_by_voice if agent is not None else (lambda _: False)
            if self._desktop is not desktop or desktop.closed:
                fallback(text)
                return
            desktop.controller.voice(text, reply=reply, active=desktop.active_context(), fallback=fallback)
        self.post(route)
        return True

    def close_desktop(self) -> None:
        self._close_browser_apps()
        if self._closed.is_set():
            return
        done = threading.Event()
        problems: list[str] = []
        def close(_: QtWidgets.QApplication) -> None:
            try:
                if self._desktop is not None:
                    self._desktop.close()
                    self._desktop = None
            except Exception as error:
                problems.append(str(error))
            finally:
                done.set()
        if threading.current_thread() is self._thread:
            close(self._root)
        else:
            self.post(close)
            if not done.wait(timeout=10):
                raise OSError("The desktop canvas did not finish closing.")
        if problems:
            raise OSError("; ".join(problems))


class RegionPicker(QtWidgets.QWidget):
    def __init__(self, area: Rect, done: Callable[[Rect | None], object]) -> None:
        super().__init__()
        self.area, self.done = area, done
        self.start: QtCore.QPointF | None = None
        self.current: QtCore.QPointF | None = None
        self.finished = False
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        windows.move(int(self.winId()), area)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 76))
        if self.start is not None and self.current is not None:
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
            painter.drawRect(QtCore.QRectF(self.start, self.current).normalized())

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.finish(None)
        else:
            self.start = self.current = event.position()
            self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self.current = event.position()
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self.start is not None and event.button() == Qt.MouseButton.LeftButton:
            scale = self.devicePixelRatioF()
            first = (self.area.left + round(self.start.x() * scale), self.area.top + round(self.start.y() * scale))
            last = (self.area.left + round(event.position().x() * scale), self.area.top + round(event.position().y() * scale))
            area = region(first, last)
            self.finish(area if area.width > 4 and area.height > 4 else None)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.finish(None)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.finish(None)
        event.accept()

    def finish(self, area: Rect | None) -> None:
        if not self.finished:
            self.finished = True
            self.hide()
            self.done(area)
            self.deleteLater()


class AnswerWindow:
    __slots__ = (
        "__weakref__",
        "entry",
        "follow_up",
        "text",
        "ui",
        "window",
    )

    def __init__(self, ui: Ui, root: QtWidgets.QApplication, title: str, follow_up: Callable[[str], None]) -> None:
        self.ui, self.follow_up = ui, follow_up
        self.window = Window(title[:80], self.close, width=760, height=540)
        layout = column(self.window)
        layout.setContentsMargins(20, 18, 20, 18)
        self.text = Editor(readonly=True)
        layout.addWidget(self.text, 1)
        bottom = row()
        self.entry = QtWidgets.QLineEdit()
        self.entry.setPlaceholderText("Ask a follow-up…")
        self.entry.setEnabled(False)
        self.entry.returnPressed.connect(self.ask)
        bottom.addWidget(self.entry, 1)
        bottom.addWidget(button("Copy answer", self.copy_all))
        layout.addLayout(bottom)
        self.add(f"{title}\n\n", "accent")
        _near_pointer(self.window, 760, 540)
        _bring_forward(self.window, self.text)

    def close(self) -> None:
        self.window.hide()
        self.window.deleteLater()

    def add(self, text: str, tag: str = "") -> None:
        def job(_: QtWidgets.QApplication) -> None:
            if shiboken6.isValid(self.window):
                cursor = self.text.textCursor()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                cursor.insertText(text)
                self.text.setTextCursor(cursor)
                self.text.ensureCursorVisible()
        self.ui.post(job)

    def all_text(self) -> str:
        return self.text.toPlainText()

    def copy_all(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.all_text())

    def ask(self) -> None:
        if question := self.entry.text().strip():
            self.entry.clear()
            self.entry.setEnabled(False)
            self.add(f"\n\n{question}\n\n", "accent")
            self.follow_up(question)

    def write(self, text: str) -> None:
        self.add(text)

    def finish(self) -> None:
        def job(_: QtWidgets.QApplication) -> None:
            if shiboken6.isValid(self.window):
                self.entry.setEnabled(True)
                self.entry.setFocus()
        self.ui.post(job)

    def fail(self, message: str) -> None:
        self.add(f"\n{message}\n", "error")
        self.finish()


class Indicator:
    __slots__ = (
        "__weakref__",
        "_flash",
        "_frame",
        "_label",
        "_steady",
        "_window",
    )

    def __init__(self, root: QtWidgets.QApplication) -> None:
        self._window = QtWidgets.QWidget()
        self._window.setObjectName("toolkitWindow")
        self._window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self._window.setStyleSheet(STYLE)
        self._window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._window.setWindowOpacity(.9)
        layout = row(self._window)
        layout.setContentsMargins(16, 6, 16, 6)
        self._label = label("")
        layout.addWidget(self._label)
        self._frame = int(self._window.winId())
        windows.make_overlay(self._frame)
        self._steady: tuple[str | None, Rect] | None = None
        self._flash: QtCore.QTimer | None = None

    def _show(self, text: str | None, work: Rect, colour: str = FOREGROUND) -> None:
        if text is None:
            self._window.hide()
            return
        self._label.setText(text)
        self._label.setStyleSheet(f"color: {colour}; font: 15px 'Segoe UI';")
        self._window.adjustSize()
        scale = self._window.devicePixelRatioF()
        width, height = round(self._window.width() * scale), round(self._window.height() * scale)
        x, y = work.left + (work.width - width) // 2, work.bottom - height - 28
        windows.move(self._frame, Rect(x, y, x + width, y + height))
        self._window.show()
        windows.show_quietly(self._frame, visible=True)

    def set(self, text: str | None, work: Rect) -> None:
        self._steady = (text, work)
        if self._flash is None:
            self._show(text, work)

    def flash(self, text: str, work: Rect, seconds: float) -> None:
        cancel(self._flash)
        self._show(text, work, ACCENT)
        self._flash = later(self._window, round(seconds * 1000), self._end_flash)

    def _end_flash(self) -> None:
        self._flash = None
        if self._steady is not None:
            self._show(*self._steady)
        else:
            self._window.hide()
_COLOURS: Final = {
    Kind.KEY: ("#2b2e33", FOREGROUND),
    Kind.MOD: ("#21403a", "#8fe3c6"),
    Kind.LAYER: ("#22324d", "#9cc3ff"),
    Kind.TOOL: ("#47381c", "#ffcf7a"),
    Kind.APP: ("#2c3f23", "#b6e39a"),
    Kind.SPECIAL: ("#3b2948", "#d7b4ff"),
    Kind.HELD: (ACCENT, BACKGROUND),
    Kind.BLANK: ("#191b1e", MUTED),
}
_LAYERS: Final[tuple[tuple[str, Layer | None], ...]] = (("Base", None), *((layer.name.title(), layer) for layer in Layer))
_ROW_START: Final = (0.0, 1.5, 0.0, 1.25)
"""Where each row starts, in key widths, as on an ISO keyboard."""
_UNIT, _GAP, _HEIGHT = 64, 5, 56


class KeymapCanvas(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.caps: dict[str, Cap] = {}
        self.boxes: dict[str, QtCore.QRectF] = {}
        self.highlighted: frozenset[str] = frozenset()
        self.scale = 1.0
        self.setAccessibleName("Physical keyboard and current layer")

    def draw(self, layer: Layer | None, scale: float = 1.0) -> None:
        self.scale = scale
        self.caps.clear()
        self.boxes.clear()
        for row_number, (caps, start) in enumerate(zip(keymap(layer), _ROW_START, strict=True)):
            x, y = start * _UNIT * scale, row_number * (_HEIGHT + _GAP) * scale
            for cap in caps:
                size = 1.75 if cap.physical == "Caps" else 1.0
                self.caps[cap.physical] = cap
                self.boxes[cap.physical] = QtCore.QRectF(x, y, (size * _UNIT - _GAP) * scale, _HEIGHT * scale)
                x += size * _UNIT * scale
        self.setFixedSize(round(14 * _UNIT * scale), round(4 * (_HEIGHT + _GAP) * scale))
        self.update()

    def colors(self, name: str) -> tuple[str, str]:
        return ("#ffcf7a", BACKGROUND) if name in self.highlighted else _COLOURS[self.caps[name].kind]

    def press(self, names: frozenset[str]) -> None:
        changed = names ^ self.highlighted
        self.highlighted = names
        for name in changed:
            if name in self.boxes:
                self.update(self.boxes[name].toAlignedRect())

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        for name, cap in self.caps.items():
            bounds = self.boxes[name]
            fill, ink = self.colors(name)
            painter.setPen(QtGui.QPen(QtGui.QColor("#fff0cc" if name in self.highlighted else "#424b58"), 1))
            painter.setBrush(QtGui.QColor(fill))
            painter.drawRoundedRect(bounds.adjusted(.5, .5, -.5, -.5), 4, 4)
            face = QtGui.QFont("Segoe UI")
            face.setPixelSize(max(8, round(10 * self.scale)))
            painter.setFont(face)
            painter.setPen(QtGui.QColor(ink if name in self.highlighted else MUTED))
            painter.drawText(bounds.adjusted(5 * self.scale, 3 * self.scale, -3, -3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, name)
            face.setPixelSize(max(8, round((15 if len(cap.label) <= 5 else 11) * self.scale)))
            face.setWeight(QtGui.QFont.Weight.DemiBold)
            painter.setFont(face)
            painter.setPen(QtGui.QColor(ink))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, cap.label)
            face.setPixelSize(max(8, round(10 * self.scale)))
            face.setWeight(QtGui.QFont.Weight.Normal)
            painter.setFont(face)
            painter.drawText(bounds.adjusted(0, 0, 0, -3 * self.scale), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, cap.hold)


class OverlayHandle(QtWidgets.QLabel):
    def __init__(self, start: Callable[[QtCore.QPoint], None], move: Callable[[QtCore.QPoint], None], end: Callable[[], None]) -> None:
        super().__init__("≡  Drag to move")
        self.start, self.move_drag, self.end = start, move, end
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: #26313d; color: #cbd5e1; padding: 6px 12px; border-radius: 5px; font: 11px 'Segoe UI';")
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.adjustSize()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.start(QtCore.QPoint(*inputs.cursor_position()))
            self.grabMouse()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        # The saved overlay position and native moves use physical pixels.
        self.move_drag(QtCore.QPoint(*inputs.cursor_position()))

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self.releaseMouse()
        self.end()


class KeymapOverlay:
    __slots__ = (
        "__weakref__",
        "_canvas",
        "_drag",
        "_frame",
        "_handle",
        "_handle_frame",
        "_heading",
        "_highlight_timer",
        "_highlights",
        "_position",
        "_view",
        "_window",
    )

    def __init__(self, root: QtWidgets.QApplication) -> None:
        self._window = QtWidgets.QWidget()
        self._window.setObjectName("toolkitWindow")
        self._window.setWindowTitle("Toolkit keyboard overlay")
        self._window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self._window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._window.setStyleSheet(STYLE)
        self._window.setWindowOpacity(.55)
        layout = column(self._window)
        layout.setContentsMargins(8, 8, 8, 8)
        self._heading = label("", "muted")
        layout.addWidget(self._heading)
        self._canvas = KeymapCanvas()
        layout.addWidget(self._canvas)
        self._frame = int(self._window.winId())
        windows.make_overlay(self._frame)
        self._handle = OverlayHandle(self._start_drag, self._drag_to, self._finish_drag)
        self._handle.setWindowTitle("Toolkit keyboard overlay handle")
        self._handle_frame = int(self._handle.winId())
        windows.make_overlay(self._handle_frame, click_through=False)
        self._position = load_position()
        self._drag: QtCore.QPoint | None = None
        self._view: tuple[Layer | None, str, tuple[Rect, ...]] | None = None
        self._highlights = KeyHighlights()
        self._highlight_timer: QtCore.QTimer | None = None

    def show(self, layer: Layer | None, title: str, work: Rect, *, visible: bool, pressed: frozenset[Scancode] = frozenset()) -> None:
        if not visible:
            self._finish_drag()
            self.set_pressed(frozenset(), reset=True)
            self._handle.hide()
            self._window.hide()
            self._view = None
            return
        areas = tuple(m.work for m in screen.monitors()) or (work,)
        view = (layer, title, areas)
        if view == self._view:
            self.set_pressed(pressed)
            return
        if self._position is not None:
            work = nearest_work_area(self._position, areas)
        density = self._window.devicePixelRatioF()
        scale = max(.2, min(.85, (work.width / density - 32) / (14 * _UNIT), (work.height / density - 180) / (4 * (_HEIGHT + _GAP))))
        self._heading.setText(f"Toolkit keyboard · {title}")
        self._canvas.draw(layer, scale)
        self._view = view
        self.set_pressed(pressed)
        self._window.adjustSize()
        if self._position is None:
            self._position = (work.left + work.width // 2, work.bottom - round((self._window.height() + self._handle.height() + 90) * density))
        self._move(self._position)
        self._window.show()
        self._handle.show()
        windows.show_quietly(self._frame, visible=True)
        windows.show_quietly(self._handle_frame, visible=True)

    def set_pressed(self, pressed: frozenset[Scancode], *, reset: bool = False) -> None:
        if reset:
            self._highlights.clear()
        if self._view is not None:
            self._highlights.update(pressed, time.monotonic())
            self._paint_highlights()

    def _paint_highlights(self) -> None:
        cancel(self._highlight_timer)
        self._highlight_timer = None
        now = time.monotonic()
        self._canvas.press(frozenset(PHYSICAL_NAMES[key] for key in self._highlights.visible(now)))
        if (delay := self._highlights.delay(now)) is not None:
            self._highlight_timer = later(self._window, delay, self._paint_highlights)

    def _move(self, position: tuple[int, int]) -> None:
        self._position = position
        x, y = position
        scale = self._window.devicePixelRatioF()
        width, height = round(self._window.width() * scale), round(self._window.height() * scale)
        handle_width, handle_height = round(self._handle.width() * scale), round(self._handle.height() * scale)
        left, top = x - width // 2, y + handle_height + 4
        windows.move(self._frame, Rect(left, top, left + width, top + height))
        left = x - handle_width // 2
        windows.move(self._handle_frame, Rect(left, y, left + handle_width, y + handle_height))

    def _start_drag(self, point: QtCore.QPoint) -> None:
        if self._position is not None and self._view is not None:
            self._drag = point - QtCore.QPoint(*self._position)

    def _drag_to(self, point: QtCore.QPoint) -> None:
        if self._drag is not None:
            value = point - self._drag
            self._move((value.x(), value.y()))

    def _finish_drag(self) -> None:
        if self._drag is None:
            return
        self._drag = None
        if self._position is not None:
            areas = tuple(m.work for m in screen.monitors())
            if areas:
                scale = self._window.devicePixelRatioF()
                self._move(fit_position(self._position, round(self._window.width() * scale),
                                        round((self._window.height() + self._handle.height() + 4) * scale), nearest_work_area(self._position, areas)))
            try:
                save_position(self._position)
            except OSError:
                traceback.print_exc()


class KeymapWindow:
    __slots__ = (
        "__weakref__",
        "buttons",
        "canvas",
        "layer",
        "shortcuts",
        "window",
    )

    def __init__(self, root: QtWidgets.QApplication) -> None:
        self.window = Window("Toolkit key map", self.close, width=940, height=390)
        layout = column(self.window)
        layout.setContentsMargins(20, 16, 20, 16)
        buttons = row()
        self.buttons: list[QtWidgets.QToolButton] = []
        self.shortcuts: list[QtGui.QShortcut] = []
        for index, (name, layer) in enumerate(_LAYERS):
            choose = partial(self.draw, layer)
            key = button(name, choose)
            key.setCheckable(True)
            buttons.addWidget(key)
            self.buttons.append(key)
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(str(index + 1)), self.window)
            shortcut.activated.connect(choose)
            self.shortcuts.append(shortcut)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.canvas = KeymapCanvas()
        layout.addWidget(self.canvas)
        layout.addWidget(label(
            "Hold a thumb for its layer · § toggles the layout · B dictates · N speaks to the agent · Keys 1-6 switch layer", "muted",
        ))
        self.layer: Layer | None = None
        self.draw(None)
        _near_pointer(self.window, 940, 390)
        _bring_forward(self.window, self.canvas)

    def draw(self, layer: Layer | None) -> None:
        self.layer = layer
        self.canvas.draw(layer)
        for key, (_, shown) in zip(self.buttons, _LAYERS, strict=True):
            key.setChecked(shown is layer)

    def close(self) -> None:
        self.window.hide()
        self.window.deleteLater()


class ReviewWindow:
    __slots__ = (
        "__weakref__",
        "done",
        "finished",
        "window",
    )

    def __init__(self, root: QtWidgets.QApplication, summary: str, diff: str, done: Callable[[bool], None]) -> None:
        self.done, self.finished = done, False
        self.window = Window("Change the toolkit?", lambda: self.finish(False), width=900, height=640)
        layout = column(self.window)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(label(summary, "title"))
        note = label("Run this code and its tests with your Windows account, then install it if the checks pass.", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        text = Editor(readonly=True)
        text.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        text.setPlainText(diff)
        text.setStyleSheet("font-family: Consolas; font-size: 12px;")
        layout.addWidget(text, 1)
        controls = row()
        controls.addStretch()
        controls.addWidget(button("Cancel", lambda: self.finish(False)))
        controls.addWidget(button("Run checks and install", lambda: self.finish(True), name="primary"))
        layout.addLayout(controls)
        _near_pointer(self.window, 900, 640)
        _bring_forward(self.window, text)

    def finish(self, install: bool) -> None:
        if not self.finished:
            self.finished = True
            self.window.hide()
            self.window.deleteLater()
            self.done(install)
