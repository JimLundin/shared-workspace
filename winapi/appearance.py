"""Desktop material preferences and bounded background observations.

Composition and Qt retain opaque text on a translucent surface. This module
reads accessibility settings and samples nearby pixels off the UI thread.
"""

import ctypes
import winreg
from concurrent.futures import Future, ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum, auto

from ._core import fn, gdi32, user32
from .shapes import Rect


class Material(Enum):
    GLASS = auto()
    SOLID = auto()


class Frost(Enum):
    LIGHT = auto()
    SMOKED = auto()
    BRIGHT = auto()


@dataclass(frozen=True, slots=True)
class Backdrop:
    bounds: Rect
    frost: Frost


class FrostSampler:
    """One bounded background read; GetPixel can wait on the desktop compositor.

    The UI submits immutable bounds and polls the result on its existing pointer
    timer. This worker never touches widgets, and neither submitting nor polling waits
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


_SystemParametersInfoW = fn(user32, "SystemParametersInfoW", wintypes.BOOL,
                           wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
_GetSysColor = fn(user32, "GetSysColor", wintypes.DWORD, ctypes.c_int)
_GetDC = fn(user32, "GetDC", wintypes.HDC, wintypes.HWND)
_ReleaseDC = fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
_GetPixel = fn(gdi32, "GetPixel", wintypes.DWORD, wintypes.HDC, ctypes.c_int, ctypes.c_int)
_winmm = ctypes.WinDLL("winmm", use_last_error=True)
_TimeBeginPeriod = fn(_winmm, "timeBeginPeriod", wintypes.UINT, wintypes.UINT)
_TimeEndPeriod = fn(_winmm, "timeEndPeriod", wintypes.UINT, wintypes.UINT)


class TimerResolution:
    """Keep the UI's short frame timers accurate only while UI motion is running.

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

    Uniform dark and bright surroundings can use a light tint with contrasting
    ink. Mixed or unavailable backgrounds need more shading. Only neighboring
    pixels are read, and no desktop image is retained.
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
    """Use light glass on uniform backgrounds; shade mixed or unknown surfaces."""
    values = tuple(luminance(color) for color in colors)
    if values and all(value < .18 for value in values):
        return Frost.LIGHT
    if values and all(value > .5 for value in values):
        return Frost.BRIGHT
    return Frost.SMOKED


def luminance(color: int) -> float:
    channels = tuple((color >> shift & 255) / 255 for shift in (0, 8, 16))
    linear = tuple(value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels)
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722), strict=True))
