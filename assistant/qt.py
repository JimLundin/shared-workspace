"""Qt imports with the assistant's Python 3.15 initialization order.

PySide6 6.12 can retain an unresolved ThreadPoolExecutor export if imported
first, breaking asyncio.to_thread in the SDK. Resolve that public Python export
before any Qt binding loads. All conversation views import Qt through here.
"""

from concurrent.futures import ThreadPoolExecutor  # noqa: F401  # pyright: ignore[reportUnusedImport]

import shiboken6
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QRegion,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
    QWheelEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QFrame, QLabel, QMenu, QTextBrowser, QTextEdit, QWidget

__all__ = [
    "QAbstractButton",
    "QApplication",
    "QColor",
    "QContextMenuEvent",
    "QCoreApplication",
    "QEvent",
    "QFont",
    "QFontMetricsF",
    "QFrame",
    "QImage",
    "QKeyEvent",
    "QLabel",
    "QMenu",
    "QMouseEvent",
    "QPaintEvent",
    "QPainter",
    "QPainterPath",
    "QPalette",
    "QPen",
    "QPoint",
    "QRectF",
    "QRegion",
    "QTest",
    "QTextBlockFormat",
    "QTextBrowser",
    "QTextCharFormat",
    "QTextCursor",
    "QTextDocument",
    "QTextEdit",
    "QTextOption",
    "QWheelEvent",
    "QWidget",
    "Qt",
    "QtCore",
    "QtGui",
    "QtSvg",
    "QtWidgets",
    "shiboken6",
]
