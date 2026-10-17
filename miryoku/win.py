"""Windows glue: installs a low-level keyboard hook, feeds keys to the engine,
sends the results, and runs the message loop. Win32 is reached only through the
typed bindings in `win32`.
"""

from __future__ import annotations

import gc
import sys
import traceback
from enum import Enum, auto
from typing import Final

from . import win32
from .engine import Engine, Signal, Verdict
from .layout import TAPPING_TERM_MS, Scancode, is_extended

MAGIC: Final = 0x4D49_524F
"""Tags keys we send ourselves so the hook lets them through untouched."""
LOCK_NAME: Final = "Local\\miryoku"
"""Name of the lock that stops a second copy from running in this Windows session."""


class WinHost:
    """What the engine needs, done with Windows calls."""

    __slots__ = ()

    def send(self, vk: int, down: bool) -> None:
        flags = (0 if down else win32.KEYEVENTF_KEYUP) | (win32.KEYEVENTF_EXTENDEDKEY if is_extended(vk) else 0)
        # The scancode, which some programs read, from Windows' own layout table.
        win32.send_key(vk, win32.scancode_for(vk), flags, MAGIC)

    def start_timer(self) -> int:
        return win32.set_timer(TAPPING_TERM_MS)

    def stop_timer(self, timer: int) -> None:
        win32.kill_timer(timer)

    def signal(self, signal: Signal) -> None:
        match signal:
            case Signal.ON:
                win32.beep(win32.MB_OK)
            case Signal.OFF:
                win32.beep(win32.MB_ICONHAND)
            case Signal.CAPS_WORD:
                win32.beep(win32.MB_ICONASTERISK)


class Exit(Enum):
    QUIT = auto()
    """Quit normally (the escape key was held)."""
    ALREADY_RUNNING = auto()
    HOOK_REFUSED = auto()
    """Windows refused the keyboard hook."""


class Keyboard:
    """The engine, plus what the hook callback needs around it."""

    __slots__ = ("busy", "engine", "host")

    def __init__(self) -> None:
        self.engine = Engine()
        self.host = WinHost()
        self.busy = False
        """Set while the engine runs; a re-entrant call lets its key through."""

    def hook_proc(self, code: int, wparam: int, lparam: int) -> int:
        """Called by Windows for every key, before any application sees it."""
        if code == 0 and wparam in (win32.WM_KEYDOWN, win32.WM_SYSKEYDOWN, win32.WM_KEYUP, win32.WM_SYSKEYUP):
            down = wparam in (win32.WM_KEYDOWN, win32.WM_SYSKEYDOWN)
            try:
                if self.swallow(win32.read_key_event(lparam), down):
                    return 1
            except Exception:
                # A bug must never eat the keyboard: report it and let the key through.
                traceback.print_exc(file=sys.stderr)
        return win32.call_next_hook(code, wparam, lparam)

    def swallow(self, key: win32.KeyEvent, down: bool) -> bool:
        """Runs the engine on one key; True if the original key should be blocked."""
        # Our own keys, and extended keys (arrows, numpad Enter...), which share
        # scancodes with main keys, are never ours.
        if key.extra == MAGIC or key.flags & win32.LLKHF_EXTENDED or self.busy:
            return False
        self.busy = True
        try:
            verdict = self.engine.on_key(self.host, Scancode(key.scancode), down, key.time)
        finally:
            self.busy = False
        match verdict:
            case Verdict.PASS:
                return False
            case Verdict.SWALLOW:
                return True
            case Verdict.QUIT:
                win32.post_quit()
                return True


def run() -> Exit:
    """Blocks until the layout quits."""
    lock = win32.create_lock(LOCK_NAME)
    if lock is None:
        return Exit.ALREADY_RUNNING
    try:
        keyboard = Keyboard()
        hook = win32.set_keyboard_hook(keyboard.hook_proc)
        if hook is None:
            return Exit.HOOK_REFUSED
        # Nothing here creates reference cycles, so the cycle collector has nothing to
        # do; turning it off means it can never pause the hook mid-keypress.
        gc.collect()
        gc.freeze()
        gc.disable()
        try:
            # Windows only delivers hook calls and timer ticks while this loop waits.
            for message in win32.messages():
                if message.message == win32.WM_TIMER:
                    keyboard.engine.timeout(keyboard.host, message.wparam)
        finally:
            win32.remove_hook(hook)
            keyboard.engine.release_all(keyboard.host)  # let go of anything still held
            gc.enable()
        return Exit.QUIT
    finally:
        win32.release_lock(lock)


def show_error(message: str) -> None:
    win32.message_box(message, "Miryoku", win32.MB_ICONHAND)
