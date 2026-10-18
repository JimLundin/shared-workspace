"""Recording the microphone, through the waveIn API."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Final

from ._core import ULONG_PTR, fn, winmm
from .system import EventHandle


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = (
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    )


class WAVEHDR(ctypes.Structure):
    _fields_ = (
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ULONG_PTR),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ULONG_PTR),
    )


_64_BIT = ctypes.sizeof(ctypes.c_void_p) == 8
for _struct, _size in ((WAVEHDR, 48 if _64_BIT else 32), (WAVEFORMATEX, 18)):
    if ctypes.sizeof(_struct) != _size:
        raise ImportError(f"{_struct.__name__} is {ctypes.sizeof(_struct)} bytes; Windows expects {_size}")

_HWAVEIN = wintypes.HANDLE
_waveInOpen = fn(
    winmm, "waveInOpen", wintypes.UINT, ctypes.POINTER(_HWAVEIN), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX), ULONG_PTR, ULONG_PTR, wintypes.DWORD
)
_waveInPrepareHeader = fn(winmm, "waveInPrepareHeader", wintypes.UINT, _HWAVEIN, ctypes.POINTER(WAVEHDR), wintypes.UINT)
_waveInUnprepareHeader = fn(winmm, "waveInUnprepareHeader", wintypes.UINT, _HWAVEIN, ctypes.POINTER(WAVEHDR), wintypes.UINT)
_waveInAddBuffer = fn(winmm, "waveInAddBuffer", wintypes.UINT, _HWAVEIN, ctypes.POINTER(WAVEHDR), wintypes.UINT)
_waveInStart = fn(winmm, "waveInStart", wintypes.UINT, _HWAVEIN)
_waveInReset = fn(winmm, "waveInReset", wintypes.UINT, _HWAVEIN)
_waveInClose = fn(winmm, "waveInClose", wintypes.UINT, _HWAVEIN)

_WAVE_MAPPER: Final = 0xFFFF_FFFF
_WAVE_FORMAT_PCM: Final = 1
_CALLBACK_EVENT: Final = 0x0005_0000
_WHDR_DONE: Final = 0x01


class Microphone:
    """Records the default microphone as 16-bit mono PCM into one buffer, allocated
    once. Windows sets `event` whenever the recording changes state, including when
    the buffer is full. The microphone is only open between `start` and `stop`."""

    __slots__ = ("_buffer", "_format", "_handle", "_header", "event")

    def __init__(self, rate: int, seconds: int, event: EventHandle) -> None:
        self._format = WAVEFORMATEX(_WAVE_FORMAT_PCM, 1, rate, rate * 2, 2, 16, 0)
        self._buffer = ctypes.create_string_buffer(rate * 2 * seconds)
        self._handle = _HWAVEIN()
        self._header = WAVEHDR()
        self.event = event

    def start(self) -> int:
        """Opens the microphone and starts recording. 0, or Windows' error code."""
        error = int(_waveInOpen(ctypes.byref(self._handle), _WAVE_MAPPER, ctypes.byref(self._format), self.event, 0, _CALLBACK_EVENT))
        if error:
            return error
        self._header = WAVEHDR(ctypes.addressof(self._buffer), len(self._buffer))
        size = ctypes.sizeof(WAVEHDR)
        for step in (_waveInPrepareHeader, _waveInAddBuffer):
            if error := int(step(self._handle, ctypes.byref(self._header), size)):
                self.stop()
                return error
        if error := int(_waveInStart(self._handle)):
            self.stop()
        return error

    def full(self) -> bool:
        return bool(self._header.dwFlags & _WHDR_DONE)

    def stop(self) -> bytes:
        """Stops, closes the microphone, and returns what was recorded."""
        _waveInReset(self._handle)
        recorded = ctypes.string_at(self._buffer, int(self._header.dwBytesRecorded))
        _waveInUnprepareHeader(self._handle, ctypes.byref(self._header), ctypes.sizeof(WAVEHDR))
        _waveInClose(self._handle)
        return recorded
