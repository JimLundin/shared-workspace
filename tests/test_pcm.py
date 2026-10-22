"""Continuous sample clocks, packet boundaries and bounded mixing, without a device."""

import math
from array import array
from unittest import TestCase

from assistant.llm import RATE
from assistant.pcm import Converter, Mixer, PcmFormat, quiet_tail


def test_resampling_is_identical_across_arbitrary_packet_boundaries() -> None:
    for rate in (44_100, 48_000, 8_000):
        # Short, uneven packets exercise the carried sample and fractional clock.
        pcm = array("f", [math.sin(index / 20) * 0.2 for index in range(194)]).tobytes()
        format = PcmFormat(rate, 2, 32, floating=True)
        expected = Converter(format).convert(pcm)
        for packet in (8, 24, 136):
            converter = Converter(format)
            actual = b"".join(converter.convert(pcm[i:i + packet]) for i in range(0, len(pcm), packet))
            assert actual == expected
            assert abs(len(actual) // 2 - 97 * RATE / rate) <= 2


def test_resampling_interpolates_known_samples_and_preserves_native_sample_clocks() -> None:
    # An independent oracle: the new samples lie halfway between their neighbors.
    converter = Converter(PcmFormat(8_000, 1, 16))
    assert converter.convert(array("h", [0, 1000, -1000, 0]).tobytes()) == array("h", [0, 500, 1000, 0, -1000, -500, 0]).tobytes()
    native = Converter(PcmFormat(RATE, 1, 16))
    frames = 0
    for values in ([100, -100, 200], [], [300, -300]):
        packet = array("h", values).tobytes()
        assert native.convert(packet) == packet
        frames += len(values)
        # WASAPI uses these clocks to place each packet on the shared timeline.
        assert native.input_frames == native.output_frames == frames


def test_native_formats_downmix_clip_and_reject_invalid_samples() -> None:
    for bits in (16, 24, 32):
        scale = 1 << (bits - 16)
        pcm = b"".join((sample * scale).to_bytes(bits // 8, "little", signed=True) for sample in (1000, 3000, -4000, 2000))
        assert Converter(PcmFormat(RATE, 2, bits)).convert(pcm) == array("h", [2000, -1000]).tobytes()
    floating = Converter(PcmFormat(RATE, 1, 32, floating=True))
    assert floating.convert(array("f", [.75, 2, -2]).tobytes()) == array("h", [24576, 32767, -32768]).tobytes()
    assert Converter(PcmFormat(RATE, 1, 32)).convert(array("i", [3 * 65536 + 32768]).tobytes()) == array("h", [4]).tobytes()
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


def test_mixer_accepts_an_exactly_full_buffer_and_one_overlapping_sample() -> None:
    mixer = Mixer()
    mixer.add(RATE * 2 - 1, array("h", [1234]).tobytes())
    assert mixer.take(RATE * 2) == bytes(RATE * 4 - 2) + array("h", [1234]).tobytes()
    mixer.add(mixer.cursor - 1, array("h", [111, 222, 333]).tobytes())
    assert mixer.take(mixer.cursor + 2) == array("h", [222, 333]).tobytes()
    with TestCase().assertRaisesRegex(OSError, "arrived too late"):
        mixer.add(mixer.cursor - 2, array("h", [111, 222, 333]).tobytes())


def test_format_limits_and_quiet_tail_keep_boundary_samples() -> None:
    for rate in (8_000, 384_000):
        for channels in (1, 8):
            assert PcmFormat(rate, channels, 16).frame_bytes == channels * 2
    for rate, channels in ((7_999, 1), (384_001, 1), (16_000, 0), (16_000, 9)):
        with TestCase().assertRaises(ValueError):
            PcmFormat(rate, channels, 16)
    assert quiet_tail(array("h", [239, -239]).tobytes())
    for sample in (240, -240):
        assert not quiet_tail(array("h", [sample]).tobytes())
    assert quiet_tail(array("h", [1000]).tobytes() + bytes(RATE))
