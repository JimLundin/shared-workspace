"""The clipboard, as text."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Final

from ._core import fn, kernel32, user32

_OpenClipboard = fn(user32, "OpenClipboard", wintypes.BOOL, wintypes.HWND)
_CloseClipboard = fn(user32, "CloseClipboard", wintypes.BOOL)
_EmptyClipboard = fn(user32, "EmptyClipboard", wintypes.BOOL)
_GetClipboardData = fn(user32, "GetClipboardData", wintypes.HANDLE, wintypes.UINT)
_SetClipboardData = fn(user32, "SetClipboardData", wintypes.HANDLE, wintypes.UINT, wintypes.HANDLE)
_GetClipboardSequenceNumber = fn(user32, "GetClipboardSequenceNumber", wintypes.DWORD)
_GlobalAlloc = fn(kernel32, "GlobalAlloc", wintypes.HGLOBAL, wintypes.UINT, ctypes.c_size_t)
_GlobalLock = fn(kernel32, "GlobalLock", wintypes.LPVOID, wintypes.HGLOBAL)
_GlobalUnlock = fn(kernel32, "GlobalUnlock", wintypes.BOOL, wintypes.HGLOBAL)
_GlobalFree = fn(kernel32, "GlobalFree", wintypes.HGLOBAL, wintypes.HGLOBAL)

_CF_UNICODETEXT: Final = 13
_GMEM_MOVEABLE: Final = 0x0002


def sequence() -> int:
    """Changes every time anything puts something on the clipboard."""
    return int(_GetClipboardSequenceNumber())


def _open(hwnd: int) -> bool:
    # Another program may have it open for a moment; try for up to half a second.
    for _ in range(25):
        if _OpenClipboard(hwnd):
            return True
        time.sleep(0.02)
    return False


def get_text(hwnd: int) -> str | None:
    """The clipboard's text; None if it holds no text, or can't be opened."""
    if not _open(hwnd):
        return None
    try:
        handle = _GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        pointer = _GlobalLock(handle)
        if not pointer:
            return None
        try:
            return str(ctypes.wstring_at(pointer))
        finally:
            _GlobalUnlock(handle)
    finally:
        _CloseClipboard()


def set_text(hwnd: int, text: str) -> bool:
    """Replaces the clipboard's contents with text. `hwnd` becomes its owner."""
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not _open(hwnd):
        return False
    try:
        _EmptyClipboard()
        handle = _GlobalAlloc(_GMEM_MOVEABLE, size)
        if not handle:
            return False
        pointer = _GlobalLock(handle)
        ctypes.memmove(pointer, data, size)
        _GlobalUnlock(handle)
        if not _SetClipboardData(_CF_UNICODETEXT, handle):
            _GlobalFree(handle)
            return False
        return True  # the clipboard owns the memory now
    finally:
        _CloseClipboard()
