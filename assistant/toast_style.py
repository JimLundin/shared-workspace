"""Quiet blue and lilac glass, with opaque ink and a dark solid fallback."""

from dataclasses import dataclass
from typing import Final, assert_never

from .notifications import Tone

GLASS_KEY: Final = "#000000"
INK: Final = "#f6f3ff"
SECONDARY: Final = "#e0dced"
USER: Final = "#c7dcff"
SURFACE: Final = "#262337"
EDGE: Final = "#716a87"


def reveal_ink(progress: float) -> str:
    """New text settles from readable secondary ink to the normal foreground."""
    amount = max(0.0, min(1.0, progress))
    channels = (round(int(SECONDARY[index:index + 2], 16) * (1 - amount) + int(INK[index:index + 2], 16) * amount)
                for index in (1, 3, 5))
    return "#" + "".join(f"{channel:02x}" for channel in channels)


@dataclass(frozen=True, slots=True)
class Palette:
    surface: str
    edge: str
    accent: str
    tint: str


def palette(tone: Tone) -> Palette:
    match tone:
        case Tone.USER | Tone.RECORDING:
            return Palette("#222c42", "#62738e", "#d3e3ff", "#335ba5")
        case Tone.THINKING | Tone.ACTION | Tone.REPLY:
            return Palette(SURFACE, EDGE, "#e8e1ff", "#4e4173")
        case Tone.QUESTION:
            return Palette(SURFACE, EDGE, "#f2ddb9", "#4e4173")
        case Tone.ERROR:
            return Palette(SURFACE, EDGE, "#ffb7b2", "#4e4173")
        case _:
            assert_never(tone)
