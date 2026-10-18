"""Other programs' windows: listing, focusing, moving; and hidden windows of our own
that other processes can find and post messages to."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Final

from ._core import dwmapi, fn, kernel32, rect, user32
from .shapes import Rect

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_EnumWindows = fn(user32, "EnumWindows", wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
_IsWindowVisible = fn(user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
_IsWindow = fn(user32, "IsWindow", wintypes.BOOL, wintypes.HWND)
_IsIconic = fn(user32, "IsIconic", wintypes.BOOL, wintypes.HWND)
_IsZoomed = fn(user32, "IsZoomed", wintypes.BOOL, wintypes.HWND)
_GetWindowTextLengthW = fn(user32, "GetWindowTextLengthW", ctypes.c_int, wintypes.HWND)
_GetWindowTextW = fn(user32, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowThreadProcessId = fn(user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_GetWindowLongW = fn(user32, "GetWindowLongW", wintypes.LONG, wintypes.HWND, ctypes.c_int)
_GetWindow = fn(user32, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowRect = fn(user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_GetForegroundWindow = fn(user32, "GetForegroundWindow", wintypes.HWND)
_SetForegroundWindow = fn(user32, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND)
_BringWindowToTop = fn(user32, "BringWindowToTop", wintypes.BOOL, wintypes.HWND)
_AttachThreadInput = fn(user32, "AttachThreadInput", wintypes.BOOL, wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
_ShowWindow = fn(user32, "ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_SetWindowPos = fn(
    user32, "SetWindowPos", wintypes.BOOL, wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
)
_FindWindowW = fn(user32, "FindWindowW", wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_CreateWindowExW = fn(
    user32,
    "CreateWindowExW",
    wintypes.HWND,
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
)
_DestroyWindow = fn(user32, "DestroyWindow", wintypes.BOOL, wintypes.HWND)
_GetCurrentThreadId = fn(kernel32, "GetCurrentThreadId", wintypes.DWORD)
_GetModuleHandleW = fn(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
_OpenProcess = fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_QueryFullProcessImageNameW = fn(
    kernel32, "QueryFullProcessImageNameW", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
)
_CloseHandle = fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_DwmGetWindowAttribute = fn(dwmapi, "DwmGetWindowAttribute", ctypes.c_long, wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
_GWL_EXSTYLE: Final = -20
_WS_EX_TOOLWINDOW: Final = 0x0000_0080
_WS_POPUP: Final = 0x8000_0000
_GW_OWNER: Final = 4
_PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
_DWMWA_EXTENDED_FRAME_BOUNDS, _DWMWA_CLOAKED = 9, 14
_SWP_NOZORDER, _SWP_NOACTIVATE = 0x0004, 0x0010


@dataclass(frozen=True, slots=True)
class Window:
    """A top-level window, as the taskbar would show it."""

    handle: int
    title: str
    exe: str
    """The program's file name, lowercase: `windowsterminal.exe`."""
    minimized: bool


def _title(hwnd: int) -> str:
    length = int(_GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    _GetWindowTextW(hwnd, buffer, length + 1)
    return str(buffer.value)


def _exe(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return ""
        return PureWindowsPath(str(buffer.value)).name.lower()
    finally:
        _CloseHandle(process)


def _cloaked(hwnd: int) -> bool:
    """Hidden by the shell (suspended Store apps, other virtual desktops)."""
    value = wintypes.DWORD()
    return _DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)) == 0 and bool(value.value)


def _on_taskbar(hwnd: int) -> bool:
    if not _IsWindowVisible(hwnd) or _GetWindow(hwnd, _GW_OWNER) or _cloaked(hwnd):
        return False
    return not (int(_GetWindowLongW(hwnd, _GWL_EXSTYLE)) & _WS_EX_TOOLWINDOW) and _GetWindowTextLengthW(hwnd) > 0


def windows() -> list[Window]:
    """Top-level windows that would be on the taskbar, front to back."""
    handles: list[int] = []

    def each(hwnd: int, _data: int) -> bool:
        if _on_taskbar(hwnd):
            handles.append(int(hwnd))
        return True

    _EnumWindows(WNDENUMPROC(each), 0)
    return [Window(h, _title(h), _exe(h), bool(_IsIconic(h))) for h in handles]


def exists(hwnd: int) -> bool:
    return bool(_IsWindow(hwnd))


def foreground() -> int:
    return int(_GetForegroundWindow() or 0)


def focus(hwnd: int) -> bool:
    """Brings a window to the front and gives it the keyboard; True if it worked."""
    if _IsIconic(hwnd):
        _ShowWindow(hwnd, SW_RESTORE)
    if _SetForegroundWindow(hwnd) and foreground() == hwnd:
        return True
    # Windows only lets the program the user is using change the foreground window.
    # Joining its input queue for a moment makes this thread count as that program.
    current = _GetWindowThreadProcessId(foreground(), None)
    ours = _GetCurrentThreadId()
    attached = bool(current and current != ours and _AttachThreadInput(ours, current, True))
    try:
        _BringWindowToTop(hwnd)
        _SetForegroundWindow(hwnd)
    finally:
        if attached:
            _AttachThreadInput(ours, current, False)
    return foreground() == hwnd


def title(hwnd: int) -> str:
    return _title(hwnd)


def exe(hwnd: int) -> str:
    return _exe(hwnd)


def show(hwnd: int, command: int) -> None:
    """SW_MAXIMIZE, SW_MINIMIZE or SW_RESTORE."""
    _ShowWindow(hwnd, command)


def is_maximized(hwnd: int) -> bool:
    return bool(_IsZoomed(hwnd))


def outer_rect(hwnd: int) -> Rect:
    """The rectangle Windows positions the window by (it includes invisible borders)."""
    r = wintypes.RECT()
    _GetWindowRect(hwnd, ctypes.byref(r))
    return rect(r)


def visible_rect(hwnd: int) -> Rect:
    """The rectangle the window is actually drawn in."""
    r = wintypes.RECT()
    if _DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        return outer_rect(hwnd)
    return rect(r)


def move(hwnd: int, to: Rect) -> None:
    """Positions by the outer rectangle (see `outer_rect`)."""
    _SetWindowPos(hwnd, None, to.left, to.top, to.width, to.height, _SWP_NOZORDER | _SWP_NOACTIVATE)


def find(title: str) -> int | None:
    """A top-level window by its exact title, hidden ones included."""
    hwnd: int | None = _FindWindowW(None, title)
    return int(hwnd) if hwnd else None


def create_hidden(title: str) -> int:
    """A window that is never shown: something other processes can `find` and post
    messages to. The messages arrive in this thread's message loop."""
    hwnd: int | None = _CreateWindowExW(0, "STATIC", title, _WS_POPUP, 0, 0, 0, 0, None, None, _GetModuleHandleW(None), None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(hwnd)


def destroy(hwnd: int) -> None:
    _DestroyWindow(hwnd)
