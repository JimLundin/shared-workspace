"""Continuous shared-mode microphone and speaker loopback through WASAPI.

Only the recording thread owns COM interfaces. The engine keeps capturing while
we drain small packets; no microphone stop/start occurs between transcript chunks.
"""
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import contextlib
import ctypes
import threading
import uuid
from collections.abc import Callable, Iterator
from ctypes import wintypes
from typing import Any, Final

from assistant.llm import RATE
from assistant.pcm import Converter, Mixer, PcmFormat

from ._core import fn, kernel32
from .audio import WAVEFORMATEX

_ole32 = ctypes.WinDLL("ole32", use_last_error=True)
_CoInitializeEx = fn(_ole32, "CoInitializeEx", ctypes.c_long, ctypes.c_void_p, wintypes.DWORD)
_CoUninitialize = fn(_ole32, "CoUninitialize", None)
_CoCreateInstance = fn(_ole32, "CoCreateInstance", ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)
_CoTaskMemFree = fn(_ole32, "CoTaskMemFree", None, ctypes.c_void_p)
_QueryPerformanceCounter = fn(kernel32, "QueryPerformanceCounter", wintypes.BOOL, ctypes.POINTER(ctypes.c_longlong))
_QueryPerformanceFrequency = fn(kernel32, "QueryPerformanceFrequency", wintypes.BOOL, ctypes.POINTER(ctypes.c_longlong))

_LOOPBACK: Final = 0x0002_0000
_DISCONTINUITY: Final = 1
_SILENT: Final = 2
_TIMESTAMP_ERROR: Final = 4
_LATENCY: Final = 0.5


def _guid(value: str) -> ctypes.Array[ctypes.c_char]:
    return ctypes.create_string_buffer(uuid.UUID(value).bytes_le, 16)


_ENUMERATOR = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
_IID_ENUMERATOR = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
_IID_CLIENT = _guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
_IID_CAPTURE = _guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")


def _check(result: int, action: str) -> None:
    if result < 0:
        raise OSError(f"{action} failed (Windows audio 0x{result & 0xFFFF_FFFF:08X}). Check the selected audio devices and desktop microphone access.")


def _method(pointer: ctypes.c_void_p, index: int, *arguments: Any) -> Any:
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)(table[index])


def _release(pointer: ctypes.c_void_p) -> None:
    if pointer:
        _method(pointer, 2)(pointer)
        pointer.value = None


def _clock() -> float:
    counter, frequency = ctypes.c_longlong(), ctypes.c_longlong()
    if not _QueryPerformanceCounter(ctypes.byref(counter)) or not _QueryPerformanceFrequency(ctypes.byref(frequency)):
        raise OSError("Windows could not read the audio clock.")
    return counter.value / frequency.value


def _format(pointer: ctypes.c_void_p) -> PcmFormat:
    format = ctypes.cast(pointer, ctypes.POINTER(WAVEFORMATEX)).contents
    tag = int(format.wFormatTag)
    if tag == 0xFFFE:  # WAVEFORMATEXTENSIBLE: samples, channel mask, and subtype GUID
        if format.cbSize < 22:
            raise OSError("The audio device returned an incomplete extended format.")
        subtype = uuid.UUID(bytes_le=ctypes.string_at((pointer.value or 0) + 24, 16))
        if subtype == uuid.UUID("00000001-0000-0010-8000-00aa00389b71"):
            tag = 1
        elif subtype == uuid.UUID("00000003-0000-0010-8000-00aa00389b71"):
            tag = 3
    if tag not in (1, 3):
        raise OSError("The audio device does not provide supported PCM audio.")
    result = PcmFormat(int(format.nSamplesPerSec), int(format.nChannels), int(format.wBitsPerSample), tag == 3)
    if result.frame_bytes != format.nBlockAlign:
        raise OSError("The audio device returned an inconsistent sample format.")
    return result


class Stream:
    __slots__ = ("capture", "client", "converter", "device", "frames", "label", "loopback", "started")

    def __init__(self, enumerator: ctypes.c_void_p, *, loopback: bool) -> None:
        self.device, self.client, self.capture = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        self.label = "Computer audio" if loopback else "Microphone"
        self.loopback = loopback
        self.started = False
        self.frames: int | None = None
        try:
            # Multimedia default matches normal app playback and the system's
            # selected microphone. Both use shared mode, including alongside B/N.
            _check(_method(enumerator, 4, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)(
                enumerator, 0 if loopback else 1, 1, ctypes.byref(self.device)), f"Opening {self.label.lower()}")
            _check(_method(self.device, 3, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)(
                self.device, _IID_CLIENT, 23, None, ctypes.byref(self.client)), f"Preparing {self.label.lower()}")
            format = ctypes.c_void_p()
            _check(_method(self.client, 8, ctypes.c_void_p)(self.client, ctypes.byref(format)), "Reading audio format")
            try:
                self.converter = Converter(_format(format))
                _check(_method(self.client, 3, ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                               ctypes.c_void_p, ctypes.c_void_p)(
                    self.client, 0, _LOOPBACK if loopback else 0, 10_000_000, 0, format, None), f"Initializing {self.label.lower()}")
            finally:
                _CoTaskMemFree(format)
            _check(_method(self.client, 14, ctypes.c_void_p, ctypes.c_void_p)(
                self.client, _IID_CAPTURE, ctypes.byref(self.capture)), "Opening audio capture")
        except BaseException:
            self.close()
            raise

    def start(self) -> None:
        _check(_method(self.client, 10)(self.client), f"Starting {self.label.lower()}")
        self.started = True

    def packets(self, origin: float) -> Iterator[tuple[int, bytes]]:
        size = wintypes.UINT()
        while True:
            _check(_method(self.capture, 5, ctypes.c_void_p)(self.capture, ctypes.byref(size)), "Reading audio packet size")
            if not size.value:
                return
            data, frames, flags = ctypes.c_void_p(), wintypes.UINT(), wintypes.DWORD()
            position, timestamp = ctypes.c_ulonglong(), ctypes.c_ulonglong()
            _check(_method(self.capture, 3, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
                self.capture, ctypes.byref(data), ctypes.byref(frames), ctypes.byref(flags), ctypes.byref(position), ctypes.byref(timestamp)),
                f"Capturing {self.label.lower()}")
            try:
                if flags.value & _TIMESTAMP_ERROR:
                    raise OSError(f"{self.label} lost its audio clock. The captured part has been saved.")
                if self.frames is not None and position.value < self.frames:
                    raise OSError(f"{self.label} restarted its audio clock. The captured part has been saved.")
                # A discontinuity reports a gap before this valid packet, not a
                # failed device. Keep the packet's clock position so the mixer
                # leaves silence for missing samples and keeps the other source.
                # Reset only the resampler's carried sample/phase across the gap.
                if self.frames is not None and flags.value & _DISCONTINUITY:
                    self.converter = Converter(self.converter.format)
                self.frames = position.value + frames.value
                count = frames.value * self.converter.format.frame_bytes
                raw = bytes(count) if flags.value & _SILENT else ctypes.string_at(data, count)
                offset = self.converter.output_frames / RATE - self.converter.input_frames / self.converter.format.rate
                start = round((timestamp.value / 10_000_000 - origin + offset) * RATE)
                pcm = self.converter.convert(raw)
            finally:
                _check(_method(self.capture, 4, wintypes.UINT)(self.capture, frames), "Returning audio packet")
            yield start, pcm

    def stop(self) -> None:
        if self.started:
            _check(_method(self.client, 11)(self.client), f"Stopping {self.label.lower()}")
            self.started = False

    def close(self) -> None:
        try:
            self.stop()
        finally:
            for pointer in (self.capture, self.client, self.device):
                _release(pointer)


class MeetingAudio:
    """Capture only between explicit Start and Stop; never opens a device at import."""

    __slots__ = ("computer_audio",)

    def __init__(self, *, computer_audio: bool = True) -> None:
        self.computer_audio = computer_audio

    def record(self, stop: threading.Event, consume: Callable[[bytes], None], ready: Callable[[], None]) -> None:
        _check(_CoInitializeEx(None, 0), "Preparing Windows audio")
        enumerator = ctypes.c_void_p()
        streams: list[Stream] = []
        try:
            _check(_CoCreateInstance(_ENUMERATOR, None, 23, _IID_ENUMERATOR, ctypes.byref(enumerator)), "Finding audio devices")
            streams.append(Stream(enumerator, loopback=False))
            if self.computer_audio:
                streams.append(Stream(enumerator, loopback=True))
            if stop.is_set():
                return
            mixer, origin = Mixer(), _clock()
            for stream in streams:
                stream.start()
            ready()
            self._capture(streams, mixer, origin, stop, consume)
        finally:
            for stream in reversed(streams):
                with contextlib.suppress(OSError):
                    stream.close()
            _release(enumerator)
            _CoUninitialize()

    def _capture(self, streams: list[Stream], mixer: Mixer, origin: float, stop: threading.Event, consume: Callable[[bytes], None]) -> None:
        latest = 0

        def drain() -> None:
            nonlocal latest
            for stream in streams:
                for start, pcm in stream.packets(origin):
                    mixer.add(start, pcm)
                    latest = max(latest, start + len(pcm) // 2)

        try:
            while not stop.wait(0.02):
                drain()
                until = max(0, int((_clock() - origin - _LATENCY) * RATE))
                if until - mixer.cursor >= RATE // 10:
                    consume(mixer.take(until))
            until = round((_clock() - origin) * RATE)
            for stream in streams:
                stream.stop()
            drain()
            consume(mixer.take(until))
        except (OSError, ValueError):
            # Retain samples already returned by the other device, including
            # the latency cushion, before reporting a capture/storage failure.
            if latest > mixer.cursor:
                consume(mixer.take(latest))
            raise
