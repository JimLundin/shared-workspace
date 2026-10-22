"""Continuous sample clocks, packet boundaries and bounded mixing, without a device."""

import math
from array import array
from unittest import TestCase

from assistant.llm import RATE
from assistant.pcm import Converter, Mixer, PcmFormat


def test_resampling_is_identical_across_arbitrary_packet_boundaries() -> None:
    for rate in (44_100, 48_000, 8_000):
        pcm = array("f", [math.sin(index / 20) * 0.2 for index in range(rate * 2)]).tobytes()
        format = PcmFormat(rate, 2, 32, floating=True)
        expected = Converter(format).convert(pcm)
        converter = Converter(format)
        actual = b"".join(converter.convert(pcm[i:i + 136]) for i in range(0, len(pcm), 136))
        assert actual == expected
        assert abs(len(actual) // 2 - RATE) <= 2


def test_native_formats_downmix_clip_and_reject_invalid_samples() -> None:
    for bits in (16, 24, 32):
        scale = 1 << (bits - 16)
        pcm = b"".join((sample * scale).to_bytes(bits // 8, "little", signed=True) for sample in (1000, 3000, -4000, 2000))
        assert Converter(PcmFormat(RATE, 2, bits)).convert(pcm) == array("h", [2000, -1000]).tobytes()
    floating = Converter(PcmFormat(RATE, 1, 32, floating=True))
    assert floating.convert(array("f", [2, -2]).tobytes()) == array("h", [32767, -32768]).tobytes()
    with TestCase().assertRaises(ValueError):
        floating.convert(array("f", [math.nan]).tobytes())
    with TestCase().assertRaises(ValueError):
        Converter(PcmFormat(RATE, 1, 16)).convert(b"\x00")


def test_resampling_and_mixing_keep_precise_positions_after_eight_hours() -> None:
    converter = Converter(PcmFormat(48_000, 1, 16))
    converter.input_frames = 8 * 3600 * 48_000
    converter.output_frames = 8 * 3600 * RATE
    converter.last = 1234
    assert converter.convert(array("h", [1234] * 480).tobytes()) == array("h", [1234] * 160).tobytes()
    mixer = Mixer()
    mixer.cursor = 8 * 3600 * RATE
    for _ in range(200):
        beginning = mixer.cursor
        mixer.add(beginning, array("h", [1000] * 160).tobytes())
        mixer.add(beginning + 80, array("h", [3000] * 160).tobytes())
        assert mixer.take(beginning + 240) == array("h", [1000] * 80 + [4000] * 80 + [3000] * 80).tobytes()
        assert len(mixer.samples) == RATE * 2
    assert not any(mixer.samples)


def test_mixer_pads_absent_playback_clips_peaks_and_reports_overruns() -> None:
    mixer = Mixer()
    mixer.add(2, array("h", [30000, -30000]).tobytes())
    mixer.add(2, array("h", [30000, -30000]).tobytes())
    assert mixer.take(6) == array("h", [0, 0, 32767, -32768, 0, 0]).tobytes()
    with TestCase().assertRaisesRegex(OSError, "arrived too late"):
        mixer.add(0, array("h", [1000] * 10).tobytes())
    with TestCase().assertRaisesRegex(OSError, "fell behind"):
        mixer.add(mixer.cursor + RATE * 2, b"\x01\x00")
    with TestCase().assertRaisesRegex(OSError, "clock jumped"):
        mixer.take(mixer.cursor + RATE * 60)
