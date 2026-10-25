"""The conversation history and composer, rendered entirely by Qt."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import time
import traceback
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import assert_never

from winapi import screen, windows
from winapi.shapes import Rect

from .agent_session import Approval, ApprovalControls, Message, ResponseText, Snapshot, pending, presentation
from .agent_window import AgentWindow, log_view
from .canvas_widgets import STYLE, button, label
from .overlay import nearest_work_area
from .panel import Edge, PanelPreferences, dock_panel, fit_panel, load_preferences, save_preferences, snap_panel
from .qt import Qt, QtCore, QtGui, QtWidgets
from .qt_text import Document, units
from .recording_state import RecordingAction, RecordingState, duration, phase_label, source_name
from .rich_text import Span, formatted
from .widgets import Browser, Editor, GestureHandle, Window, column, row, show_window


@dataclass(frozen=True, slots=True)
class TextAnchor:
    message: int
    offset: int


@dataclass(frozen=True, slots=True)
class Selection:
    start: TextAnchor
    end: TextAnchor
    text: str


@dataclass(frozen=True, slots=True)
class MovePanel:
    start: tuple[int, int]
    bounds: Rect
    edge: Edge | None


@dataclass(frozen=True, slots=True)
class ResizePanel:
    start: tuple[int, int]
    bounds: Rect
    corner: bool


type Gesture = MovePanel | ResizePanel


class AgentPanel:
    """History updates preserve selection, scroll and the user's active draft."""
    __slots__ = (
        "__weakref__",
        "activity_text",
        "agent",
        "approval_bar",
        "approval_layout",
        "compose_action",
        "composing",
        "controls",
        "corner",
        "details",
        "document",
        "drag_handle",
        "floating_height",
        "gesture",
        "handle",
        "history_painted",
        "identity",
        "input",
        "items",
        "latest",
        "menu",
        "microphone_action",
        "new_action",
        "on_hide",
        "options",
        "pinned",
        "preferences",
        "question",
        "queue",
        "queued",
        "recording_bar",
        "recording_button",
        "recording_label",
        "recording_menu",
        "recording_state",
        "remember_enabled",
        "reply",
        "resize_handle",
        "scroll",
        "send",
        "show_activity",
        "starts",
        "status",
        "stop",
        "stopping",
        "text",
        "timer",
        "translucent",
        "window",
        "write",
    )


    def __init__(  # noqa: PLR0915  # A single retained native view.
        self, root: QtWidgets.QApplication, agent: AgentWindow, *, preferences: PanelPreferences | None = None,
        remember: bool = True, on_hide: Callable[[], None] | None = None,
    ) -> None:
        self.agent, self.on_hide = agent, on_hide
        self.preferences = preferences if preferences is not None else load_preferences()
        self.remember_enabled = remember
        self.identity = ""
        self.items: list[Message] = []
        self.starts: list[int] = []
        self.controls: dict[int, QtWidgets.QWidget] = {}
        self.queued: tuple[str, ...] = ()
        self.stopping = self.composing = False
        self.question = ""
        self.recording_state = RecordingState()
        self.history_painted = False
        self.gesture: Gesture | None = None
        self.floating_height = 720
        self.window = Window("Toolkit · Agent timeline", self.hide, width=520, height=740)
        self.window.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.window.escape.activated.disconnect()
        self.window.escape.activated.connect(self.escape)
        self.window.setMinimumSize(340, 360)
        self.handle = int(self.window.winId())
        layout = column(self.window)
        layout.setContentsMargins(20, 16, 20, 16)
        self.drag_handle = GestureHandle(self.window, lambda event: self.start_drag(self.pointer(event)),
                                        lambda event: self.drag_to(self.pointer(event)), lambda event: self.end_drag(self.pointer(event)))
        self.drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        header = row(self.drag_handle)
        title = label("Conversation", "title")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(title)
        header.addStretch()
        self.status = label("", "muted")
        header.addWidget(self.status)
        self.queue = label("", "muted")
        header.addWidget(self.queue)
        self.stop = button("Stop", self.cancel)
        header.addWidget(self.stop)
        self.options = button("···", self.open_menu, tip="Conversation options")
        header.addWidget(self.options)
        layout.addWidget(self.drag_handle)
        self.menu = QtWidgets.QMenu(self.window)
        self.new_action = self.menu.addAction("New chat", self.new)
        self.compose_action = self.menu.addAction("Write a message", self.compose)
        self.pinned = self.menu.addAction("Keep on top")
        self.pinned.setCheckable(True)
        self.pinned.setChecked(self.preferences.pinned)
        self.pinned.triggered.connect(self.pin)
        self.translucent = self.menu.addAction("Translucent background")
        self.translucent.setCheckable(True)
        self.translucent.setChecked(self.preferences.translucent)
        self.translucent.triggered.connect(self.set_transparency)
        self.show_activity = self.menu.addAction("Show activity")
        self.show_activity.setCheckable(True)
        self.show_activity.triggered.connect(self.activity)
        self.menu.addSeparator()
        self.recording_menu = self.menu.addAction("Record a meeting", self.toggle_recording)
        self.microphone_action = self.menu.addAction("Record microphone only", lambda: self.recording_action("start_microphone"))
        self.menu.addAction("Transcripts", lambda: self.recording_action("folder"))
        if self.agent.on_work is not None:
            self.menu.addAction("Work", self.agent.show_work)
        self.menu.addSeparator()
        self.menu.addAction("Hide", self.hide)
        self.recording_bar = QtWidgets.QWidget()
        recording = row(self.recording_bar)
        self.recording_label = button("", lambda: self.recording_action("open"))
        recording.addWidget(self.recording_label, 1)
        self.recording_button = button("Stop", self.recording_control)
        recording.addWidget(self.recording_button)
        layout.addWidget(self.recording_bar)
        self.text = Browser()
        self.text.painted.connect(self.history_paint)
        self.text.setAccessibleName("Conversation history")
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self.open_link)
        self.document = Document(self.text)
        self.text.setDocument(self.document.qt)
        self.text.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0; }")
        self.scroll = self.text.verticalScrollBar()
        self.scroll.valueChanged.connect(self.scrolled)
        layout.addWidget(self.text, 1)
        self.approval_bar = QtWidgets.QWidget()
        self.approval_layout = column(self.approval_bar)
        layout.addWidget(self.approval_bar)
        self.latest = button("Latest ↓", self.see_latest)
        layout.addWidget(self.latest, 0, Qt.AlignmentFlag.AlignRight)
        self.latest.hide()
        self.details = QtWidgets.QWidget()
        activity_layout = column(self.details)
        activity_layout.addWidget(label("Activity", "muted"))
        self.activity_text = Editor(readonly=True)
        self.activity_text.setMaximumHeight(160)
        activity_layout.addWidget(self.activity_text)
        layout.addWidget(self.details)
        self.details.hide()
        self.reply = QtWidgets.QWidget()
        reply_layout = column(self.reply)
        self.input = Editor(enter_sends=True)
        self.input.setPlaceholderText("Message…")
        self.input.setAccessibleName("Message or answer")
        self.input.setMinimumHeight(55)
        self.input.setMaximumHeight(140)
        self.input.submitted.connect(self.submit)
        self.input.textChanged.connect(self.resize_input)
        reply_layout.addWidget(self.input)
        composer_actions = row()
        composer_actions.addWidget(label("N to speak · Shift+Enter for a new line", "muted"))
        composer_actions.addStretch()
        self.send = button("Send", self.submit, name="primary")
        composer_actions.addWidget(self.send)
        reply_layout.addLayout(composer_actions)
        layout.addWidget(self.reply)
        self.reply.hide()
        self.write = button("Write a message", self.compose)
        layout.addWidget(self.write, 0, Qt.AlignmentFlag.AlignLeft)
        self.resize_handle = GestureHandle(self.window, lambda event: self.start_resize(self.pointer(event)),
                                          lambda event: self.resize_to(self.pointer(event)), lambda event: self.end_resize(self.pointer(event)))
        self.resize_handle.setCursor(Qt.CursorShape.SizeHorCursor)
        self.corner = GestureHandle(self.window, lambda event: self.start_resize(self.pointer(event), corner=True),
                                    lambda event: self.resize_to(self.pointer(event)), lambda event: self.end_resize(self.pointer(event)))
        self.corner.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.window.geometry_changed.connect(self.position_handles)
        self.position_handles()
        self.set_transparency()
        self.timer = QtCore.QTimer(self.window)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.acknowledge_approvals)
        self.timer.start()

    def open_menu(self) -> None:
        self.menu.popup(self.options.mapToGlobal(QtCore.QPoint(0, self.options.height())))

    def open_link(self, url: QtCore.QUrl) -> None:
        if url.scheme() in ("https", "http", "mailto"):
            webbrowser.open(url.toString())

    def cancel(self) -> None:
        self.agent.stop()

    def compose(self) -> None:
        self.composing = not bool(self.question)
        self.reply.show()
        self.write.hide()
        self.input.setFocus()

    def submit(self) -> None:
        if not (self.question or self.composing):
            return
        try:
            if self.agent.submit(self.input.toPlainText()):
                self.input.clear()
                self.composing = False
                if not self.question:
                    self.reply.hide()
                    self.write.show()
                    self.text.setFocus()
        except Exception:
            log_view(traceback.format_exc())
            self.agent.problem("Couldn't send that message. Your draft is still in the message box.")
        self.resize_input()

    def resize_input(self) -> None:
        self.send.setEnabled(bool(self.input.toPlainText().strip()) and not self.stopping)

    def new(self) -> None:
        if self.agent.new():
            self.input.clear()
            self.composing = False
            self.reply.hide()
            self.write.show()
            self.text.setFocus()

    def selection_anchor(self, position: int) -> TextAnchor:
        for index in range(len(self.starts) - 1, -1, -1):
            if position >= self.starts[index]:
                return TextAnchor(index, position - self.starts[index])
        return TextAnchor(0, position)

    def selection(self) -> Selection | None:
        cursor = self.text.textCursor()
        if not cursor.hasSelection():
            return None
        return Selection(self.selection_anchor(cursor.selectionStart()), self.selection_anchor(cursor.selectionEnd()), cursor.selectedText())

    def restore_selection(self, selected: Selection | None) -> None:
        if selected is None or max(selected.start.message, selected.end.message) >= len(self.starts):
            return
        cursor = self.text.textCursor()
        maximum = self.document.qt.characterCount() - 1
        cursor.setPosition(min(maximum, self.starts[selected.start.message] + selected.start.offset))
        cursor.setPosition(min(maximum, self.starts[selected.end.message] + selected.end.offset), QtGui.QTextCursor.MoveMode.KeepAnchor)
        if cursor.selectedText() == selected.text:
            self.text.setTextCursor(cursor)

    def render(self, state: Snapshot) -> None:  # noqa: PLR0915  # Preserve a single retained history projection.
        self.history_painted = False
        self.render_recording(state.recording)
        selected = self.selection()
        following = self.scroll.value() >= self.scroll.maximum() - 2 and selected is None
        scroll = self.scroll.value()
        if state.identity != self.identity:
            self.identity = state.identity
            self.items.clear()
            self.starts.clear()
            self.document.load((), max(1, self.text.viewport().width()))
            selected = None
            following = True
        spans: list[Span] = []
        self.starts = []
        offset = 0
        for message in state.messages:
            self.starts.append(offset)
            shown = presentation(message)
            stamp = time.strftime("%H:%M", time.localtime(message.created_at))
            current = [Span(f"{shown.role}  ·  {stamp}\n", ("muted",)), *(
                (Span(message.text),) if shown.style == "plain" else formatted(message.text)
            )]
            if shown.detail:
                current.append(Span("\n" + shown.detail, ("code", "muted")))
            match shown.response:
                case ResponseText(text):
                    current.append(Span("\n\n" + text, ("muted",)))
                case ApprovalControls() | None:
                    pass
                case _:
                    assert_never(shown.response)
            current.append(Span("\n\n"))
            offset += sum(units(span.text) for span in current)
            spans.extend(current)
        self.document.load(tuple(spans), max(1, self.text.viewport().width()))
        self.items = list(state.messages)
        self.restore_selection(selected)
        self.scroll.setValue(self.scroll.maximum() if following else scroll)
        self.status.setText(state.status if state.busy or state.waiting or state.status == "Needs attention" else "")
        self.queued = state.queued
        self.queue.setText(f"+{len(state.queued)}" if state.queued else "")
        self.queue.setToolTip("\n\n".join(state.queued))
        self.stop.setVisible(state.busy)
        self.stop.setEnabled(not state.stopping)
        self.stopping = state.stopping
        self.new_action.setEnabled(not state.busy)
        self.compose_action.setEnabled(not state.stopping)
        question = state.question.request_id if state.question is not None else ""
        if self.question and question != self.question:
            self.input.clear()
        self.question = question
        self.input.setPlaceholderText("Reply…" if question else "Message…")
        self.reply.setVisible(bool(question) or self.composing)
        self.write.setVisible(not (question or self.composing))
        self.resize_input()
        text = "\n\n".join(state.activity)
        if self.activity_text.toPlainText() != text:
            old_scroll = self.activity_text.verticalScrollBar().value()
            self.activity_text.setPlainText(text)
            self.activity_text.verticalScrollBar().setValue(old_scroll)
        self.render_approvals()
        self.scrolled()

    def render_approvals(self) -> None:
        desired = {index: message for index, message in enumerate(self.items) if isinstance(message, Approval) and pending(message)}
        for index in tuple(self.controls):
            if index not in desired or self.controls[index].property("request") != desired[index].request_id:
                self.controls.pop(index).deleteLater()
        for index, message in desired.items():
            if index in self.controls:
                continue
            frame = QtWidgets.QWidget()
            frame.setProperty("request", message.request_id)
            actions = row(frame)
            actions.addWidget(button("Read preview", partial(self.reveal_message, index)))
            approve = button("Approve", partial(self.respond, index, message.request_id, "approve"), name="primary")
            approve.setObjectName("approve")
            approve.setEnabled(False)
            actions.addWidget(approve)
            actions.addWidget(button("Decline", partial(self.respond, index, message.request_id, "decline")))
            self.controls[index] = frame
            self.approval_layout.addWidget(frame)
        self.approval_bar.setVisible(bool(desired))

    def reveal_message(self, index: int) -> None:
        cursor = self.text.textCursor()
        cursor.setPosition(self.starts[index])
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()

    def respond(self, index: int, identity: str, answer: str) -> None:
        if self.agent.respond(identity, answer) and (frame := self.controls.get(index)) is not None:
            frame.setEnabled(False)

    def approval_visible(self, request_id: str) -> bool:
        if not self.history_painted or not self.text.isVisible() or not windows.is_visible(self.handle):
            return False
        for index, message in enumerate(self.items):
            if isinstance(message, Approval) and message.request_id == request_id and pending(message):
                cursor = QtGui.QTextCursor(self.document.qt)
                cursor.setPosition(self.starts[index])
                return self.text.viewport().rect().intersects(self.text.cursorRect(cursor))
        return False

    def acknowledge_approvals(self) -> None:
        for index, message in enumerate(self.items):
            if isinstance(message, Approval) and self.approval_visible(message.request_id):
                self.agent.input_presented(message.request_id)
                frame = self.controls.get(index)
                if frame is not None and (approve := frame.findChild(QtWidgets.QToolButton, "approve")) is not None:
                    approve.setEnabled(True)

    def scrolled(self) -> None:
        self.history_painted = False
        self.latest.setVisible(self.scroll.value() < self.scroll.maximum() - 2)

    def history_paint(self) -> None:
        self.history_painted = True
        self.acknowledge_approvals()

    def see_latest(self) -> None:
        self.scroll.setValue(self.scroll.maximum())

    def activity(self) -> None:
        self.details.setVisible(self.show_activity.isChecked())

    def recording_action(self, action: RecordingAction) -> None:
        if self.agent.on_recording is not None:
            self.agent.on_recording(action)

    def toggle_recording(self) -> None:
        self.recording_action("stop" if self.recording_state.capturing else "start")

    def recording_control(self) -> None:
        self.recording_action(self.recording_state.control)

    def render_recording(self, state: RecordingState) -> None:
        self.recording_state = state
        self.recording_bar.setVisible(state.visible)
        self.recording_label.setText(f"● {phase_label(state.phase)} · {duration(state.seconds)}")
        self.recording_label.setToolTip(source_name(state.source) + " · Open full transcript")
        self.recording_button.setText(state.control.capitalize())
        self.recording_button.setEnabled(state.phase != "stopping")
        self.recording_menu.setText("Stop recording" if state.capturing else "Record a meeting")
        self.microphone_action.setEnabled(not state.capturing)

    def pin(self) -> None:
        self.preferences = replace(self.preferences, pinned=self.pinned.isChecked())
        windows.show_quietly(self.handle, visible=self.window.isVisible(), topmost=self.preferences.pinned)
        self.remember()

    def set_transparency(self) -> None:
        translucent = self.translucent.isChecked()
        color = "rgba(32,40,49,238)" if translucent else "#202831"
        self.window.setStyleSheet(STYLE + f"\nQWidget#toolkitWindow {{ background: {color}; border: 1px solid #465465; border-radius: 12px; }}")
        if translucent != self.preferences.translucent:
            self.preferences = replace(self.preferences, translucent=translucent)
            self.remember()

    def position_handles(self) -> None:
        left = self.preferences.edge == "right"
        self.resize_handle.setGeometry(0 if left else self.window.width() - 6, 38, 6, max(1, self.window.height() - 56))
        self.corner.setGeometry(self.window.width() - 18, self.window.height() - 18, 18, 18)
        self.corner.setVisible(self.preferences.edge is None)
        self.resize_handle.raise_()
        self.corner.raise_()

    def pointer(self, event: QtGui.QMouseEvent) -> tuple[int, int]:
        local = self.window.mapFromGlobal(event.globalPosition().toPoint())
        bounds, scale = windows.outer_rect(self.handle), windows.scale(self.handle)
        return bounds.left + round(local.x() * scale), bounds.top + round(local.y() * scale)

    def move_to(self, bounds: Rect) -> None:
        scale = windows.scale(self.handle)
        self.window.resize(round(bounds.width / scale), round(bounds.height / scale))
        windows.move(self.handle, bounds)

    def work_area(self, point: tuple[int, int]) -> Rect:
        return nearest_work_area(point, [monitor.work for monitor in screen.monitors()])

    def start_drag(self, point: tuple[int, int]) -> None:
        self.cancel_interaction()
        self.gesture = MovePanel(point, windows.outer_rect(self.handle), self.preferences.edge)

    def drag_to(self, point: tuple[int, int]) -> None:
        if not isinstance(self.gesture, MovePanel):
            return
        gesture = self.gesture
        dx, dy = point[0] - gesture.start[0], point[1] - gesture.start[1]
        if abs(dx) + abs(dy) < 4:
            return
        height = self.floating_height if gesture.edge is not None else gesture.bounds.height
        self.preferences = replace(self.preferences, edge=None)
        self.move_to(Rect(gesture.bounds.left + dx, gesture.bounds.top + dy, gesture.bounds.right + dx, gesture.bounds.top + dy + height))
        self.position_handles()

    def end_drag(self, point: tuple[int, int]) -> None:
        if not isinstance(self.gesture, MovePanel):
            return
        origin = self.gesture.bounds
        self.drag_to(point)
        self.gesture = None
        bounds, scale = windows.outer_rect(self.handle), windows.scale(self.handle)
        if bounds != origin:
            self.floating_height = bounds.height
            target, edge = snap_panel(bounds, self.work_area(point), (round(340 * scale), round(360 * scale)), distance=round(28 * scale))
            self.preferences = replace(self.preferences, edge=edge)
            self.move_to(target)
            self.position_handles()
        self.remember()

    def start_resize(self, point: tuple[int, int], *, corner: bool = False) -> None:
        self.cancel_interaction()
        self.gesture = ResizePanel(point, windows.outer_rect(self.handle), corner)

    def resize_to(self, point: tuple[int, int]) -> None:
        if not isinstance(self.gesture, ResizePanel):
            return
        gesture = self.gesture
        bounds, scale = gesture.bounds, windows.scale(self.handle)
        work = self.work_area((bounds.left + bounds.width // 2, bounds.top + 1))
        delta = gesture.start[0] - point[0] if self.preferences.edge == "right" else point[0] - gesture.start[0]
        width = min(max(round(340 * scale), bounds.width + delta), round(640 * scale), work.width)
        if self.preferences.edge is not None:
            target = dock_panel(width, work, self.preferences.edge)
        else:
            height = bounds.height + point[1] - gesture.start[1] if gesture.corner else bounds.height
            height = min(max(round(360 * scale), height), max(1, work.bottom - bounds.top))
            target = Rect(bounds.left, bounds.top, min(work.right, bounds.left + width), bounds.top + height)
        self.move_to(target)

    def end_resize(self, point: tuple[int, int]) -> None:
        if isinstance(self.gesture, ResizePanel):
            self.resize_to(point)
            self.gesture = None
            self.floating_height = windows.outer_rect(self.handle).height
            self.remember()

    def cancel_interaction(self) -> None:
        match self.gesture:
            case MovePanel(bounds=bounds, edge=edge):
                self.preferences = replace(self.preferences, edge=edge)
                self.move_to(bounds)
            case ResizePanel(bounds=bounds):
                self.move_to(bounds)
            case None:
                return
            case _:
                assert_never(self.gesture)
        self.gesture = None
        if (grabber := QtWidgets.QWidget.mouseGrabber()) is not None:
            grabber.releaseMouse()
        self.position_handles()

    def escape(self) -> None:
        if self.gesture is not None:
            self.cancel_interaction()
        else:
            self.hide()

    def show(self, *, focus: bool = False) -> None:
        if not self.window.isVisible():
            areas = [monitor.work for monitor in screen.monitors()]
            bounds = self.preferences.bounds
            point = (bounds.left, bounds.top) if bounds is not None else (areas[-1].right - 1, areas[-1].top)
            work = nearest_work_area(point, areas)
            display = QtGui.QGuiApplication.screenAt(QtCore.QPoint(work.left, work.top))
            scale = display.devicePixelRatio() if display is not None else self.window.devicePixelRatioF()
            if self.preferences.edge is not None:
                bounds = dock_panel(round(self.preferences.rail_width * scale), work, self.preferences.edge)
            else:
                bounds = bounds or Rect(work.right - round(540 * scale), work.top + 24, work.right - 24, work.bottom - 24)
                bounds = fit_panel(bounds, work, (340, 360))
            self.window.resize(round(bounds.width / scale), round(bounds.height / scale))
            windows.move(self.handle, bounds)
            self.position_handles()
        show_window(self.window, focus=focus, target=self.input if self.question or self.composing else self.text, topmost=self.preferences.pinned)

    def remember(self) -> None:
        if self.gesture is not None:
            return
        if self.window.isVisible() and not self.window.isMinimized() and not self.window.isMaximized():
            bounds = windows.outer_rect(self.handle)
            self.preferences = replace(
                self.preferences, bounds=bounds, rail_width=max(300, min(640, round(bounds.width / windows.scale(self.handle)))),
            )
        if self.remember_enabled:
            try:
                save_preferences(self.preferences)
            except OSError:
                log_view(traceback.format_exc())

    def hide(self) -> None:
        self.cancel_interaction()
        self.remember()
        self.window.hide()
        if self.on_hide is not None:
            self.on_hide()

    def close(self) -> None:
        self.cancel_interaction()
        self.remember()
        self.timer.stop()
        self.window.hide()
        self.window.deleteLater()
