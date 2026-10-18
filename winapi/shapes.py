"""Plain value types the bindings return. No ctypes: importable anywhere."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    """A screen rectangle in physical pixels; `right` and `bottom` are exclusive."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True, slots=True)
class Window:
    """A top-level window, as the taskbar would show it."""

    handle: int
    title: str
    exe: str
    """The program's file name, lowercase: `windowsterminal.exe`."""
    minimized: bool


def utf16_units(text: str) -> list[int]:
    """Windows types text as UTF-16 code units: two for characters past U+FFFF."""
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]
