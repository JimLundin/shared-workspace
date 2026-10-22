"""Voice capture against a Windows input device with a slow cold open."""

import ctypes
import sys
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from ctypes import wintypes
from typing import Literal, cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows audio bindings")

from winapi import audio
from winapi.system import EventHandle

RATE = 16_000
SPEECH = b"\x01\x00" * RATE + b"\x02\x00" * RATE + b"\x03\x00" * RATE


class InputDevice:
    """A clock and PCM source: opening costs a second, starting is immediate."""

    __slots__ = ("header", "now", "opened", "speech", "spoken_at", "started_at")

    def __init__(self) -> None:
        self.now = 0.0
        self.spoken_at = 0.0
        self.started_at: float | None = None
        self.speech = SPEECH
        self.header: audio.WAVEHDR | None = None
        self.opened = False

    def state(self) -> Literal["closed", "ready", "recording"]:
        if self.started_at is not None:
            return "recording"
        return "ready" if self.opened else "closed"

    def open(self, handle: object, *args: object) -> int:
        self.now += 1.0
        ctypes.cast(cast("ctypes.c_void_p", handle), ctypes.POINTER(wintypes.HANDLE)).contents.value = 1
        self.opened = True
        return 0

    def add(self, handle: object, header: object, size: int) -> int:
        self.header = ctypes.cast(cast("ctypes.c_void_p", header), ctypes.POINTER(audio.WAVEHDR)).contents
        return 0

    def start(self, handle: object) -> int:
        self.started_at = self.now
        return 0

    def reset(self, handle: object) -> int:
        if self.header is not None and self.started_at is not None:
            beginning = round((self.started_at - self.spoken_at) * RATE) * 2
            end = round((self.now - self.spoken_at) * RATE) * 2
            recorded = self.speech[beginning:end]
            ctypes.memmove(cast("int", self.header.lpData), recorded, len(recorded))
            self.header.dwBytesRecorded = len(recorded)
        if self.header is not None:
            self.header.dwFlags = 1  # WHDR_DONE: the driver has returned the buffer
        self.started_at = None
        return 0

    def close(self, handle: object) -> int:
        self.opened = False
        return 0

    def say(self, mic: audio.Microphone) -> bytes:
        self.spoken_at = self.now  # speech starts with the key press
        assert mic.start() == 0
        self.now = self.spoken_at + len(self.speech) / (RATE * 2)
        return mic.stop()


@contextmanager
def input_device() -> Generator[InputDevice]:
    device = InputDevice()
    with (
        patch("winapi.audio._waveInOpen", side_effect=device.open),
        patch("winapi.audio._waveInPrepareHeader", return_value=0),
        patch("winapi.audio._waveInAddBuffer", side_effect=device.add),
        patch("winapi.audio._waveInStart", side_effect=device.start),
        patch("winapi.audio._waveInReset", side_effect=device.reset),
        patch("winapi.audio._waveInUnprepareHeader", return_value=0),
        patch("winapi.audio._waveInClose", side_effect=device.close),
    ):
        yield device


def test_captures_the_first_words_spoken_immediately_on_press() -> None:
    with input_device() as device:
        mic = audio.Microphone(RATE, 300, EventHandle(1))
        recorded = device.say(mic)
        assert recorded == SPEECH, f"Lost {(len(SPEECH) - len(recorded)) / (RATE * 2):.1f}s from the beginning"
        mic.close()


def test_captures_the_first_words_again_after_an_idle_pause() -> None:
    with input_device() as device:
        mic = audio.Microphone(RATE, 300, EventHandle(1))
        device.say(mic)
        device.now += 60
        device.speech = SPEECH[::-1]
        assert device.say(mic) == device.speech
        mic.close()


def test_microphone_only_records_while_held_and_releases_the_device_on_close() -> None:
    with input_device() as device:
        mic = audio.Microphone(RATE, 300, EventHandle(1))
        assert device.state() == "ready"
        assert mic.start() == 0
        assert device.state() == "recording"
        mic.stop()
        assert device.state() == "ready"
        assert mic.stop() == b""
        mic.close()
        assert device.state() == "closed"
        mic.close()


def test_closing_while_held_stops_recording_and_releases_the_device() -> None:
    with input_device() as device:
        mic = audio.Microphone(RATE, 300, EventHandle(1))
        assert mic.start() == 0
        mic.close()
        assert device.state() == "closed"
        assert mic.stop() == b""


def test_failed_startup_open_is_reported_on_press_and_can_be_retried() -> None:
    with input_device() as device:
        with patch("winapi.audio._waveInOpen", return_value=5):
            mic = audio.Microphone(RATE, 300, EventHandle(1))
            assert mic.start() == 5
            assert device.state() == "closed"
            assert mic.stop() == b""
            mic.close()
        assert mic.start() == 0
        assert device.state() == "recording"
        mic.close()


def test_a_failed_recording_releases_the_device_and_can_be_retried() -> None:
    for step in ("_waveInPrepareHeader", "_waveInAddBuffer", "_waveInStart"):
        with input_device() as device:
            mic = audio.Microphone(RATE, 300, EventHandle(1))
            with patch(f"winapi.audio.{step}", return_value=5):
                assert mic.start() == 5
            assert device.state() == "closed"
            assert mic.stop() == b""
            assert mic.start() == 0
            mic.close()


def test_stop_waits_for_the_final_audio_buffer_after_a_stale_open_event() -> None:
    """A completion event, not reset's return, owns the final byte count."""
    with input_device() as device:
        mic = audio.Microphone(RATE, 300, EventHandle(1))
        waits = 0

        def reset(handle: object) -> int:
            device.reset(handle)
            assert device.header is not None
            device.header.dwFlags = 0
            device.header.dwBytesRecorded = len(SPEECH) // 2
            return 0

        def returned(events: list[EventHandle], timeout_ms: int) -> int:
            nonlocal waits
            assert events == [EventHandle(1)] and timeout_ms > 0
            waits += 1
            if waits == 2:  # the first signal is left over from opening the device
                assert device.header is not None
                device.header.dwBytesRecorded = len(SPEECH)
                device.header.dwFlags = 1
            return 0

        with patch("winapi.audio._waveInReset", side_effect=reset), patch("winapi.audio.wait_any", side_effect=returned, create=True):
            assert device.say(mic) == SPEECH, "The last half of the voice prompt was cut off"
        assert waits == 2
        mic.close()


def test_a_stop_error_is_reported_and_the_next_capture_can_retry() -> None:
    for step in ("_waveInReset", "_waveInUnprepareHeader"):
        with input_device() as device:
            mic = audio.Microphone(RATE, 300, EventHandle(1))
            with patch(f"winapi.audio.{step}", return_value=5), unittest.TestCase().assertRaises(OSError):
                device.say(mic)
            assert device.say(mic) == SPEECH
            mic.close()


def test_an_unreturned_buffer_times_out_without_giving_back_partial_audio() -> None:
    with input_device() as device:
        mic = audio.Microphone(RATE, 300, EventHandle(1))

        def incomplete(handle: object) -> int:
            device.reset(handle)
            assert device.header is not None
            device.header.dwFlags = 0
            device.header.dwBytesRecorded = len(SPEECH) // 2
            return 0

        with (
            patch("winapi.audio._waveInReset", side_effect=incomplete),
            patch("winapi.audio.wait_any", return_value=None),
            unittest.TestCase().assertRaisesRegex(OSError, "complete recording"),
        ):
            device.say(mic)
        assert device.say(mic) == SPEECH
        mic.close()
