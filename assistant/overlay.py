"""The learning overlay's state, placement and remembered preferences."""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from miryoku.layout import Layer, Scancode
from usage import DIRECTORY
from winapi.shapes import Rect

from .keymap import PHYSICAL_NAMES

PREFERENCE: Final = DIRECTORY / "overlay.json"
POSITION: Final = DIRECTORY / "overlay-position.json"
"""Position is written by the UI thread, independently of the tray preference."""


def _read(path: Path) -> dict[str, object]:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cast("dict[str, object]", data) if isinstance(data, dict) else {}


def _save(data: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_enabled(path: Path = PREFERENCE) -> bool:
    """Start with the learning aid visible unless the user has hidden it."""
    value = _read(path).get("enabled")
    return value if isinstance(value, bool) else True


def save_enabled(enabled: bool, path: Path = PREFERENCE) -> None:
    _save({"enabled": enabled}, path)


def load_position(path: Path = POSITION) -> tuple[int, int] | None:
    """The drag handle's horizontal centre and top, in physical screen pixels."""
    data = _read(path)
    x, y = data.get("x"), data.get("y")
    return (x, y) if type(x) is int and type(y) is int else None


def save_position(position: tuple[int, int], path: Path = POSITION) -> None:
    x, y = position
    _save({"x": x, "y": y}, path)


def nearest_work_area(position: tuple[int, int], areas: Sequence[Rect]) -> Rect:
    """The monitor under the handle, or the nearest remaining one after unplugging."""
    x, y = position

    def distance(area: Rect) -> int:
        return max(area.left - x, 0, x - (area.right - 1)) ** 2 + max(area.top - y, 0, y - (area.bottom - 1)) ** 2

    return min(areas, key=distance)


def fit_position(position: tuple[int, int], width: int, height: int, work: Rect) -> tuple[int, int]:
    """Keep the keyboard and handle on this monitor, with the handle always reachable."""
    x, y = position
    visible_width = min(width, work.width)
    x = max(work.left + visible_width // 2, min(x, work.right - (visible_width - visible_width // 2)))
    y = max(work.top, min(y, work.bottom - height))
    return x, y


class KeyHighlights:
    """Held keys stay lit; a quick tap stays visible for at least 120 milliseconds."""

    __slots__ = ("_held", "_until")

    def __init__(self) -> None:
        self._held: frozenset[Scancode] = frozenset()
        self._until: dict[Scancode, float] = {}

    def update(self, held: frozenset[Scancode], now: float) -> None:
        for key in held - self._held:
            self._until[key] = now + 0.12
        self._held = held

    def visible(self, now: float) -> frozenset[Scancode]:
        self._until = {key: until for key, until in self._until.items() if key in self._held or until > now}
        return self._held | frozenset(self._until)

    def delay(self, now: float) -> int | None:
        """Milliseconds until the next released key goes dark; no polling while held."""
        deadlines = [until for key, until in self._until.items() if key not in self._held]
        return max(1, math.ceil((min(deadlines) - now) * 1000)) if deadlines else None

    def clear(self) -> None:
        self._held = frozenset()
        self._until.clear()


@dataclass(slots=True)
class OverlayState:
    enabled: bool = True
    layout_on: bool = False
    layer: Layer | None = None
    locked: bool = False
    caps_word: bool = False
    pressed: frozenset[Scancode] = frozenset()

    @property
    def visible(self) -> bool:
        return self.enabled and self.layout_on

    @property
    def title(self) -> str:
        parts = [self.layer.name if self.layer else "BASE"]
        if self.layer is not None and self.locked:
            parts.append("locked")
        if self.caps_word:
            parts.append("CAPS WORD")
        return " · ".join(parts)

    def set_layout(self, on: bool) -> None:
        self.layout_on = on
        self.pressed = frozenset()
        if not on:
            self.layer, self.locked, self.caps_word = None, False, False

    def indicate(self, layer: Layer | None, *, locked: bool, caps_word: bool) -> None:
        self.layer, self.locked, self.caps_word = layer, locked, caps_word

    def key(self, scancode: Scancode, down: bool) -> None:
        if not scancode:
            self.pressed = frozenset()
        elif self.layout_on and scancode in PHYSICAL_NAMES:
            self.pressed = self.pressed | {scancode} if down else self.pressed - {scancode}
