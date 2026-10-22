"""Desktop material and motion, used only by the assistant's UI process.

Windows composes the acrylic behind a color-keyed Tk window. The background is
translucent while foreground pixels keep their opacity; no desktop capture or
per-frame bitmap copying is needed. Unsupported systems use the solid palette.
"""

import ctypes
import winreg
from concurrent.futures import Future, ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum, auto

from ._core import dwmapi, fn, gdi32, optional_fn, user32
from .shapes import Rect


class Material(Enum):
    GLASS = auto()
    SOLID = auto()


class Frost(Enum):
    LIGHT = auto()
    SMOKED = auto()


@dataclass(frozen=True, slots=True)
class Backdrop:
    bounds: Rect
    frost: Frost


class FrostSampler:
    """One bounded background read; GetPixel can wait on the desktop compositor.

    The UI submits immutable bounds and polls the result on its existing pointer
    timer. This worker never touches Tk, and neither submitting nor polling waits
    for desktop pixels. The owner discards observations after a card moves.
    """

    __slots__ = ("pending", "worker")

    def __init__(self) -> None:
        self.worker: ThreadPoolExecutor | None = None
        self.pending: Future[tuple[Backdrop, ...]] | None = None

    def submit(self, bounds: tuple[Rect, ...]) -> None:
        if not bounds or self.pending is not None:
            return
        if self.worker is None:
            self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="toolkit-glass")
        self.pending = self.worker.submit(sample_backgrounds, bounds)

    def read(self) -> tuple[Backdrop, ...]:
        if self.pending is None or not self.pending.done():
            return ()
        completed, self.pending = self.pending, None
        return completed.result()

    def close(self) -> None:
        if self.worker is not None:
            self.worker.shutdown(wait=False, cancel_futures=True)
            self.worker = None
        self.pending = None


def sample_backgrounds(bounds: tuple[Rect, ...]) -> tuple[Backdrop, ...]:
    return tuple(Backdrop(rect, background_frost(rect)) for rect in bounds)


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
_GetDC = fn(user32, "GetDC", wintypes.HDC, wintypes.HWND)
_ReleaseDC = fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
_GetPixel = fn(gdi32, "GetPixel", wintypes.DWORD, wintypes.HDC, ctypes.c_int, ctypes.c_int)
_SetWindowCompositionAttribute = optional_fn(user32, "SetWindowCompositionAttribute", wintypes.BOOL,
                                            wintypes.HWND, ctypes.POINTER(_Composition))
_DwmSetWindowAttribute = fn(dwmapi, "DwmSetWindowAttribute", ctypes.c_long,
                          wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
_CreateRoundRectRgn = fn(gdi32, "CreateRoundRectRgn", wintypes.HRGN,
                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_SetWindowRgn = fn(user32, "SetWindowRgn", ctypes.c_int, wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
_DeleteObject = fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
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


def background_frost(bounds: Rect) -> Frost:
    """Sample four surrounding pixels, off the animation path; retain no image.

    Native acrylic's extra luminosity layer is useful over a light application,
    but flattens dark desktops to gray. Sample outside our own glass and shadow
    so changing the material cannot feed back into the next observation.
    """
    dc = _GetDC(None)
    if not dc:
        return Frost.SMOKED
    # Sample the sides: above/below can contain a neighboring conversation bubble.
    points = tuple((x, bounds.top + bounds.height * third // 3)
                   for x in (bounds.left - 12, bounds.right + 12) for third in (1, 2))
    colors: list[int] = []
    try:
        for px, py in points:
            color = int(_GetPixel(dc, px, py))
            if color != 0xFFFF_FFFF:
                colors.append(color)
    finally:
        _ReleaseDC(None, dc)
    return frost_for(tuple(colors))


def frost_for(colors: tuple[int, ...]) -> Frost:
    """Keep dark backgrounds transparent; shade bright or unavailable backgrounds."""
    return Frost.LIGHT if colors and all(luminance(color) < .18 for color in colors) else Frost.SMOKED


def luminance(color: int) -> float:
    channels = tuple((color >> shift & 255) / 255 for shift in (0, 8, 16))
    linear = tuple(value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels)
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722), strict=True))


def round_corners(hwnd: int, width: int, height: int, radius: int) -> None:
    """Round the hit region and solid fallback; DWM chooses glass's visual corners."""
    diameter = max(2, min(radius * 2, width, height))
    region = _CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter)
    if region and not _SetWindowRgn(hwnd, region, True):
        _DeleteObject(region)  # Windows owns the region only after a successful call.


def material(hwnd: int, surface: str, edge: str, *, glass: bool, frost: Frost = Frost.SMOKED) -> bool:
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
    # A 22% tint restores the light, colored material in the accepted preview.
    # The extra luminosity layer is reserved for bright surroundings. This is
    # tint opacity, not whole-window opacity: text and controls stay opaque.
    accent = _Accent(4 if glass else 0, 2 if frost is Frost.SMOKED else 0, 0x3800_0000 | colorref(surface), 0)
    data = _Composition(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
    return bool(_SetWindowCompositionAttribute(hwnd, ctypes.byref(data)) and glass)
