"""Live key feedback on a hidden Tk canvas, without taking focus or sending input."""

import sys
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from typing import Protocol, cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows overlay")

import tkinter as tk

from assistant.ui import KeymapOverlay
from miryoku.layout import AGENT, Layer, Scancode
from winapi.shapes import Rect

WORK = Rect(0, 0, 1920, 1040)
Q, M = Scancode(0x10), Scancode(0x32)


class CanvasReading(Protocol):
    """The concrete Tk return type missing from its itemcget stub."""

    def itemcget(self, tag: str, option: str) -> str: ...


@contextmanager
def hidden_overlay() -> Generator[tuple[tk.Tk, KeymapOverlay, tk.Canvas]]:
    root = tk.Tk()
    root.withdraw()
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
            canvas = cast("tk.Canvas", next(
                child for window in root.winfo_children() for child in window.winfo_children() if child.winfo_class() == "Canvas"
            ))
            try:
                yield root, overlay, canvas
            finally:
                overlay.show(None, "BASE", WORK, visible=False)
    finally:
        root.destroy()


def key_style(canvas: tk.Canvas, name: str) -> tuple[str, ...]:
    reading = cast("CanvasReading", canvas)
    return tuple(reading.itemcget(f"key:{name}:{part}", "fill") for part in ("background", "physical", "label", "hold"))


def check_chord_feedback(overlay: KeymapOverlay, canvas: tk.Canvas) -> None:
    now = 1.0
    with patch("assistant.ui.time.monotonic", side_effect=lambda: now):
        normal_q, normal_m, normal_b = (key_style(canvas, name) for name in ("Q", "M", "B"))
        normal_n = key_style(canvas, "N")
        items = canvas.find_all()
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
        assert canvas.find_all() == items


def check_feedback_across_layers(overlay: KeymapOverlay, canvas: tk.Canvas) -> None:
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


def check_hiding_and_resetting(overlay: KeymapOverlay, canvas: tk.Canvas) -> None:
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
        root.after(250, root.quit)
        root.mainloop()
        assert key_style(canvas, "Q") == normal
