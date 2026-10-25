"""Quiet blue and lilac glass, with opaque ink and a dark solid fallback."""

from dataclasses import dataclass
from typing import Final, assert_never

from .notifications import Tone

INK: Final = "#f6f3ff"
SECONDARY: Final = "#e0dced"
USER: Final = "#c7dcff"
SURFACE: Final = "#262337"
EDGE: Final = "#716a87"


@dataclass(frozen=True, slots=True)
class Ink:
    foreground: str = INK
    secondary: str = SECONDARY
    link: str = USER
    error: str = "#ffb7b2"


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
