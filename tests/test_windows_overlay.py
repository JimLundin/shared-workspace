"""Live key feedback on a hidden Qt canvas, without taking focus or sending input."""
# pyright: reportPrivateUsage=false

import sys
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows overlay")

from assistant.qt import Qt, QtCore, QtGui, QtWidgets
from assistant.qt_ui import application
from assistant.ui import KeymapCanvas, KeymapOverlay
from miryoku.layout import AGENT, Layer, Scancode
from winapi.shapes import Rect

WORK = Rect(0, 0, 1920, 1040)
Q, M = Scancode(0x10), Scancode(0x32)


@contextmanager
def hidden_overlay() -> Generator[tuple[QtWidgets.QApplication, KeymapOverlay, KeymapCanvas]]:
    root = application()
    try:
        with (
            patch("assistant.ui.windows.make_overlay"),
            patch("assistant.ui.windows.show_quietly"),
            patch("assistant.ui.windows.move"),
            patch("assistant.ui.screen.monitors", return_value=()),
            patch("assistant.ui.load_position", return_value=None),
        ):
            overlay = KeymapOverlay(root)
            overlay.show(None, "BASE", WORK, visible=True)
            canvas = overlay._canvas
            try:
                yield root, overlay, canvas
            finally:
                overlay.show(None, "BASE", WORK, visible=False)
                overlay._window.deleteLater()
                overlay._handle.deleteLater()
    finally:
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def key_style(canvas: KeymapCanvas, name: str) -> tuple[str, ...]:
    background, foreground = canvas.colors(name)
    return background, foreground, foreground, foreground


def test_overlay_drag_retains_physical_pixels_at_fractional_display_scale() -> None:
    with hidden_overlay() as (_, overlay, _), patch("assistant.ui.save_position"):
        overlay._move((1000, 600))
        for kind, logical, physical, pressed in (
            (QtCore.QEvent.Type.MouseButtonPress, (800, 480), (1000, 600), Qt.MouseButton.LeftButton),
            (QtCore.QEvent.Type.MouseMove, (820, 500), (1025, 625), Qt.MouseButton.LeftButton),
            (QtCore.QEvent.Type.MouseButtonRelease, (820, 500), (1025, 625), Qt.MouseButton.NoButton),
        ):
            event = QtGui.QMouseEvent(kind, QtCore.QPointF(5, 5), QtCore.QPointF(*logical),
                                     Qt.MouseButton.LeftButton, pressed, Qt.KeyboardModifier.NoModifier)
            with patch("assistant.ui.inputs.cursor_position", return_value=physical):
                QtWidgets.QApplication.sendEvent(overlay._handle, event)
        assert overlay._position == (1025, 625) and overlay._drag is None


def check_chord_feedback(overlay: KeymapOverlay, canvas: KeymapCanvas) -> None:
    now = 1.0
    with patch("assistant.ui.time.monotonic", side_effect=lambda: now):
        normal_q, normal_m, normal_b = (key_style(canvas, name) for name in ("Q", "M", "B"))
        normal_n = key_style(canvas, "N")
        items = tuple(canvas.boxes.items())
        overlay.set_pressed(frozenset({Q, M, AGENT}))
        pressed_q, pressed_m = key_style(canvas, "Q"), key_style(canvas, "M")
        assert pressed_q != normal_q and pressed_m != normal_m
        assert pressed_q == pressed_m  # both keys use the same contrasting feedback
        assert pressed_q[0] != pressed_q[1] == pressed_q[2] == pressed_q[3]
        assert key_style(canvas, "B") == normal_b
        assert key_style(canvas, "N") == pressed_q
        now = 2.0
        overlay.set_pressed(frozenset({M}))
        assert key_style(canvas, "Q") == normal_q
        assert key_style(canvas, "M") == pressed_m
        assert key_style(canvas, "N") == normal_n
        overlay.set_pressed(frozenset())
        assert key_style(canvas, "M") == normal_m
        assert tuple(canvas.boxes.items()) == items


def check_feedback_across_layers(overlay: KeymapOverlay, canvas: KeymapCanvas) -> None:
    now = 1.0
    with patch("assistant.ui.time.monotonic", side_effect=lambda: now):
        overlay.show(Layer.SYM, "SYM", WORK, visible=True)
        normal_q, normal_m = key_style(canvas, "Q"), key_style(canvas, "M")
        overlay.show(None, "BASE", WORK, visible=True)
        overlay.set_pressed(frozenset({Q, M}))
        gold = key_style(canvas, "M")
        now = 1.01
        overlay.set_pressed(frozenset({M}))
        overlay.show(Layer.SYM, "SYM", WORK, visible=True, pressed=frozenset({M}))
        assert key_style(canvas, "Q")[0] == gold[0]
        assert key_style(canvas, "M") == gold
        now = 1.121
        overlay.set_pressed(frozenset({M}))
        assert key_style(canvas, "Q") == normal_q
        assert key_style(canvas, "M") == gold
        overlay.set_pressed(frozenset())
        assert key_style(canvas, "M") == normal_m  # restores the blue layer indicator


def check_hiding_and_resetting(overlay: KeymapOverlay, canvas: KeymapCanvas) -> None:
    now = 1.0
    with patch("assistant.ui.time.monotonic", side_effect=lambda: now):
        overlay.show(None, "BASE", WORK, visible=True)
        normal_q, normal_m = key_style(canvas, "Q"), key_style(canvas, "M")
        overlay.set_pressed(frozenset({Q}))
        gold = key_style(canvas, "Q")
        overlay.set_pressed(frozenset())
        assert key_style(canvas, "Q") == gold
        overlay.show(None, "BASE", WORK, visible=False)
        overlay.set_pressed(frozenset({Q, M}))  # state arriving while hidden must not replay old flashes
        overlay.show(None, "BASE", WORK, visible=True, pressed=frozenset({M}))
        assert key_style(canvas, "Q") == normal_q
        assert key_style(canvas, "M") == gold
        overlay.set_pressed(frozenset(), reset=True)
        assert key_style(canvas, "M") == normal_m


def test_overlay_renders_chords_flashes_layer_changes_and_resets() -> None:
    with hidden_overlay() as (root, overlay, canvas):
        check_chord_feedback(overlay, canvas)
        check_feedback_across_layers(overlay, canvas)
        check_hiding_and_resetting(overlay, canvas)
        normal = key_style(canvas, "Q")
        overlay.set_pressed(frozenset({Q}))
        overlay.set_pressed(frozenset())
        assert key_style(canvas, "Q") != normal
        QtCore.QTimer.singleShot(250, root.quit)
        root.exec()
        assert key_style(canvas, "Q") == normal
