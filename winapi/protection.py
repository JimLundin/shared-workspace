"""Protect integration credentials with the signed-in Windows account's DPAPI key."""

import ctypes
from ctypes import wintypes

from ._core import fn, kernel32, last_error


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_Protect = fn(_crypt32, "CryptProtectData", wintypes.BOOL, ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob))
_Unprotect = fn(_crypt32, "CryptUnprotectData", wintypes.BOOL, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob))
_LocalFree = fn(kernel32, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)


def _transform(data: bytes, *, decrypt: bool) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    operation = _Unprotect if decrypt else _Protect
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise last_error()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        _LocalFree(result.data)


def protect(data: bytes) -> bytes:
    """Account-bound encryption, with prompts forbidden and no machine-wide key."""
    return _transform(data, decrypt=False)


def unprotect(data: bytes) -> bytes:
    return _transform(data, decrypt=True)
