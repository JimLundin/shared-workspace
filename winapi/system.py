"""Message loops, timers, events, locks, sounds and message boxes."""

from __future__ import annotations

import ctypes
from collections.abc import Iterator, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final, NewType

from ._core import ULONG_PTR, fn, kernel32, last_error, user32

WM_NULL: Final = 0x0000
WM_DESTROY: Final = 0x0002
WM_CLOSE: Final = 0x0010
WM_COMMAND: Final = 0x0111
WM_TIMER: Final = 0x0113
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_APP: Final = 0x8000

MB_OK, MB_YESNO = 0x00, 0x04
MB_ICONHAND, MB_ICONQUESTION, MB_ICONEXCLAMATION, MB_ICONASTERISK = 0x10, 0x20, 0x30, 0x40
MB_SETFOREGROUND, MB_TOPMOST = 0x0001_0000, 0x0004_0000
IDYES: Final = 6

_ERROR_ALREADY_EXISTS: Final = 183
_INFINITE: Final = 0xFFFF_FFFF

_GetMessageW = fn(user32, "GetMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_TranslateMessage = fn(user32, "TranslateMessage", wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
_DispatchMessageW = fn(user32, "DispatchMessageW", ULONG_PTR, ctypes.POINTER(wintypes.MSG))
_PostQuitMessage = fn(user32, "PostQuitMessage", None, ctypes.c_int)
_PostMessageW = fn(user32, "PostMessageW", wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_PostThreadMessageW = fn(user32, "PostThreadMessageW", wintypes.BOOL, wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_SetTimer = fn(user32, "SetTimer", ULONG_PTR, wintypes.HWND, ULONG_PTR, wintypes.UINT, ctypes.c_void_p)
_KillTimer = fn(user32, "KillTimer", wintypes.BOOL, wintypes.HWND, ULONG_PTR)
_MessageBeep = fn(user32, "MessageBeep", wintypes.BOOL, wintypes.UINT)
_MessageBoxW = fn(user32, "MessageBoxW", ctypes.c_int, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
_GetCurrentThreadId = fn(kernel32, "GetCurrentThreadId", wintypes.DWORD)
_CreateMutexW = fn(kernel32, "CreateMutexW", wintypes.HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_CloseHandle = fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_CreateEventW = fn(kernel32, "CreateEventW", wintypes.HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_SetEvent = fn(kernel32, "SetEvent", wintypes.BOOL, wintypes.HANDLE)
_WaitForMultipleObjects = fn(
    kernel32, "WaitForMultipleObjects", wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD
)

Lock = NewType("Lock", int)
EventHandle = NewType("EventHandle", int)


@dataclass(frozen=True, slots=True)
class Message:
    hwnd: int
    message: int
    wparam: int
    lparam: int


def messages() -> Iterator[Message]:
    """Waits for and yields this thread's messages (without dispatching them to a
    window procedure), until a quit message arrives."""
    msg = wintypes.MSG()
    while _GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        yield Message(int(msg.hWnd or 0), int(msg.message), int(msg.wParam), int(msg.lParam))


def run_message_loop() -> None:
    """Dispatches this thread's messages to their windows until a quit message."""
    msg = wintypes.MSG()
    while _GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        _TranslateMessage(ctypes.byref(msg))
        _DispatchMessageW(ctypes.byref(msg))


def post_quit() -> None:
    _PostQuitMessage(0)


def post_message(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
    """Queues a message for a window (any process's); doesn't wait for it."""
    return bool(_PostMessageW(hwnd, message, wparam, lparam))


def post_thread_message(thread: int, message: int, wparam: int, lparam: int) -> bool:
    return bool(_PostThreadMessageW(thread, message, wparam, lparam))


def thread_id() -> int:
    return int(_GetCurrentThreadId())


def set_timer(ms: int) -> int:
    """A timer for this thread: a WM_TIMER message, every `ms`, with the returned id."""
    return int(_SetTimer(None, 0, ms, None))


def kill_timer(timer: int) -> None:
    _KillTimer(None, timer)


def beep(sound: int) -> None:
    """A Windows system sound (an MB_ICON* value, or MB_OK); it plays in the background."""
    _MessageBeep(sound)


def message_box(text: str, title: str, style: int) -> int:
    """Blocks until dismissed; returns the button pressed (IDYES...)."""
    return int(_MessageBoxW(None, text, title, style))


def ask_yes_no(text: str, title: str) -> bool:
    """A Yes/No question on top of everything; Yes is True."""
    style = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
    return message_box(text, title, style) == IDYES


def create_lock(name: str) -> Lock | None:
    """A named lock for this Windows session; None if another process holds it."""
    handle: int | None = _CreateMutexW(None, False, name)
    if handle and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _CloseHandle(handle)
        return None
    return Lock(handle) if handle else None


def release_lock(lock: Lock) -> None:
    _CloseHandle(lock)


def create_event() -> EventHandle:
    """An auto-reset event, private to this program."""
    handle: int | None = _CreateEventW(None, False, False, None)
    if not handle:
        raise last_error()
    return EventHandle(handle)


def set_event(event: EventHandle) -> None:
    _SetEvent(event)


def close_event(event: EventHandle) -> None:
    _CloseHandle(event)


def wait_any(events: Sequence[EventHandle], timeout_ms: int | None = None) -> int | None:
    """Blocks until one of the events is set; its index, or None on timeout."""
    handles = (wintypes.HANDLE * len(events))(*events)
    index = int(_WaitForMultipleObjects(len(events), handles, False, _INFINITE if timeout_ms is None else timeout_ms))
    if index < len(events):
        return index
    if index == 0x102:  # WAIT_TIMEOUT
        return None
    raise last_error()
