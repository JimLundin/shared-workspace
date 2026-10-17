"""Typed bindings for the few Win32 functions this program uses.

This is the only module that touches ctypes. ctypes itself is untyped, so every
function here has an exact signature, and every value is converted to a concrete
type before it leaves. Nothing untyped crosses into the rest of the program.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterator
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final, NewType

# ---- Structures ----------------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


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
    """Unused, but part of INPUT: without it the union is too small for Windows."""

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

# ---- Functions -----------------------------------------------------------------------

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_fn = ctypes.WINFUNCTYPE
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_SetWindowsHookExW = _fn(wintypes.HHOOK, ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)(("SetWindowsHookExW", _user32))
_CallNextHookEx = _fn(LRESULT, wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)(("CallNextHookEx", _user32))
_UnhookWindowsHookEx = _fn(wintypes.BOOL, wintypes.HHOOK)(("UnhookWindowsHookEx", _user32))
_GetMessageW = _fn(wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)(("GetMessageW", _user32))
_PostQuitMessage = _fn(None, ctypes.c_int)(("PostQuitMessage", _user32))
_SendInput = _fn(wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)(("SendInput", _user32))
_MapVirtualKeyW = _fn(wintypes.UINT, wintypes.UINT, wintypes.UINT)(("MapVirtualKeyW", _user32))
_SetTimer = _fn(ULONG_PTR, wintypes.HWND, ULONG_PTR, wintypes.UINT, ctypes.c_void_p)(("SetTimer", _user32))
_KillTimer = _fn(wintypes.BOOL, wintypes.HWND, ULONG_PTR)(("KillTimer", _user32))
_MessageBeep = _fn(wintypes.BOOL, wintypes.UINT)(("MessageBeep", _user32))
_MessageBoxW = _fn(ctypes.c_int, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)(("MessageBoxW", _user32))
_CreateMutexW = _fn(wintypes.HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR, use_last_error=True)(("CreateMutexW", _kernel32))
_CloseHandle = _fn(wintypes.BOOL, wintypes.HANDLE)(("CloseHandle", _kernel32))
_GetModuleHandleW = _fn(wintypes.HMODULE, wintypes.LPCWSTR)(("GetModuleHandleW", _kernel32))

WM_TIMER: Final = 0x0113
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_EXTENDED: Final = 0x01
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
MB_OK, MB_ICONHAND, MB_ICONASTERISK = 0x00, 0x10, 0x40
_WH_KEYBOARD_LL: Final = 13
_INPUT_KEYBOARD: Final = 1
_MAPVK_VK_TO_VSC: Final = 0
_ERROR_ALREADY_EXISTS: Final = 183

# ---- Typed API -----------------------------------------------------------------------

HookProc = Callable[[int, int, int], int]
"""(code, wparam, lparam) -> result, as Windows calls a low-level hook."""

Lock = NewType("Lock", int)


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
class Message:
    message: int
    wparam: int


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


def messages() -> Iterator[Message]:
    """Waits for and yields this thread's messages, until a quit message arrives."""
    msg = wintypes.MSG()
    while _GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        yield Message(int(msg.message), int(msg.wParam))


def post_quit() -> None:
    _PostQuitMessage(0)


def send_key(vk: int, scancode: int, flags: int, extra: int) -> None:
    event = INPUT(_INPUT_KEYBOARD, INPUT_UNION(ki=KEYBDINPUT(vk, scancode, flags, 0, extra)))
    _SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


def scancode_for(vk: int) -> int:
    """The key's scancode, from Windows' own layout table."""
    return int(_MapVirtualKeyW(vk, _MAPVK_VK_TO_VSC))


def set_timer(ms: int) -> int:
    return int(_SetTimer(None, 0, ms, None))


def kill_timer(timer: int) -> None:
    _KillTimer(None, timer)


def beep(sound: int) -> None:
    """A Windows system sound; it plays in the background."""
    _MessageBeep(sound)


def message_box(text: str, title: str, style: int) -> None:
    _MessageBoxW(None, text, title, style)


def create_lock(name: str) -> Lock | None:
    """A named lock for this Windows session; None if another process holds it."""
    handle: int | None = _CreateMutexW(None, False, name)
    if handle and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _CloseHandle(handle)
        return None
    return Lock(handle) if handle else None


def release_lock(lock: Lock) -> None:
    _CloseHandle(lock)
