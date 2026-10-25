"""Shared Qt controls, selection-preserving text updates and native window behavior."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import threading
from collections.abc import Callable

from winapi import windows

from .canvas_widgets import STYLE
from .qt import Qt, QtCore, QtGui, QtWidgets


class Window(QtWidgets.QWidget):
    geometry_changed = QtCore.Signal()

    def __init__(self, title: str, close: Callable[[], None], *, width: int = 900, height: int = 640) -> None:
        super().__init__()
        self.on_close = close
        self.setObjectName("toolkitWindow")
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(width, height)
        self.escape = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self)
        self.escape.activated.connect(lambda: self.on_close())  # noqa: PLW0108  # Retain slotted callback owners.

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        event.ignore()
        self.on_close()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.geometry_changed.emit()


class GestureHandle(QtWidgets.QFrame):
    """One native pointer capture, released even when the gesture is cancelled."""

    def __init__(self, parent: QtWidgets.QWidget, start: Callable[[QtGui.QMouseEvent], None],
                 move: Callable[[QtGui.QMouseEvent], None], finish: Callable[[QtGui.QMouseEvent], None]) -> None:
        super().__init__(parent)
        self.start, self.move_pointer, self.finish = start, move, finish

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.start(event)
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move_pointer(event)
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.releaseMouse()
            self.finish(event)
            event.accept()


def show_window(window: QtWidgets.QWidget, *, focus: bool, target: QtWidgets.QWidget | None = None, topmost: bool = False) -> None:
    handle = int(window.winId())
    windows.allow_activation(handle, False)
    try:
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.show()
        windows.show_quietly(handle, visible=True, topmost=topmost)
    finally:
        windows.allow_activation(handle, True)
    if focus:
        threading.Thread(target=lambda: windows.focus(handle), name="focus toolkit", daemon=True).start()
        (target or window).setFocus(Qt.FocusReason.OtherFocusReason)


class Editor(QtWidgets.QPlainTextEdit):
    submitted = QtCore.Signal()
    painted = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None, *, readonly: bool = False, enter_sends: bool = False) -> None:
        super().__init__(parent)
        self.enter_sends = enter_sends
        self.setReadOnly(readonly)
        self.setTabChangesFocus(True)
        self.setMinimumHeight(60)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        send = not bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier) if self.enter_sends else bool(
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        if enter and send and not self.isReadOnly():
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        self.painted.emit()


class Browser(QtWidgets.QTextBrowser):
    painted = QtCore.Signal()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        self.painted.emit()


def replace_text(widget: QtWidgets.QPlainTextEdit, value: str) -> None:
    if widget.toPlainText() == value:
        return
    cursor = widget.textCursor()
    anchor, position, selected = cursor.anchor(), cursor.position(), cursor.selectedText()
    scroll = widget.verticalScrollBar().value()
    widget.setPlainText(value)
    maximum = widget.document().characterCount() - 1
    cursor.setPosition(min(anchor, maximum))
    cursor.setPosition(min(position, maximum), QtGui.QTextCursor.MoveMode.KeepAnchor)
    if cursor.selectedText() == selected:
        widget.setTextCursor(cursor)
    widget.verticalScrollBar().setValue(scroll)


def row(parent: QtWidgets.QWidget | None = None) -> QtWidgets.QHBoxLayout:
    layout = QtWidgets.QHBoxLayout(parent) if parent is not None else QtWidgets.QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    return layout


def column(parent: QtWidgets.QWidget | None = None) -> QtWidgets.QVBoxLayout:
    layout = QtWidgets.QVBoxLayout(parent) if parent is not None else QtWidgets.QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    return layout
