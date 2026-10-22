"""WASAPI ABI and continuous capture with synthetic devices, never the real mic."""
# pyright: reportPrivateUsage=false

import ctypes
import sys
import threading
import time
import unittest
from array import array
from collections.abc import Callable, Generator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows continuous audio bindings")

from assistant.llm import RATE
from assistant.meeting_store import MeetingArchive, MeetingStore
from assistant.meetings import Meetings
from winapi import audio, recording

from .fake_endpoint import fake_endpoint
from .test_llm import settings
from .test_meetings import wait_for


def test_transcript_refresh_waits_for_a_real_windows_reader_to_release_the_file(tmp_path: Path) -> None:
    store = MeetingStore.create(tmp_path, "microphone")
    store.append(array("h", [1200] * RATE).tobytes())
    store.finish_capture()
    batch = store.batch()
    assert batch is not None
    store.complete(batch, "The complete transcript.")
    with store.transcript.open("r", encoding="utf-8") as reader:
        def release() -> None:
            time.sleep(0.15)
            reader.close()

        closer = threading.Thread(target=release)
        closer.start()
        try:
            store.export()
        finally:
            closer.join(timeout=2)
    assert "The complete transcript." in store.transcript.read_text(encoding="utf-8")


def set_pointer(argument: object, value: int) -> None:
    ctypes.cast(cast("ctypes.c_void_p", argument), ctypes.POINTER(ctypes.c_void_p)).contents.value = value


def set_uint(argument: object, value: int) -> None:
    ctypes.cast(cast("ctypes.c_void_p", argument), ctypes.POINTER(wintypes.UINT)).contents.value = value


def set_u64(argument: object, value: int) -> None:
    ctypes.cast(cast("ctypes.c_void_p", argument), ctypes.POINTER(ctypes.c_ulonglong)).contents.value = value


class NativeCall(Protocol):
    def __call__(self, *arguments: object) -> int: ...


class Wasapi:
    __slots__ = (
        "buffer", "calls", "delivered", "discontinuous", "dropouts", "fail", "flags", "format", "now", "origin", "packet_frames", "stop_at", "ticks",
    )

    def __init__(self) -> None:
        self.format = audio.WAVEFORMATEX(3, 2, 48_000, 48_000 * 8, 8, 32, 0)
        self.origin = self.now = 1000.0
        self.ticks, self.stop_at = 0, 127
        self.delivered = {30: 0, 31: 0}
        self.dropouts: dict[tuple[int, int], int] = {}
        self.discontinuous: set[int] = set()
        self.packet_frames = 480
        self.flags = 0
        self.buffer = ctypes.create_string_buffer(0)
        self.calls: list[tuple[int, int]] = []
        self.fail: tuple[int, int] | None = None

    def create(self, *arguments: object) -> int:
        set_pointer(arguments[-1], 1)
        return 0

    def wait(self, timeout: float) -> bool:
        self.ticks += 1
        self.now = self.origin + self.ticks * timeout
        return self.ticks >= self.stop_at

    def method(self, pointer: ctypes.c_void_p, index: int, *types: object) -> NativeCall:
        handle = pointer.value
        assert handle is not None

        def invoke(*arguments: object) -> int:
            self.calls.append((handle, index))
            if (handle, index) == self.fail:
                return -2_002_000_001
            return self.invoke(handle, index, arguments)

        return invoke

    def invoke(self, handle: int, index: int, arguments: tuple[object, ...]) -> int:
        if index == 2:  # IUnknown.Release
            return 0
        if handle == 1 and index == 4:  # default endpoint: eRender=0, eCapture=1
            set_pointer(arguments[-1], 10 if arguments[1] == 1 else 11)
        elif handle in (10, 11) and index == 3:  # device.Activate
            set_pointer(arguments[-1], handle + 10)
        elif handle in (20, 21):
            if index == 8:  # client.GetMixFormat
                set_pointer(arguments[-1], ctypes.addressof(self.format))
            elif index == 14:  # client.GetService
                set_pointer(arguments[-1], handle + 10)
            elif index == 3:  # client.Initialize, shared mode and one-second engine buffer
                assert arguments[1] == 0 and arguments[3] == 10_000_000
                assert arguments[2] == (0 if handle == 20 else 0x0002_0000)
            else:
                assert index in (10, 11), (handle, index)
        elif handle in (30, 31):
            self.capture(handle, index, arguments)
        else:
            raise AssertionError((handle, index))
        return 0

    def capture(self, handle: int, index: int, arguments: tuple[object, ...]) -> None:
        rate = cast(int, self.format.nSamplesPerSec)
        if index == 5:
            if missing := self.dropouts.pop((handle, self.delivered[handle]), 0):
                self.delivered[handle] += missing
                self.discontinuous.add(handle)
            available = round((self.now - self.origin) * rate) - self.delivered[handle]
            set_uint(arguments[1], self.packet_frames if available >= self.packet_frames else 0)
        elif index == 3:
            value = 1000 if handle == 30 else 2000
            samples = self.packet_frames * cast(int, self.format.nChannels)
            pcm = array("f", [value / 32768] * samples) if cast(int, self.format.wFormatTag) == 3 else array("h", [value] * samples)
            self.buffer = ctypes.create_string_buffer(pcm.tobytes())
            set_pointer(arguments[1], ctypes.addressof(self.buffer))
            set_uint(arguments[2], self.packet_frames)
            set_uint(arguments[3], 1 if not self.delivered[handle] or handle in self.discontinuous else self.flags)
            set_u64(arguments[4], self.delivered[handle])
            set_u64(arguments[5], round((self.origin + self.delivered[handle] / rate) * 10_000_000))
        elif index == 4:
            self.delivered[handle] += self.packet_frames
            self.discontinuous.discard(handle)
        else:
            raise AssertionError((handle, index))


@contextmanager
def wasapi() -> Generator[Wasapi]:
    api = Wasapi()
    with (
        patch("winapi.recording._CoInitializeEx", return_value=0),
        patch("winapi.recording._CoUninitialize"),
        patch("winapi.recording._CoCreateInstance", side_effect=api.create),
        patch("winapi.recording._CoTaskMemFree"),
        patch("winapi.recording._clock", side_effect=lambda: api.now),
        patch("winapi.recording._method", side_effect=api.method),
    ):
        yield api


def test_continuous_loopback_and_microphone_share_a_clock_and_flush_on_stop() -> None:
    stop, ready = threading.Event(), threading.Event()
    blocks: list[bytes] = []
    with wasapi() as api, patch.object(stop, "wait", side_effect=api.wait):
        recording.MeetingAudio().record(stop, blocks.append, ready.set)
        assert ready.is_set()
        assert b"".join(blocks) == array("h", [3000] * round(2.54 * RATE)).tobytes()
        assert max(map(len, blocks)) < RATE * 2  # small callbacks even at final Stop
        for handle in (20, 21):
            assert api.calls.count((handle, 10)) == 1  # one Start, never at chunk boundaries
            assert api.calls.count((handle, 11)) == 1
        for handle in (1, 10, 11, 20, 21, 30, 31):
            assert api.calls.count((handle, 2)) == 1


def test_microphone_only_never_opens_the_playback_device() -> None:
    stop = threading.Event()
    blocks: list[bytes] = []
    with wasapi() as api, patch.object(stop, "wait", side_effect=api.wait):
        recording.MeetingAudio(computer_audio=False).record(stop, blocks.append, lambda: None)
        assert b"".join(blocks) == array("h", [1000] * round(2.54 * RATE)).tobytes()
        assert not any(handle in (11, 21, 31) for handle, _ in api.calls)


def test_microphone_discontinuities_do_not_end_a_meeting() -> None:
    stop = threading.Event()
    blocks: list[bytes] = []
    with wasapi() as api, patch.object(stop, "wait", side_effect=api.wait):
        api.flags = 1
        recording.MeetingAudio(computer_audio=False).record(stop, blocks.append, lambda: None)
        assert api.ticks == api.stop_at, "A microphone discontinuity ended capture before Stop."
        assert b"".join(blocks) == array("h", [1000] * round(2.54 * RATE)).tobytes()
        assert api.calls.count((20, 10)) == 1


def test_a_microphone_dropout_preserves_timing_and_other_speakers_until_stop() -> None:
    for computer_audio in (False, True):
        stop = threading.Event()
        blocks: list[bytes] = []
        with wasapi() as api, patch.object(stop, "wait", side_effect=api.wait):
            api.dropouts = {(30, 48_000): 9_600}
            recording.MeetingAudio(computer_audio=computer_audio).record(stop, blocks.append, lambda: None)
            assert api.ticks == api.stop_at
            audio = array("h", b"".join(blocks))
            normal, gap = (3000, 2000) if computer_audio else (1000, 0)
            assert audio == array("h", [normal] * RATE + [gap] * (RATE // 5) + [normal] * round(1.34 * RATE))
            assert api.calls.count((20, 10)) == api.calls.count((20, 11)) == 1


def test_meeting_service_saves_speech_after_a_microphone_dropout(tmp_path: Path) -> None:
    class Capture:
        __slots__ = ()

        def record(self, stop: threading.Event, consume: Callable[[bytes], None], ready: Callable[[], None]) -> None:
            with wasapi() as api, patch.object(stop, "wait", side_effect=api.wait):
                api.dropouts = {(30, 48_000): 9_600}
                recording.MeetingAudio().record(stop, consume, ready)
                assert api.ticks == api.stop_at

    with fake_endpoint() as endpoint:
        service = Meetings(settings(endpoint.url), lambda source: Capture(), archive=MeetingArchive(tmp_path))
        try:
            service.start()
            wait_for(lambda: service.snapshot().phase in ("saved", "interrupted"))
            state = service.snapshot()
            assert state.phase == "saved", state.detail
            assert state.seconds == 2.54 and state.pending_seconds == 0
            assert "hello there" in Path(state.path).read_text(encoding="utf-8")
            assert len(endpoint.received) == 1
        finally:
            service.close()


def test_cancelling_during_device_setup_never_starts_capture() -> None:
    stop = threading.Event()
    stop.set()
    with wasapi() as api:
        recording.MeetingAudio().record(stop, lambda pcm: None, lambda: None)
        assert not any(index == 10 for _, index in api.calls)
        assert (20, 2) in api.calls and (21, 2) in api.calls


def test_a_failed_second_device_start_releases_both_devices_and_stops_the_first() -> None:
    with wasapi() as api:
        api.fail = (21, 10)
        with unittest.TestCase().assertRaises(OSError):
            recording.MeetingAudio().record(threading.Event(), lambda pcm: None, lambda: None)
        assert (20, 11) in api.calls
        for handle in (1, 10, 11, 20, 21, 30, 31):
            assert api.calls.count((handle, 2)) == 1


def test_packet_clock_errors_return_the_driver_buffer_before_releasing_devices() -> None:
    stop = threading.Event()
    with wasapi() as api, patch.object(stop, "wait", side_effect=api.wait):
        api.flags = 4
        with unittest.TestCase().assertRaisesRegex(OSError, "audio clock"):
            recording.MeetingAudio().record(stop, lambda pcm: None, lambda: None)
        assert api.calls.count((30, 4)) == 2
        assert api.calls.index((30, 4)) < api.calls.index((30, 2))
        assert (20, 11) in api.calls and (21, 11) in api.calls


def test_digital_silence_is_kept() -> None:
    stop = threading.Event()
    blocks: list[bytes] = []
    with wasapi() as api, patch.object(stop, "wait", side_effect=api.wait):
        api.flags = 2
        recording.MeetingAudio(computer_audio=False).record(stop, blocks.append, lambda: None)
        result = b"".join(blocks)
        assert result[:320] == array("h", [1000] * 160).tobytes()
        assert not any(result[320:])
