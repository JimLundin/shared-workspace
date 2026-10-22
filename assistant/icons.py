"""The tray icon: a coloured dot whose colour says what the assistant is doing."""

import math
from enum import Enum


class State(Enum):
    """What the tray icon shows. The value is its colour (red, green, blue)."""

    READY = (46, 160, 120)
    """Layout on; nothing running."""
    LAYOUT_OFF = (140, 140, 140)
    """The layout is switched off or not running; tools still work from the tray."""
    LISTENING = (220, 50, 50)
    RECORDING = (230, 64, 85)
    """A meeting is being recorded, including while the agent does other work."""
    WORKING = (235, 160, 30)
    AGENT = (60, 120, 230)
    """The agent is working on a task."""
    NEEDS_INPUT = (230, 185, 75)
    """An approval or question is waiting in the agent panel."""


def dot(size: int, rgb: tuple[int, int, int]) -> bytes:
    """A filled circle with a soft edge on a transparent square: BGRA, top row first."""
    r, g, b = rgb
    centre, radius = (size - 1) / 2, size * 0.42
    pixels = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            coverage = min(max(radius - math.hypot(x - centre, y - centre) + 0.5, 0.0), 1.0)
            if coverage:
                i = (y * size + x) * 4
                pixels[i : i + 4] = bytes((b, g, r, round(255 * coverage)))
    return bytes(pixels)
