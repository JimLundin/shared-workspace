"""Desktop material and motion, used only by the assistant's UI process.

Windows composes the acrylic behind a color-keyed Tk window. The background is
translucent while foreground pixels keep their opacity; no desktop capture or
per-frame bitmap copying is needed. Unsupported systems use the solid palette.
"""

import ctypes
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum, auto

from ._core import dwmapi, fn, optional_fn, user32


class Material(Enum):
    GLASS = auto()
    SOLID = auto()


@dataclass(frozen=True, slots=True)
class Contrast:
    background: str
    foreground: str


type Preference = Material | Contrast


class _HighContrast(ctypes.Structure):
    _fields_ = [("size", wintypes.UINT), ("flags", wintypes.DWORD), ("scheme", wintypes.LPWSTR)]


class _Accent(ctypes.Structure):
    _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int), ("color", wintypes.DWORD), ("animation", ctypes.c_int)]


class _Composition(ctypes.Structure):
    _fields_ = [("attribute", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]

_SystemParametersInfoW = fn(user32, "SystemParametersInfoW", wintypes.BOOL,
                           wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
_GetSysColor = fn(user32, "GetSysColor", wintypes.DWORD, ctypes.c_int)
_SetWindowCompositionAttribute = optional_fn(user32, "SetWindowCompositionAttribute", wintypes.BOOL,
                                            wintypes.HWND, ctypes.POINTER(_Composition))
_DwmSetWindowAttribute = fn(dwmapi, "DwmSetWindowAttribute", ctypes.c_long,
                          wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
_winmm = ctypes.WinDLL("winmm", use_last_error=True)
_TimeBeginPeriod = fn(_winmm, "timeBeginPeriod", wintypes.UINT, wintypes.UINT)
_TimeEndPeriod = fn(_winmm, "timeEndPeriod", wintypes.UINT, wintypes.UINT)


class TimerResolution:
    """Keep Tk's short frame timers accurate only while UI motion is running.

    Windows' default timer granularity can turn a 16 ms wait into 31 ms. Each
    successful request is balanced when movement ends, history opens or the
    view closes; idle notifications and the keyboard process never request it.
    """

    __slots__ = ("active",)

    def __init__(self) -> None:
        self.active = False

    def update(self, active: bool) -> None:
        if active and not self.active:
            self.active = _TimeBeginPeriod(1) == 0
        elif not active and self.active:
            _TimeEndPeriod(1)
            self.active = False


def animations_enabled() -> bool:
    enabled = wintypes.BOOL()
    return bool(_SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0) and enabled.value)  # SPI_GETCLIENTAREAANIMATION


def preference() -> Preference:
    contrast = _HighContrast(ctypes.sizeof(_HighContrast), 0, None)
    if not _SystemParametersInfoW(0x0042, contrast.size, ctypes.byref(contrast), 0):  # SPI_GETHIGHCONTRAST
        return Material.SOLID
    if contrast.flags & 1:
        return Contrast(system_color(5), system_color(8))  # COLOR_WINDOW, COLOR_WINDOWTEXT
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            enabled, _kind = winreg.QueryValueEx(key, "EnableTransparency")
    except OSError:
        return Material.SOLID
    return Material.GLASS if enabled == 1 else Material.SOLID


def system_color(index: int) -> str:
    value = int(_GetSysColor(index))
    return f"#{value & 255:02x}{value >> 8 & 255:02x}{value >> 16 & 255:02x}"


def colorref(color: str) -> int:
    return int.from_bytes(bytes.fromhex(color.removeprefix("#")), "little")


def material(hwnd: int, surface: str, edge: str, *, glass: bool) -> bool:
    """Apply native smoke and rounded edges; return whether glass was available.

    Accent policy is optional on older Windows. Always disable it when selecting
    a solid/high-contrast surface, including on already visible cards.
    """
    rounded = ctypes.c_int(2)  # DWMWCP_ROUND
    _DwmSetWindowAttribute(hwnd, 33, ctypes.byref(rounded), ctypes.sizeof(rounded))
    border = wintypes.DWORD(colorref(edge))
    _DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
    if _SetWindowCompositionAttribute is None:
        return False
    accent = _Accent(4 if glass else 0, 2, 0xCC00_0000 | colorref(surface), 0)
    data = _Composition(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
    return bool(_SetWindowCompositionAttribute(hwnd, ctypes.byref(data)) and glass)
