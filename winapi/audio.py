"""Recording the microphone, through the waveIn API."""

import ctypes
import time
from ctypes import wintypes
from typing import Final

from ._core import ULONG_PTR, fn, winmm
from .system import EventHandle, wait_any


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

    def __init__(self, data: int = 0, length: int = 0) -> None:
        super().__init__(data, length)


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
_RETURN_TIMEOUT_MS: Final = 1000


class Microphone:
    """Records the default microphone as 16-bit mono PCM into one buffer, allocated
    once. Windows sets `event` whenever the recording changes state, including when
    the buffer is full. Opened at construction and kept ready until `close`, but
    only records between `start` and `stop`."""

    __slots__ = ("_buffer", "_format", "_handle", "_header", "_prepared", "_queued", "_stopping", "event")

    def __init__(self, rate: int, seconds: int, event: EventHandle) -> None:
        self._format = WAVEFORMATEX(_WAVE_FORMAT_PCM, 1, rate, rate * 2, 2, 16, 0)
        self._buffer = ctypes.create_string_buffer(rate * 2 * seconds)
        self._handle = _HWAVEIN()
        self._header = WAVEHDR()
        self._prepared = False
        self._queued = False
        self._stopping = False
        self.event = event
        # Opening a device can take a second. Pay that cost before the talk key is
        # pressed; start() retries and reports the error if it isn't available yet.
        self._open()

    def _open(self) -> int:
        if self._handle:
            return 0
        handle = _HWAVEIN()
        error = int(_waveInOpen(ctypes.byref(handle), _WAVE_MAPPER, ctypes.byref(self._format), self.event, 0, _CALLBACK_EVENT))
        if not error:
            self._handle = handle
        return error

    def start(self) -> int:
        """Starts recording on the ready microphone. 0, or Windows' error code."""
        if self._prepared:
            if not self._stopping:
                return 0
            try:
                self.stop()  # finish cleanup after a failed stop before reusing its buffer
            except OSError as problem:
                return problem.errno or 1
        if error := self._open():
            return error
        self._header = WAVEHDR(ctypes.addressof(self._buffer), len(self._buffer))
        size = ctypes.sizeof(WAVEHDR)
        if error := int(_waveInPrepareHeader(self._handle, ctypes.byref(self._header), size)):
            self.close()
            return error
        self._prepared = True
        if error := int(_waveInAddBuffer(self._handle, ctypes.byref(self._header), size)):
            self.close()
            return error
        self._queued = True
        if error := int(_waveInStart(self._handle)):
            self.close()
        return error

    def full(self) -> bool:
        return bool(self._header.dwFlags & _WHDR_DONE)

    def stop(self) -> bytes:
        """Returns the recording, keeping the microphone ready for the next press."""
        if not self._prepared:
            return b""
        self._stopping = True
        recorded = b""
        if self._queued:
            if error := int(_waveInReset(self._handle)):
                raise OSError(error, "The microphone could not finish recording. Please try again.")
            self._wait_for_return()
            recorded = ctypes.string_at(self._buffer, int(self._header.dwBytesRecorded))
        if error := int(_waveInUnprepareHeader(self._handle, ctypes.byref(self._header), ctypes.sizeof(WAVEHDR))):
            raise OSError(error, "The microphone could not release its recording buffer. Please try again.")
        self._prepared = False
        self._queued = False
        self._stopping = False
        self._header = WAVEHDR()
        return recorded

    def _wait_for_return(self) -> None:
        """Keep the header and PCM alive until the driver returns the final data.

        Opening and starting the device signal the same event, so a signal alone
        is not proof that the last buffer is ready to read or unprepare.
        """
        deadline = time.monotonic() + _RETURN_TIMEOUT_MS / 1000
        while not self.full():
            remaining = round((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or wait_any([self.event], timeout_ms=remaining) is None:
                raise OSError("Timed out waiting for the microphone's complete recording. Please try again.")

    def close(self) -> None:
        """Stops any recording and releases the microphone before its event closes."""
        if self._handle:
            self.stop()
            _waveInClose(self._handle)
            self._handle = _HWAVEIN()
