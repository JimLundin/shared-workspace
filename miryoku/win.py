"""Windows glue: installs a low-level keyboard hook, feeds keys to the engine,
sends the results with SendInput, and runs the message loop.

Win32 is reached through ctypes from the standard library; each function used
is declared below with its exact argument and return types.
"""

from __future__ import annotations

import ctypes
import gc
import sys
import traceback
from collections.abc import Callable
from ctypes import wintypes
from enum import Enum, auto
from typing import Any, Final

from .engine import Engine, Signal, Verdict
from .layout import TAPPING_TERM_MS, Scancode, is_extended

# ---- Win32 declarations ------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):  # noqa: N801 (Win32 name)
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


# Struct sizes must match Windows exactly, or SendInput silently does nothing.
if ctypes.sizeof(INPUT) != (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28):
    raise ImportError(f"INPUT is {ctypes.sizeof(INPUT)} bytes; Windows expects a different size")

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def declare(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object) -> Callable[..., Any]:
    """A Win32 function with its exact signature. ctypes checks and converts the
    arguments at call time; results are converted explicitly where they are used."""
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = argtypes
    return function  # type: ignore[no-any-return]


SetWindowsHookExW: Final = declare(user32, "SetWindowsHookExW", wintypes.HHOOK, ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
CallNextHookEx: Final = declare(user32, "CallNextHookEx", LRESULT, wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
UnhookWindowsHookEx: Final = declare(user32, "UnhookWindowsHookEx", wintypes.BOOL, wintypes.HHOOK)
GetMessageW: Final = declare(user32, "GetMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
PostQuitMessage: Final = declare(user32, "PostQuitMessage", None, ctypes.c_int)
SendInput: Final = declare(user32, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
MapVirtualKeyW: Final = declare(user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
SetTimer: Final = declare(user32, "SetTimer", ULONG_PTR, wintypes.HWND, ULONG_PTR, wintypes.UINT, ctypes.c_void_p)
KillTimer: Final = declare(user32, "KillTimer", wintypes.BOOL, wintypes.HWND, ULONG_PTR)
MessageBeep: Final = declare(user32, "MessageBeep", wintypes.BOOL, wintypes.UINT)
MessageBoxW: Final = declare(user32, "MessageBoxW", ctypes.c_int, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
CreateMutexW: Final = declare(kernel32, "CreateMutexW", wintypes.HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
CloseHandle: Final = declare(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
GetModuleHandleW: Final = declare(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)

WH_KEYBOARD_LL: Final = 13
WM_TIMER: Final = 0x0113
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_EXTENDED: Final = 0x01
INPUT_KEYBOARD: Final = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
MAPVK_VK_TO_VSC: Final = 0
MB_OK, MB_ICONHAND, MB_ICONASTERISK = 0x00, 0x10, 0x40
ERROR_ALREADY_EXISTS: Final = 183

MAGIC: Final = 0x4D49_524F
"""Tags keys we send ourselves so the hook lets them through untouched."""
LOCK_NAME: Final = "Local\\miryoku"
"""Name of the lock that stops a second copy from running in this Windows session."""


# ---- The host the engine talks to ----------------------------------------------------


class WinHost:
    def send(self, vk: int, down: bool) -> None:
        flags = (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if is_extended(vk) else 0)
        # The scancode, which some programs read, from Windows' own layout table.
        scan = int(MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
        event = INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=KEYBDINPUT(vk, scan, flags, 0, MAGIC)))
        SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))

    def start_timer(self) -> int:
        return int(SetTimer(None, 0, TAPPING_TERM_MS, None))

    def stop_timer(self, timer: int) -> None:
        KillTimer(None, timer)

    def signal(self, signal: Signal) -> None:
        """Windows system sounds; they play in the background, so the hook isn't held up."""
        match signal:
            case Signal.ON:
                MessageBeep(MB_OK)
            case Signal.OFF:
                MessageBeep(MB_ICONHAND)
            case Signal.CAPS_WORD:
                MessageBeep(MB_ICONASTERISK)


# ---- Running -------------------------------------------------------------------------


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
        if code == 0:
            kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            up = wparam in (WM_KEYUP, WM_SYSKEYUP)
            if kb.dwExtraInfo != MAGIC and (down or up):
                try:
                    if self.swallow(kb, down):
                        return 1
                except Exception:
                    # A bug must never eat the keyboard: report it and let the key through.
                    traceback.print_exc(file=sys.stderr)
        return int(CallNextHookEx(None, code, wparam, lparam))

    def swallow(self, kb: KBDLLHOOKSTRUCT, down: bool) -> bool:
        """Runs the engine on one key; True if the original key should be blocked."""
        # Extended keys (arrows, numpad Enter...) share scancodes with main keys: never ours.
        if kb.flags & LLKHF_EXTENDED or self.busy:
            return False
        self.busy = True
        try:
            verdict = self.engine.on_key(self.host, Scancode(kb.scanCode), down, kb.time)
        finally:
            self.busy = False
        match verdict:
            case Verdict.PASS:
                return False
            case Verdict.SWALLOW:
                return True
            case Verdict.QUIT:
                PostQuitMessage(0)
                return True

    def message_loop(self) -> None:
        """Windows only delivers hook calls and timer ticks while this loop waits."""
        msg = wintypes.MSG()
        while GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER:
                self.engine.timeout(self.host, int(msg.wParam))


def run() -> Exit:
    """Blocks until the layout quits."""
    lock = CreateMutexW(None, False, LOCK_NAME)
    if not lock or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if lock:
            CloseHandle(lock)
        return Exit.ALREADY_RUNNING
    try:
        keyboard = Keyboard()
        callback = HOOKPROC(keyboard.hook_proc)  # kept alive here for as long as the hook is installed
        hook = SetWindowsHookExW(WH_KEYBOARD_LL, callback, GetModuleHandleW(None), 0)
        if not hook:
            return Exit.HOOK_REFUSED
        # Nothing here creates reference cycles, so the cycle collector has nothing to
        # do; turning it off means it can never pause the hook mid-keypress.
        gc.collect()
        gc.freeze()
        gc.disable()
        try:
            keyboard.message_loop()
        finally:
            UnhookWindowsHookEx(hook)
            keyboard.engine.release_all(keyboard.host)  # let go of anything still held
            gc.enable()
        return Exit.QUIT
    finally:
        CloseHandle(lock)


def show_error(message: str) -> None:
    MessageBoxW(None, message, "Miryoku", MB_ICONHAND)
