"""Other programs' windows: listing, focusing, moving; and hidden windows of our own
that other processes can find and post messages to."""

import ctypes
import threading
from ctypes import wintypes
from pathlib import PureWindowsPath
from typing import Final

from ._core import LRESULT, ULONG_PTR, comctl32, dwmapi, fn, kernel32, rect, user32
from .shapes import Rect, Window

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
_SetWindowLongW = fn(user32, "SetWindowLongW", wintypes.LONG, wintypes.HWND, ctypes.c_int, wintypes.LONG)
_GetWindow = fn(user32, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowRect = fn(user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_GetClientRect = fn(user32, "GetClientRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_GetDpiForWindow = fn(user32, "GetDpiForWindow", wintypes.UINT, wintypes.HWND)
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
_DwmSetWindowAttribute = fn(dwmapi, "DwmSetWindowAttribute", ctypes.c_long, wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
_SUBCLASSPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, ULONG_PTR, ULONG_PTR)
_SetWindowSubclass = fn(comctl32, "SetWindowSubclass", wintypes.BOOL, wintypes.HWND, _SUBCLASSPROC, ULONG_PTR, ULONG_PTR)
_RemoveWindowSubclass = fn(comctl32, "RemoveWindowSubclass", wintypes.BOOL, wintypes.HWND, _SUBCLASSPROC, ULONG_PTR)
_DefSubclassProc = fn(comctl32, "DefSubclassProc", LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
_GWL_EXSTYLE: Final = -20
_WS_EX_TOOLWINDOW: Final = 0x0000_0080
_WS_POPUP: Final = 0x8000_0000
_WS_EX_TRANSPARENT, _WS_EX_LAYERED, _WS_EX_NOACTIVATE = 0x0000_0020, 0x0008_0000, 0x0800_0000
_GW_OWNER: Final = 4
_PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
_DWMWA_EXTENDED_FRAME_BOUNDS, _DWMWA_CLOAKED = 9, 14
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOZORDER, _SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
_SW_HIDE, _SW_SHOWNOACTIVATE = 0, 4
_HWND_TOPMOST: Final = -1
_WM_MOUSEACTIVATE, _WM_NCDESTROY = 0x0021, 0x0082
_MA_NOACTIVATE: Final = 3


def overlay_message(hwnd: int, message: int, wparam: int, lparam: int, subclass: int, _data: int) -> int:
    """Keep an interactive overlay from activating, even if its toolkit (Tk) asks to."""
    if message == _WM_MOUSEACTIVATE:
        return _MA_NOACTIVATE
    if message == _WM_NCDESTROY:
        _RemoveWindowSubclass(hwnd, _OVERLAY_PROC, subclass)
    return int(_DefSubclassProc(hwnd, message, wparam, lparam))


_OVERLAY_PROC = _SUBCLASSPROC(overlay_message)  # keep the native callback alive until every overlay is destroyed


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


def process_id(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def focus(hwnd: int, timeout_s: float = 1.0) -> bool:
    """Brings a window to the front and gives it the keyboard; True if it worked.

    Done on a helper thread with a time limit: briefly joining the front program's
    input queue (see `_focus`) waits on that program, and a hung one must never
    freeze the caller."""
    result: list[bool] = []
    helper = threading.Thread(target=lambda: result.append(_focus(hwnd)), name="focus", daemon=True)
    helper.start()
    helper.join(timeout_s)
    return bool(result and result[0])


def _focus(hwnd: int) -> bool:
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


def is_minimized(hwnd: int) -> bool:
    return bool(_IsIconic(hwnd))


def is_visible(hwnd: int) -> bool:
    return bool(_IsWindowVisible(hwnd))


def client_size(hwnd: int) -> tuple[int, int]:
    r = wintypes.RECT()
    _GetClientRect(hwnd, ctypes.byref(r))
    return int(r.right - r.left), int(r.bottom - r.top)


def scale(hwnd: int) -> float:
    return float(_GetDpiForWindow(hwnd) or 96) / 96


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


def make_overlay(hwnd: int, *, click_through: bool = True) -> None:
    """An overlay that never takes the keyboard or appears on the taskbar/Alt+Tab.
    Clicks pass through by default; a drag handle can opt into mouse input."""
    style = int(_GetWindowLongW(hwnd, _GWL_EXSTYLE)) | _WS_EX_LAYERED | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
    style = style | _WS_EX_TRANSPARENT if click_through else style & ~_WS_EX_TRANSPARENT
    _SetWindowLongW(hwnd, _GWL_EXSTYLE, ctypes.c_long(style & 0xFFFF_FFFF).value)
    if not click_through and not _SetWindowSubclass(hwnd, _OVERLAY_PROC, 0, 0):
        raise OSError("Could not keep the overlay handle from taking keyboard focus.")


def show_quietly(hwnd: int, visible: bool, *, topmost: bool = True) -> None:
    """Shows or hides without activating. Overlays stay on top by default."""
    if visible:
        _ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        _SetWindowPos(hwnd, _HWND_TOPMOST if topmost else -2, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
    else:
        _ShowWindow(hwnd, _SW_HIDE)


def allow_activation(hwnd: int, allowed: bool) -> None:
    """Temporarily prevent a GUI toolkit's Show call from taking the keyboard."""
    style = int(_GetWindowLongW(hwnd, _GWL_EXSTYLE))
    style = style & ~_WS_EX_NOACTIVATE if allowed else style | _WS_EX_NOACTIVATE
    _SetWindowLongW(hwnd, _GWL_EXSTYLE, ctypes.c_long(style & 0xFFFF_FFFF).value)


def dark_titlebar(hwnd: int) -> None:
    """Match a dark client area on Windows 10/11; older versions keep their default."""
    enabled = wintypes.BOOL(True)
    if _DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)) != 0:
        _DwmSetWindowAttribute(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))


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
