"""One Qt event loop and Windows composition lifetime for all assistant views."""

from collections.abc import Callable
from typing import ClassVar

from winapi import screen
from winapi.composition import DesktopComposition
from winapi.shapes import Rect

from .agent_window import log_view
from .qt import QApplication, QCoreApplication, QEvent, QtCore, shiboken6


def application() -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def later(parent: QtCore.QObject, milliseconds: int, callback: Callable[[], None]) -> QtCore.QTimer:
    """Schedule one UI-owned callback. Closing its owner cancels delivery."""
    timer = QtCore.QTimer(parent)
    timer.setSingleShot(True)

    def deliver() -> None:
        timer.deleteLater()
        callback()

    timer.timeout.connect(deliver)
    timer.start(milliseconds)
    return timer


def cancel(timer: QtCore.QTimer | None) -> None:
    if timer is not None and shiboken6.isValid(timer):
        timer.stop()
        timer.deleteLater()


class EventLoop:
    __slots__ = ("app", "closed", "composition", "painted", "timer")
    _users: ClassVar[int] = 0
    _composition: ClassVar[DesktopComposition | None] = None

    def __init__(self, app: QApplication, painted: Callable[[], None]) -> None:
        self.app, self.painted = app, painted
        self.closed = False
        self.timer = QtCore.QTimer(app)
        self.timer.setInterval(16)
        self.timer.timeout.connect(lambda: self.painted())  # noqa: PLW0108  # Retain slotted callback owners.
        EventLoop._users += 1
        if EventLoop._users == 1:
            try:
                EventLoop._composition = DesktopComposition()
            except OSError as error:
                log_view(f"Desktop glass unavailable; using the solid material: {error}")
        self.composition = EventLoop._composition

    def pump(self) -> None:
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if not self.closed:
            self.painted()

    def scale(self, work: Rect) -> float:
        area = next((monitor.area for monitor in screen.monitors() if monitor.area.contains(work.left, work.top)), None)
        target = next((display for display in self.app.screens() if area is not None
                       and (display.geometry().left(), display.geometry().top()) == (area.left, area.top)), self.app.primaryScreen())
        return target.devicePixelRatio()

    def watch(self, visible: bool) -> None:
        if not self.closed:
            self.timer.start() if visible else self.timer.stop()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.timer.deleteLater()
        EventLoop._users -= 1
        if EventLoop._users == 0 and EventLoop._composition is not None:
            EventLoop._composition.close(self.app.processEvents)
            EventLoop._composition = None
        self.composition = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
