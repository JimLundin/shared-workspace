"""Wheel input over translucent notification glass, only in the assistant process.

Empty glass pixels pass native hit testing through to the desktop. A small mouse
hook routes vertical wheel messages within the current card bounds to their Qt
text widgets. It never handles keys or activates a window. Its dedicated message
thread does no widget work, and removes the hook when there are no scrolling cards.
"""

import ctypes
import threading
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass

from . import system
from ._core import LRESULT, ULONG_PTR, fn, kernel32, last_error, user32
from .shapes import Rect

_WM_MOUSEWHEEL = 0x020A
_WM_QUIT = 0x0012
_HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_SetWindowsHookExW = fn(user32, "SetWindowsHookExW", wintypes.HHOOK, ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_UnhookWindowsHookEx = fn(user32, "UnhookWindowsHookEx", wintypes.BOOL, wintypes.HHOOK)
_CallNextHookEx = fn(user32, "CallNextHookEx", LRESULT, wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_GetModuleHandleW = fn(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
_PeekMessageW = fn(user32, "PeekMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)


class _Mouse(ctypes.Structure):
    _fields_ = [("point", wintypes.POINT), ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ULONG_PTR)]


@dataclass(frozen=True, slots=True)
class Region:
    target: int
    bounds: Rect


@dataclass(frozen=True, slots=True)
class Scroll:
    target: int
    delta: int


class WheelRouter:
    __slots__ = ("active", "closed", "error", "events", "ready", "regions", "thread", "thread_id")

    def __init__(self) -> None:
        self.regions: tuple[Region, ...] = ()
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.ready = threading.Event()
        self.active = False
        self.closed = False
        self.error: OSError | None = None
        self.events: deque[Scroll] = deque(maxlen=64)

    def update(self, regions: tuple[Region, ...]) -> None:
        if self.closed:
            return
        enabled = bool(self.regions)
        self.regions = regions
        if not regions:
            self.events.clear()
        if regions and self.thread is None:
            self.thread = threading.Thread(target=self.run, name="notification wheel", daemon=True)
            self.thread.start()
        elif self.thread_id and bool(regions) != enabled:
            system.post_thread_message(self.thread_id, system.WM_APP, 0, 0)

    def dispatch(self, x: int, y: int, delta: int) -> bool:
        if delta:
            for region in self.regions:
                bounds = region.bounds
                if bounds.left <= x < bounds.right and bounds.top <= y < bounds.bottom:
                    # A bounded queue keeps the hook independent of widget work.
                    # Posting WM_MOUSEWHEEL directly does not work when the widget's
                    # nonactivating windows have no keyboard focus.
                    self.events.append(Scroll(region.target, delta))
                    return True
        return False

    def drain(self) -> tuple[Scroll, ...]:
        result: list[Scroll] = []
        while True:
            try:
                result.append(self.events.popleft())
            except IndexError:
                return tuple(result)

    def hook(self, code: int, message: int, address: int) -> int:
        if code >= 0 and message == _WM_MOUSEWHEEL:
            event = ctypes.cast(address, ctypes.POINTER(_Mouse)).contents
            if self.dispatch(int(event.point.x), int(event.point.y), ctypes.c_short(event.data >> 16).value):
                return 1
        return int(_CallNextHookEx(None, code, message, address))

    def run(self) -> None:
        callback = _HOOKPROC(self.hook)  # Keep the native trampoline alive until unhooked.
        handle: int | None = None
        message = wintypes.MSG()
        _PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # Create this thread's message queue.
        self.thread_id = system.thread_id()
        system.post_thread_message(self.thread_id, system.WM_APP, 0, 0)
        try:
            for _message in system.messages():
                if self.closed:
                    break
                if self.regions and handle is None:
                    handle = _SetWindowsHookExW(14, callback, _GetModuleHandleW(None), 0)  # WH_MOUSE_LL
                    self.error = None if handle else last_error()
                elif not self.regions and handle is not None:
                    _UnhookWindowsHookEx(handle)
                    handle = None
                self.active = handle is not None
                self.ready.set()
        finally:
            if handle is not None:
                _UnhookWindowsHookEx(handle)
            self.active = False
            self.ready.set()

    def close(self) -> None:
        self.closed = True
        self.regions = ()
        self.events.clear()
        if self.thread is not None:
            if self.thread_id:
                system.post_thread_message(self.thread_id, _WM_QUIT, 0, 0)
            self.thread.join(timeout=1)
