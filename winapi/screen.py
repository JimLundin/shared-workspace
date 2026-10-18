"""Monitors, DPI awareness, and capturing the screen."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final

from ._core import fn, gdi32, optional_fn, rect, user32
from .shapes import Rect


class MONITORINFO(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD))


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class BITMAPINFO(ctypes.Structure):
    _fields_ = (("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3))


MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
_EnumDisplayMonitors = fn(user32, "EnumDisplayMonitors", wintypes.BOOL, wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM)
_GetMonitorInfoW = fn(user32, "GetMonitorInfoW", wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
_MonitorFromWindow = fn(user32, "MonitorFromWindow", wintypes.HMONITOR, wintypes.HWND, wintypes.DWORD)
_GetSystemMetrics = fn(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
_SetProcessDpiAwarenessContext = optional_fn(user32, "SetProcessDpiAwarenessContext", wintypes.BOOL, ctypes.c_ssize_t)
_SetProcessDPIAware = fn(user32, "SetProcessDPIAware", wintypes.BOOL)
_GetDC = fn(user32, "GetDC", wintypes.HDC, wintypes.HWND)
_ReleaseDC = fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
_CreateCompatibleDC = fn(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
_DeleteDC = fn(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC)
_CreateDIBSection = fn(
    gdi32,
    "CreateDIBSection",
    wintypes.HBITMAP,
    wintypes.HDC,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
)
_SelectObject = fn(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_DeleteObject = fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
_SetStretchBltMode = fn(gdi32, "SetStretchBltMode", ctypes.c_int, wintypes.HDC, ctypes.c_int)
_SetBrushOrgEx = fn(gdi32, "SetBrushOrgEx", wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
_StretchBlt = fn(
    gdi32,
    "StretchBlt",
    wintypes.BOOL,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
)

_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79
_MONITORINFOF_PRIMARY: Final = 1
_MONITOR_DEFAULTTONEAREST: Final = 2
_PER_MONITOR_AWARE_V2: Final = -4
_DIB_RGB_COLORS, _HALFTONE = 0, 4
_SRCCOPY, _CAPTUREBLT = 0x00CC_0020, 0x4000_0000


@dataclass(frozen=True, slots=True)
class Monitor:
    area: Rect
    work: Rect
    """The area windows are placed in: without the taskbar."""
    primary: bool


def use_physical_pixels() -> None:
    """Makes every coordinate this process sees a real pixel, on every monitor.
    Call before creating any window."""
    if _SetProcessDpiAwarenessContext is None or not _SetProcessDpiAwarenessContext(_PER_MONITOR_AWARE_V2):
        _SetProcessDPIAware()


def virtual_screen() -> Rect:
    """The rectangle around all monitors."""
    left, top = int(_GetSystemMetrics(_SM_XVIRTUALSCREEN)), int(_GetSystemMetrics(_SM_YVIRTUALSCREEN))
    return Rect(left, top, left + int(_GetSystemMetrics(_SM_CXVIRTUALSCREEN)), top + int(_GetSystemMetrics(_SM_CYVIRTUALSCREEN)))


def _monitor(handle: int) -> Monitor:
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    _GetMonitorInfoW(handle, ctypes.byref(info))
    return Monitor(rect(info.rcMonitor), rect(info.rcWork), bool(info.dwFlags & _MONITORINFOF_PRIMARY))


def monitors() -> list[Monitor]:
    """All monitors, left to right."""
    found: list[Monitor] = []

    def each(handle: int, _dc: int, _rect: object, _data: int) -> bool:
        found.append(_monitor(handle))
        return True

    _EnumDisplayMonitors(None, None, MONITORENUMPROC(each), 0)
    return sorted(found, key=lambda m: (m.area.left, m.area.top))


def monitor_of(hwnd: int) -> Monitor:
    return _monitor(_MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST))


def capture(area: Rect, width: int, height: int) -> bytes:
    """The pixels in `area`, scaled to width x height: BGRA, top row first."""
    info = BITMAPINFO()
    info.bmiHeader = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    screen = _GetDC(None)
    memory = _CreateCompatibleDC(screen)
    bits = ctypes.c_void_p()
    bitmap = _CreateDIBSection(memory, ctypes.byref(info), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    previous = _SelectObject(memory, bitmap)
    try:
        _SetStretchBltMode(memory, _HALFTONE)  # averages pixels when shrinking, so text stays legible
        _SetBrushOrgEx(memory, 0, 0, None)
        _StretchBlt(memory, 0, 0, width, height, screen, area.left, area.top, area.width, area.height, _SRCCOPY | _CAPTUREBLT)
        return ctypes.string_at(bits, width * height * 4)
    finally:
        _SelectObject(memory, previous)
        _DeleteObject(bitmap)
        _DeleteDC(memory)
        _ReleaseDC(None, screen)
