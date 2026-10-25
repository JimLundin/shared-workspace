"""Leased desktop effects: appbar space, hotkeys, DWM previews and taskbar visibility.

Every resource is released explicitly. Explorer remains the Windows shell.
"""

import ctypes
from collections.abc import Callable
from ctypes import wintypes

from ._core import dwmapi, fn, kernel32, rect, shell32, user32
from .shapes import Rect


class _AppBarData(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


class _ThumbnailProperties(ctypes.Structure):
    _fields_ = [("dwFlags", wintypes.DWORD), ("rcDestination", wintypes.RECT), ("rcSource", wintypes.RECT),
                ("opacity", ctypes.c_ubyte), ("fVisible", wintypes.BOOL), ("fSourceClientAreaOnly", wintypes.BOOL)]


_AppBarMessage = fn(shell32, "SHAppBarMessage", ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(_AppBarData))
_RegisterHotKey = fn(user32, "RegisterHotKey", wintypes.BOOL, wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_UnregisterHotKey = fn(user32, "UnregisterHotKey", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_DwmRegisterThumbnail = fn(dwmapi, "DwmRegisterThumbnail", ctypes.c_long, wintypes.HWND, wintypes.HWND, ctypes.POINTER(wintypes.HANDLE))
_DwmUnregisterThumbnail = fn(dwmapi, "DwmUnregisterThumbnail", ctypes.c_long, wintypes.HANDLE)
_DwmUpdateThumbnailProperties = fn(dwmapi, "DwmUpdateThumbnailProperties", ctypes.c_long, wintypes.HANDLE, ctypes.POINTER(_ThumbnailProperties))
_DwmQueryThumbnailSourceSize = fn(dwmapi, "DwmQueryThumbnailSourceSize", ctypes.c_long, wintypes.HANDLE, ctypes.POINTER(wintypes.SIZE))
_FindWindowEx = fn(user32, "FindWindowExW", wintypes.HWND, wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_IsWindowVisible = fn(user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
_ShowWindow = fn(user32, "ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_OpenProcess = fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_WaitForSingleObject = fn(kernel32, "WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
_CloseHandle = fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)

WM_HOTKEY = 0x0312
LAUNCHER_KEY, CANVAS_KEY = 0x5401, 0x5402


class AppBar:
    __slots__ = ("_data", "_registered")

    def __init__(self, hwnd: int) -> None:
        self._data = _AppBarData(cbSize=ctypes.sizeof(_AppBarData), hWnd=hwnd, uEdge=1)
        self._registered = bool(_AppBarMessage(0, ctypes.byref(self._data)))
        if not self._registered:
            raise OSError("Windows could not reserve space for the Toolkit bar.")

    def place(self, monitor: Rect, height: int) -> Rect:
        self._data.rc = wintypes.RECT(monitor.left, monitor.top, monitor.right, monitor.bottom)
        _AppBarMessage(2, ctypes.byref(self._data))
        self._data.rc.bottom = self._data.rc.top + height
        _AppBarMessage(3, ctypes.byref(self._data))
        return rect(self._data.rc)

    def close(self) -> None:
        if self._registered:
            _AppBarMessage(1, ctypes.byref(self._data))
            self._registered = False


class Hotkeys:
    __slots__ = ("hwnd", "registered")

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self.registered: tuple[int, ...] = tuple(identity for identity, key in ((LAUNCHER_KEY, 0x20), (CANVAS_KEY, 0x44))
                                                if _RegisterHotKey(hwnd, identity, 0x4003, key))  # Ctrl+Alt, no repeat

    def close(self) -> None:
        for identity in self.registered:
            _UnregisterHotKey(self.hwnd, identity)
        self.registered = ()


class Thumbnail:
    __slots__ = ("handle",)

    def __init__(self, destination: int, source: int) -> None:
        handle = wintypes.HANDLE()
        if _DwmRegisterThumbnail(destination, source, ctypes.byref(handle)) != 0:
            raise OSError("A live preview is unavailable for this window.")
        self.handle = int(handle.value or 0)

    def show(self, destination: Rect | None) -> None:
        properties = _ThumbnailProperties(dwFlags=0x1 | 0x4 | 0x8 | 0x10, opacity=255,
                                          fVisible=destination is not None, fSourceClientAreaOnly=False)
        if destination is not None:
            size = wintypes.SIZE()
            if _DwmQueryThumbnailSourceSize(self.handle, ctypes.byref(size)) == 0 and size.cx > 0 and size.cy > 0:
                ratio = min(destination.width / size.cx, destination.height / size.cy)
                width, height = round(size.cx * ratio), round(size.cy * ratio)
                left, top = destination.left + (destination.width - width) // 2, destination.top + (destination.height - height) // 2
                properties.rcDestination = wintypes.RECT(left, top, left + width, top + height)
            else:
                properties.rcDestination = wintypes.RECT(destination.left, destination.top, destination.right, destination.bottom)
        if _DwmUpdateThumbnailProperties(self.handle, ctypes.byref(properties)) != 0:
            raise OSError("The window preview is no longer available.")

    def close(self) -> None:
        if self.handle:
            _DwmUnregisterThumbnail(self.handle)
            self.handle = 0


def taskbars() -> tuple[int, ...]:
    handles: list[int] = []
    for name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        previous = 0
        while found := _FindWindowEx(0, previous, name, None):
            previous = int(found)
            handles.append(previous)
    return tuple(handles)


def visible(hwnd: int) -> bool:
    return bool(_IsWindowVisible(hwnd))


def taskbar_visible(hwnd: int, *, show: bool) -> None:
    if hwnd in taskbars():
        _ShowWindow(hwnd, 4 if show else 0)


def wait_for_exit(pid: int, ready: Callable[[], None] | None = None) -> None:
    """Used only by the taskbar recovery companion, never on a UI thread."""
    process = _OpenProcess(0x00100000, False, pid)
    if process:
        try:
            if ready is not None:
                ready()
            _WaitForSingleObject(process, 0xFFFFFFFF)
        finally:
            _CloseHandle(process)
