"""Bounded PCM conversion and mixing for continuous, clock-aligned recording."""

import math
from array import array
from dataclasses import dataclass

from .llm import RATE


@dataclass(frozen=True, slots=True)
class PcmFormat:
    rate: int
    channels: int
    bits: int
    floating: bool = False

    def __post_init__(self) -> None:
        if not 8_000 <= self.rate <= 384_000 or not 1 <= self.channels <= 8:
            raise ValueError("Unsupported audio sample rate or channel count.")
        if self.bits not in ((32,) if self.floating else (16, 24, 32)):
            raise ValueError("Unsupported audio sample format.")

    @property
    def frame_bytes(self) -> int:
        return self.channels * (self.bits // 8)


class Converter:
    """Downmix native packets and resample with one carried sample, without drift.

    Positions use integer ratios, so packet boundaries and hours of elapsed time
    cannot accumulate rounding error. WASAPI supplies little-endian Windows PCM.
    """

    __slots__ = ("format", "input_frames", "last", "output_frames")

    def __init__(self, format: PcmFormat) -> None:
        self.format = format
        self.input_frames = self.output_frames = 0
        self.last = 0.0

    def convert(self, pcm: bytes) -> bytes:
        format = self.format
        if len(pcm) % format.frame_bytes:
            raise ValueError("The audio packet ended inside a sample.")
        if not pcm:
            return b""
        if format == PcmFormat(RATE, 1, 16):
            self.input_frames += len(pcm) // 2
            self.output_frames += len(pcm) // 2
            return pcm
        if format.bits == 24:
            samples = [float(int.from_bytes(pcm[i:i + 3], "little", signed=True)) / 256 for i in range(0, len(pcm), 3)]
        elif format.floating:
            samples = [float(value) * 32768 for value in array("f", pcm)]
        elif format.bits == 16:
            samples = [float(value) for value in array("h", pcm)]
        else:
            samples = [float(value) / 65536 for value in array("i", pcm)]
        if not all(math.isfinite(sample) for sample in samples):
            raise ValueError("The audio device returned a non-finite sample.")
        mono = [sum(samples[i:i + format.channels]) / format.channels for i in range(0, len(samples), format.channels)]
        beginning = self.input_frames
        if beginning:
            mono.insert(0, self.last)
            beginning -= 1
        self.input_frames += len(pcm) // format.frame_bytes
        self.last = mono[-1]
        result = array("h")
        while True:
            index, fraction = divmod(self.output_frames * format.rate, RATE)
            local = index - beginning
            if local >= len(mono) or (fraction and local + 1 >= len(mono)):
                break
            sample = mono[local]
            if fraction:
                sample += (mono[local + 1] - sample) * fraction / RATE
            result.append(max(-32768, min(32767, round(sample))))
            self.output_frames += 1
        return result.tobytes()


class Mixer:
    """A two-second ring on the shared device clock; absent playback is silence."""

    __slots__ = ("cursor", "samples")

    def __init__(self) -> None:
        self.samples = array("i", [0]) * (RATE * 2)
        self.cursor = 0

    def add(self, start: int, pcm: bytes) -> None:
        samples = array("h", pcm)
        if start + len(samples) > self.cursor + len(self.samples):
            raise OSError("Recording fell behind the audio device. The captured part has been saved; start a new recording.")
        offset = max(0, self.cursor - start)
        if self.cursor and offset > 1:
            raise OSError("Audio arrived too late to keep a complete recording. The captured part has been saved; check the audio device.")
        for index in range(offset, len(samples)):
            self.samples[(start + index) % len(self.samples)] += samples[index]

    def take(self, until: int) -> bytes:
        until = max(self.cursor, until)
        if until - self.cursor > len(self.samples):
            raise OSError("The audio clock jumped, possibly during sleep. The captured part has been saved.")
        result = array("h")
        for frame in range(self.cursor, until):
            index = frame % len(self.samples)
            result.append(max(-32768, min(32767, self.samples[index])))
            self.samples[index] = 0
        self.cursor = until
        return result.tobytes()


def quiet_tail(pcm: bytes) -> bool:
    """Prefer a half-second pause near a chunk boundary, without omitting speech."""
    return all(abs(sample) < 240 for sample in array("h", pcm[-RATE:]))
