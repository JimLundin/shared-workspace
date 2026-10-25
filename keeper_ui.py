"""Protected Qt dialogs for the keeper; independent of editable assistant modules.

Import this only after runtime activation. The bootstrap itself uses the standard
library and remains importable on Python 3.12. Qt is the pinned GUI dependency.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor  # noqa: F401  # pyright: ignore[reportUnusedImport]

from PySide6 import QtCore, QtGui, QtWidgets

STYLE = """
QWidget { color: #d8e0ea; background: #252c34; font: 12px 'Segoe UI'; }
QLabel { background: transparent; }
QPushButton { border: 1px solid #586271; border-radius: 6px; padding: 8px 14px; background: #303a46; }
QPushButton:hover { background: #415063; }
QPushButton:focus { border: 1px solid #b8d2ff; }
QPushButton#primary { background: #d9c8f3; color: #282431; border: 1px solid #dfd0f4; }
QPlainTextEdit { border: 1px solid #43505e; border-radius: 8px; background: #1e252d; padding: 12px; }
"""


class KeeperUi:
    __slots__ = (
        "__weakref__",
        "app",
        "timer",
        "trial",
        "windows",
    )

    def __init__(self) -> None:
        self.app = QtWidgets.QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setStyleSheet(STYLE)
        self.windows: list[QtWidgets.QDialog] = []
        self.trial: QtWidgets.QDialog | None = None
        self.timer = QtCore.QTimer()

    def dialog(self, title: str, text: str) -> tuple[QtWidgets.QDialog, QtWidgets.QVBoxLayout]:
        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle(title)
        dialog.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        message = QtWidgets.QLabel(text)
        message.setWordWrap(True)
        message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(message)
        return dialog, layout

    def approve(self, diff: str) -> bool:
        dialog, layout = self.dialog(
            "Toolkit update review",
            "Run checks and install this change?\n\nThese checks execute the proposed code with your Windows account.",
        )
        dialog.resize(900, 640)
        text = QtWidgets.QPlainTextEdit(diff)
        text.setReadOnly(True)
        text.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        text.setFont(QtGui.QFont("Consolas", 10))
        layout.addWidget(text, 1)
        controls = QtWidgets.QHBoxLayout()
        controls.addStretch()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        controls.addWidget(cancel)
        approve = QtWidgets.QPushButton("Run checks and install")
        approve.setObjectName("primary")
        approve.clicked.connect(dialog.accept)
        controls.addWidget(approve)
        layout.addLayout(controls)
        result = dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
        dialog.deleteLater()
        return result

    def corner(self, dialog: QtWidgets.QDialog) -> None:
        dialog.adjustSize()
        work = self.app.primaryScreen().availableGeometry()
        dialog.move(work.right() - dialog.width() - 24, work.bottom() - dialog.height() - 24)
        self.windows.append(dialog)
        dialog.finished.connect(lambda: self.release(dialog))
        dialog.show()

    def release(self, dialog: QtWidgets.QDialog) -> None:
        if dialog in self.windows:
            self.windows.remove(dialog)
        dialog.deleteLater()

    def notify(self, message: str) -> None:
        dialog, layout = self.dialog("Toolkit", message[:700])
        dialog.setMinimumWidth(440)
        close = QtWidgets.QPushButton("OK")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        self.corner(dialog)
        QtCore.QTimer.singleShot(20_000, dialog, dialog.accept)

    def show_trial(self, summary: str | None, choose: Callable[[str], None]) -> None:
        if self.trial is not None:
            self.trial.hide()
            self.trial.deleteLater()
            if self.trial in self.windows:
                self.windows.remove(self.trial)
            self.trial = None
        if summary is None:
            return
        dialog, layout = self.dialog("Toolkit update", f"Updated: {summary}\n\nOn trial for ten minutes; it's undone if it crashes.")
        dialog.setMinimumWidth(420)
        controls = QtWidgets.QHBoxLayout()
        controls.addStretch()
        undo = QtWidgets.QPushButton("Undo")
        keep = QtWidgets.QPushButton("Keep")
        keep.setObjectName("primary")
        undo.clicked.connect(lambda: choose("undo"))
        keep.clicked.connect(lambda: choose("keep"))
        dialog.rejected.connect(lambda: choose("keep"))
        controls.addWidget(undo)
        controls.addWidget(keep)
        layout.addLayout(controls)
        self.trial = dialog
        self.corner(dialog)

    def run(self, tick: Callable[[], bool]) -> None:
        def poll() -> None:
            if tick():
                self.timer.stop()
                self.app.quit()
        self.timer.setInterval(500)
        self.timer.timeout.connect(poll)
        self.timer.start()
        self.app.exec()
        for dialog in tuple(self.windows):
            dialog.hide()
            dialog.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
