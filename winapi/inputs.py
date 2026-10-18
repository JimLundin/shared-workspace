"""Keyboard and mouse: the low-level keyboard hook, and sending input as if typed."""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final

from ._core import LRESULT, ULONG_PTR, fn, kernel32, user32
from .shapes import Rect, require_input_count, utf16_units


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class INPUT_UNION(ctypes.Union):  # noqa: N801 (Win32 name)
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT))


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("u", INPUT_UNION))


# Struct sizes must match Windows exactly, or SendInput silently does nothing.
if ctypes.sizeof(INPUT) != (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28):
    raise ImportError(f"INPUT is {ctypes.sizeof(INPUT)} bytes; Windows expects a different size")

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_SetWindowsHookExW = fn(user32, "SetWindowsHookExW", wintypes.HHOOK, ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_CallNextHookEx = fn(user32, "CallNextHookEx", LRESULT, wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_UnhookWindowsHookEx = fn(user32, "UnhookWindowsHookEx", wintypes.BOOL, wintypes.HHOOK)
_GetModuleHandleW = fn(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
_SendInput = fn(user32, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_MapVirtualKeyW = fn(user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
_GetCursorPos = fn(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))

LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0004, 0x0008
_WH_KEYBOARD_LL: Final = 13
_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_MAPVK_VK_TO_VSC: Final = 0
_VK_RETURN: Final = 0x0D
_MOUSEEVENTF_MOVE, _MOUSEEVENTF_ABSOLUTE, _MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
_MOUSEEVENTF_WHEEL: Final = 0x0800
_BUTTON_FLAGS: Final = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

HookProc = Callable[[int, int, int], int]
"""(code, wparam, lparam) -> result, as Windows calls a low-level hook."""


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """What a low-level keyboard hook receives for one key."""

    vk: int
    scancode: int
    flags: int
    time: int
    extra: int


@dataclass(frozen=True, slots=True)
class Hook:
    """An installed hook. Holds the callback so it stays alive while installed."""

    handle: int
    callback: object


@dataclass(frozen=True, slots=True)
class KeyStroke:
    """One key going down or up, by virtual key."""

    vk: int
    down: bool
    extended: bool = False


def read_key_event(lparam: int) -> KeyEvent:
    kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
    return KeyEvent(int(kb.vkCode), int(kb.scanCode), int(kb.flags), int(kb.time), int(kb.dwExtraInfo))


def set_keyboard_hook(proc: HookProc) -> Hook | None:
    callback = HOOKPROC(proc)
    handle: int | None = _SetWindowsHookExW(_WH_KEYBOARD_LL, callback, _GetModuleHandleW(None), 0)
    return Hook(handle, callback) if handle else None


def remove_hook(hook: Hook) -> None:
    _UnhookWindowsHookEx(hook.handle)


def call_next_hook(code: int, wparam: int, lparam: int) -> int:
    return int(_CallNextHookEx(None, code, wparam, lparam))


def scancode_for(vk: int) -> int:
    """The key's scancode, from Windows' own layout table."""
    return int(_MapVirtualKeyW(vk, _MAPVK_VK_TO_VSC))


def _key(vk: int, scancode: int, flags: int, extra: int) -> INPUT:
    return INPUT(_INPUT_KEYBOARD, INPUT_UNION(ki=KEYBDINPUT(vk, scancode, flags, 0, extra)))


def _send(events: Sequence[INPUT]) -> int:
    """All in one call, so the user's own input can't land in the middle."""
    array = (INPUT * len(events))(*events)
    sent = int(_SendInput(len(events), array, ctypes.sizeof(INPUT)))
    require_input_count(sent, len(events))
    return sent


def send_key(vk: int, scancode: int, flags: int, extra: int) -> None:
    _send([_key(vk, scancode, flags, extra)])


def send_keys(strokes: Sequence[KeyStroke], extra: int) -> None:
    """Virtual keys, with the scancodes Windows' layout gives them."""
    _send([_key(s.vk, scancode_for(s.vk), (0 if s.down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if s.extended else 0), extra) for s in strokes])


def send_scancodes(strokes: Sequence[tuple[int, bool]], extra: int) -> None:
    """Physical keys by scancode, as a keyboard would send them (used by tests)."""
    _send([_key(0, code, KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP), extra) for code, down in strokes])


def type_text(text: str, extra: int) -> None:
    """Types text as characters, whatever the keyboard layout. A newline presses Enter."""
    enter = scancode_for(_VK_RETURN)
    events: list[INPUT] = []
    for i, line in enumerate(text.split("\n")):
        if i:
            events += [_key(_VK_RETURN, enter, 0, extra), _key(_VK_RETURN, enter, KEYEVENTF_KEYUP, extra)]
        events += [_key(0, unit, KEYEVENTF_UNICODE | up, extra) for unit in utf16_units(line) for up in (0, KEYEVENTF_KEYUP)]
    if events:
        _send(events)


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    _GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def _mouse(dx: int, dy: int, data: int, flags: int, extra: int) -> INPUT:
    return INPUT(_INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(dx, dy, data, flags, 0, extra)))


def _absolute(x: int, y: int, screen: Rect) -> tuple[int, int]:
    """Mouse coordinates are 0..65535 across the whole virtual screen."""
    return (x - screen.left) * 65535 // max(screen.width - 1, 1), (y - screen.top) * 65535 // max(screen.height - 1, 1)


def move_mouse(x: int, y: int, screen: Rect, extra: int) -> None:
    dx, dy = _absolute(x, y, screen)
    _send([_mouse(dx, dy, 0, _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK, extra)])


def click(x: int, y: int, button: str, count: int, screen: Rect, extra: int) -> None:
    """Moves to (x, y) and clicks `count` times. `button`: left, right or middle."""
    down, up = _BUTTON_FLAGS[button]
    dx, dy = _absolute(x, y, screen)
    move = _mouse(dx, dy, 0, _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK, extra)
    _send([move, *(_mouse(0, 0, 0, flag, extra) for _ in range(count) for flag in (down, up))])


def mouse_button(button: str, down: bool, extra: int) -> None:
    """Presses or releases a button where the mouse is (for dragging)."""
    pressed, released = _BUTTON_FLAGS[button]
    _send([_mouse(0, 0, 0, pressed if down else released, extra)])


def scroll(clicks: int, extra: int) -> None:
    """Scrolls the wheel where the mouse is; positive is up."""
    _send([_mouse(0, 0, (clicks * 120) & 0xFFFF_FFFF, _MOUSEEVENTF_WHEEL, extra)])
