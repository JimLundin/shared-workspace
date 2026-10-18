"""Window placement and screenshot coordinates: plain arithmetic on rectangles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from winapi.shapes import Rect


def half(work: Rect, side: Literal["left", "right"], outer: Rect, visible: Rect) -> Rect:
    """Where to put a window (its outer rectangle) so what's drawn of it fills one
    half of the work area. Windows positions windows by their outer rectangle, which
    has invisible borders around what's drawn; they're added back on."""
    middle = work.left + work.width // 2
    target = Rect(work.left, work.top, middle, work.bottom) if side == "left" else Rect(middle, work.top, work.right, work.bottom)
    return Rect(
        target.left - (visible.left - outer.left),
        target.top - (visible.top - outer.top),
        target.right + (outer.right - visible.right),
        target.bottom + (outer.bottom - visible.bottom),
    )


def to_monitor(window: Rect, source: Rect, target: Rect) -> Rect:
    """The same place and proportions on another monitor's work area, kept on screen."""
    sx, sy = target.width / source.width, target.height / source.height
    width, height = min(round(window.width * sx), target.width), min(round(window.height * sy), target.height)
    left = target.left + round((window.left - source.left) * sx)
    top = target.top + round((window.top - source.top) * sy)
    left = min(max(left, target.left), target.right - width)
    top = min(max(top, target.top), target.bottom - height)
    return Rect(left, top, left + width, top + height)


def fit(width: int, height: int, longest: int) -> tuple[int, int]:
    """A size no wider or taller than `longest`, same shape; never enlarged."""
    scale = min(1.0, longest / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


@dataclass(frozen=True, slots=True)
class Shot:
    """A screenshot: the screen area it shows, and the size it was scaled to."""

    area: Rect
    width: int
    height: int

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """A point in the image as a point on the screen."""
        return self.area.left + round(x * self.area.width / self.width), self.area.top + round(y * self.area.height / self.height)


def region(start: tuple[int, int], end: tuple[int, int]) -> Rect:
    """The rectangle dragged out between two points, both included."""
    (x1, y1), (x2, y2) = start, end
    return Rect(min(x1, x2), min(y1, y2), max(x1, x2) + 1, max(y1, y2) + 1)
