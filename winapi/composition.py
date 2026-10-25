"""Rounded desktop glass and shadows, owned by the assistant's UI thread.

Windows composes a host backdrop through a rounded geometry. A separate,
nonactivating window places it immediately behind the per-pixel Qt foreground;
neither desktop captures nor color keys participate in rendering.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false

import ctypes
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, cast

from win32more import FAILED, WinError
from win32more.Windows.Foundation import IAsyncInfo
from win32more.Windows.Foundation.Numerics import Vector2, Vector3
from win32more.Windows.System import DispatcherQueueController
from win32more.Windows.UI import Color
from win32more.Windows.UI.Composition import Compositor
from win32more.Windows.UI.Composition.Desktop import DesktopWindowTarget
from win32more.Windows.Win32.System.WinRT import (
    RO_INIT_SINGLETHREADED,
    CreateDispatcherQueueController,
    DispatcherQueueOptions,
    RoInitialize,
    RoUninitialize,
)
from win32more.Windows.Win32.System.WinRT.Composition import ICompositorDesktopInterop

from . import windows
from ._core import dwmapi, fn, kernel32, user32
from .shapes import Rect

_DwmSetWindowAttribute = fn(dwmapi, "DwmSetWindowAttribute", ctypes.c_long,
                          ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong)

_CreateWindowExW = fn(user32, "CreateWindowExW", wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
_GetModuleHandleW = fn(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
_SetWindowPos = fn(user32, "SetWindowPos", wintypes.BOOL, wintypes.HWND, wintypes.HWND,
                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)


def _check(result: object) -> None:
    if not isinstance(result, int):
        raise TypeError("Windows returned an invalid HRESULT.")
    if FAILED(result):
        raise WinError(result)


def _color(value: str, opacity: float) -> ctypes.Structure:
    return cast(ctypes.Structure, Color(round(max(0, min(1, opacity)) * 255), *(int(value[index:index + 2], 16) for index in (1, 3, 5))))


class DesktopComposition:
    __slots__ = ("_compositor", "_interop", "_queue", "closed")

    def __init__(self) -> None:
        _check(RoInitialize(RO_INIT_SINGLETHREADED))
        self.closed = False
        self._queue: Any = DispatcherQueueController(own=True)
        try:
            _check(CreateDispatcherQueueController(DispatcherQueueOptions(ctypes.sizeof(DispatcherQueueOptions), 2, 2), self._queue))
            self._compositor: Any = Compositor()
            self._interop = self._compositor.as_(ICompositorDesktopInterop)
        except BaseException:
            RoUninitialize()
            raise

    def glass(self) -> Glass:
        return Glass(self)

    def close(self, pump: Callable[[], None]) -> None:
        """Drain the native dispatcher before its owning UI thread can exit."""
        if self.closed:
            return
        self.closed = True
        self._compositor.Close()
        shutdown = self._queue.ShutdownQueueAsync().as_(IAsyncInfo)
        deadline = time.monotonic() + 2
        while shutdown.Status == 0:
            pump()
            if time.monotonic() >= deadline:
                raise RuntimeError("The desktop composition dispatcher did not finish shutting down.")
            time.sleep(.001)
        if shutdown.Status != 1:
            raise OSError("The desktop composition dispatcher could not shut down cleanly.")
        RoUninitialize()


class Glass:
    __slots__ = (
        "backdrop", "bounds", "closed", "geometry", "group", "handle", "mask", "mask_shape", "mask_surface", "mask_visual",
        "opacity", "root", "shadow", "shadow_visual", "size", "target", "tint", "tint_brush", "visible",
    )

    def __init__(self, owner: DesktopComposition) -> None:
        # NOREDIRECTIONBITMAP lets composition supply the entire surface. It is
        # intentionally not a layered/color-keyed window.
        self.handle = int(_CreateWindowExW(0x0820_00A0, "STATIC", "Toolkit · Glass", 0x8000_0000,
                                                  0, 0, 1, 1, None, None, _GetModuleHandleW(None), None) or 0)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.closed = self.visible = False
        self.bounds: Rect | None = None
        self.size: tuple[int, int, float] | None = None
        self.opacity = 1.0
        try:
            enabled = ctypes.c_int(1)
            _check(int(_DwmSetWindowAttribute(self.handle, 17, ctypes.byref(enabled), ctypes.sizeof(enabled))))
            self.target: Any = DesktopWindowTarget(own=True)
            _check(owner._interop.CreateDesktopWindowTarget(self.handle, False, self.target))  # pyright: ignore[reportPrivateUsage]
            compositor = owner._compositor  # pyright: ignore[reportPrivateUsage]
            self.root = compositor.CreateContainerVisual()
            self.target.Root = self.root
            self.geometry = compositor.CreateRoundedRectangleGeometry()
            self.group = compositor.CreateContainerVisual()
            self.group.Clip = compositor.CreateGeometricClipWithGeometry(self.geometry)

            # The shadow uses the same rounded silhouette as the glass. Its
            # source visual is offscreen; the compositor retains the mask.
            self.mask_shape = compositor.CreateSpriteShapeWithGeometry(self.geometry)
            self.mask_shape.FillBrush = compositor.CreateColorBrushWithColor(_color("#ffffff", 1))
            self.mask_visual = compositor.CreateShapeVisual()
            self.mask_visual.Shapes.Append(self.mask_shape)
            self.mask_surface = compositor.CreateVisualSurface()
            self.mask_surface.SourceVisual = self.mask_visual
            self.mask = compositor.CreateSurfaceBrushWithSurface(self.mask_surface)
            self.shadow = compositor.CreateDropShadow()
            self.shadow.Color = _color("#000000", 1)
            self.shadow.Opacity = .28
            self.shadow.Mask = self.mask
            self.shadow_visual = compositor.CreateSpriteVisual()
            self.shadow_visual.Shadow = self.shadow
            self.root.Children.InsertAtTop(self.shadow_visual)
            self.root.Children.InsertAtTop(self.group)
            self.backdrop = compositor.CreateSpriteVisual()
            self.backdrop.Brush = compositor.CreateHostBackdropBrush()
            self.group.Children.InsertAtTop(self.backdrop)
            self.tint_brush = compositor.CreateColorBrush()
            self.tint = compositor.CreateSpriteVisual()
            self.tint.Brush = self.tint_brush
            self.group.Children.InsertAtTop(self.tint)
        except BaseException:
            windows.destroy(self.handle)
            raise

    def color(self, tint: str, opacity: float = .22) -> None:
        self.tint_brush.Color = _color(tint, opacity)

    def position(self, bounds: Rect, scale: float, opacity: float) -> None:
        padding = round(28 * scale)
        outer = Rect(bounds.left - padding, bounds.top - padding, bounds.right + padding, bounds.bottom + padding)
        if self.bounds != outer:
            self.bounds = outer
            windows.move(self.handle, outer)
        if self.size != (bounds.width, bounds.height, scale):
            self.size = (bounds.width, bounds.height, scale)
            size = Vector2(bounds.width, bounds.height)
            offset = Vector3(padding, padding, 0)
            self.root.Size = Vector2(outer.width, outer.height)
            self.geometry.Size = size
            radius = min(26 * scale, bounds.height / 2, bounds.width / 2)
            self.geometry.CornerRadius = Vector2(radius, radius)
            for visual in (self.group, self.backdrop, self.tint, self.mask_visual, self.shadow_visual):
                visual.Size = size
            self.group.Offset = self.shadow_visual.Offset = offset
            self.mask_surface.SourceSize = size
            self.shadow.BlurRadius = 24 * scale
            self.shadow.Offset = Vector3(0, 8 * scale, 0)
        opacity = max(0, min(1, opacity))
        if self.opacity != opacity:
            self.root.Opacity = opacity
            self.opacity = opacity

    def show(self, foreground: int) -> None:
        if not self.visible:
            windows.show_quietly(self.handle, True)
            self.visible = True
        # Keep the native blur below the corresponding text, including after a
        # new bubble or a context menu changes the topmost window order.
        _SetWindowPos(self.handle, foreground, 0, 0, 0, 0, 0x0013)

    def hide(self) -> None:
        if self.visible:
            windows.show_quietly(self.handle, False)
            self.visible = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.target.Root = None
        self.target.Close()
        windows.destroy(self.handle)
