"""End to end on Windows: the real layout process, real key presses, a real window.

It presses physical keys with SendInput, so it takes over the keyboard for a few
seconds; that's why it only runs when asked: `TOOLKIT_E2E=1 pytest tests/test_windows_e2e.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

if sys.platform != "win32" or os.environ.get("TOOLKIT_E2E") != "1":
    pytest.skip("end-to-end tests run on Windows, with TOOLKIT_E2E=1", allow_module_level=True)

import ctypes  # noqa: E402

from winapi import inputs  # noqa: E402 (Windows only)

UNDER_WINE = hasattr(ctypes.WinDLL("ntdll"), "wine_get_version")
"""Wine garbles typed Unicode text (KEYEVENTF_UNICODE) into Tk; real Windows doesn't."""

SCANCODES = {
    "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07, "8": 0x09, "9": 0x0A,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "i": 0x17, "o": 0x18,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "k": 0x25, "l": 0x26, ";": 0x27,
    "c": 0x2E, "v": 0x2F, ".": 0x34, "/": 0x35,
}  # fmt: skip


def wait_for(condition: Callable[[], bool], pump: Callable[[], None], seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pump()
        if condition():
            return True
        time.sleep(0.02)
    return False


@contextmanager
def layout() -> Generator[None, None, None]:
    process = subprocess.Popen([sys.executable, "-m", "miryoku"], cwd=ROOT)
    time.sleep(1.5)  # time to install its hook
    try:
        yield
    finally:
        process.terminate()
        process.wait()


@contextmanager
def text_box() -> Generator[tuple[tk.Tk, tk.Entry], None, None]:
    root = tk.Tk()
    entry = tk.Entry(root, width=40)
    entry.pack()
    root.update()
    root.focus_force()
    entry.focus_set()
    root.update()
    try:
        yield root, entry
    finally:
        root.destroy()


def tap(*names: str) -> None:
    for name in names:
        inputs.send_scancodes([(SCANCODES[name], True), (SCANCODES[name], False)], extra=0)
        time.sleep(0.03)


def test_layout_types_colemak_dh_and_expands_snippets() -> None:
    with layout(), text_box() as (root, entry):
        tap("2", "3", "4", "5")  # top row moved up: q w f p
        tap("q", "w", "e", "r")  # home row taps: a r s t
        assert wait_for(lambda: entry.get() == "qwfparst", root.update), entry.get()
        tap(";", ";", "r", "l", "a")  # , , t h x: a snippet
        if UNDER_WINE:  # the trigger is erased; what's typed instead can't be checked here
            assert wait_for(lambda: len(entry.get()) > len("qwfparst") and "," not in entry.get(), root.update), entry.get()
        else:
            assert wait_for(lambda: entry.get() == "qwfparstThanks!", root.update), entry.get()
