"""The DLLs, and how a function prototype is declared. Internal to `winapi`."""

import ctypes
from ctypes import wintypes
from typing import Any

from .shapes import Rect

ULONG_PTR = ctypes.c_size_t
LONG_PTR = ctypes.c_ssize_t
LRESULT = LONG_PTR

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)


def fn(dll: ctypes.WinDLL, name: str, restype: Any, *argtypes: Any) -> Any:
    """A typed foreign function: `fn(user32, "GetForegroundWindow", HWND)`."""
    prototype = ctypes.WINFUNCTYPE(restype, *argtypes, use_last_error=True)
    return prototype((name, dll))


def optional_fn(dll: ctypes.WinDLL, name: str, restype: Any, *argtypes: Any) -> Any:
    """Like `fn`, for functions older Windows versions don't have; None if missing."""
    try:
        return fn(dll, name, restype, *argtypes)
    except AttributeError:
        return None


def rect(r: wintypes.RECT) -> Rect:
    return Rect(int(r.left), int(r.top), int(r.right), int(r.bottom))


def last_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())
