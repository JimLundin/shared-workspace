"""Quiet semantic accents over a dark material; labels also identify each role."""

from dataclasses import dataclass
from typing import Final, assert_never

from .notifications import Tone

GLASS_KEY: Final = "#000000"
INK: Final = "#f1f3f8"
SECONDARY: Final = "#c8cedc"
USER: Final = "#a9cbff"


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


def palette(tone: Tone) -> Palette:
    match tone:
        case Tone.USER:
            return Palette("#202a38", "#475f7a", USER)
        case Tone.THINKING:
            return Palette("#242131", "#58516e", "#c4b4ff")
        case Tone.ACTION:
            return Palette("#1c2930", "#42606b", "#92ddeb")
        case Tone.REPLY:
            return Palette("#1e2b29", "#45655c", "#a1e5c5")
        case Tone.QUESTION:
            return Palette("#30291e", "#726346", "#f5d596")
        case Tone.ERROR:
            return Palette("#312124", "#795354", "#ffaba4")
        case Tone.RECORDING:
            return Palette("#2d2230", "#715570", "#f2b5de")
        case _:
            assert_never(tone)
