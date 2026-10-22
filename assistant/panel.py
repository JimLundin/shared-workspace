"""Remembered placement for the personal agent panel, in physical screen pixels."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from usage import DIRECTORY
from winapi.shapes import Rect

PREFERENCE: Final = DIRECTORY / "agent-panel.json"
Edge = Literal["left", "right"]


@dataclass(frozen=True, slots=True)
class PanelPreferences:
    # Frame position and client size, in physical pixels on the Windows desktop.
    bounds: Rect | None = None
    pinned: bool = True
    edge: Edge | None = "right"
    translucent: bool = True
    rail_width: int = 360  # logical pixels; placement remains in physical pixels


def load_preferences(path: Path = PREFERENCE) -> PanelPreferences:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PanelPreferences()
    if not isinstance(data, dict):
        return PanelPreferences()
    values = cast("dict[str, object]", data)
    x, y, width, height = (values.get(key) for key in ("x", "y", "width", "height"))
    bounds = None
    if type(x) is int and type(y) is int and type(width) is int and type(height) is int and width > 0 and height > 0:
        bounds = Rect(x, y, x + width, y + height)
    pinned = values.get("pinned")
    edge = values.get("edge", "right")
    translucent = values.get("translucent")
    rail_width = values.get("rail_width")
    return PanelPreferences(
        bounds, pinned if isinstance(pinned, bool) else True,
        edge if edge in ("left", "right", None) else "right",
        translucent if isinstance(translucent, bool) else True,
        rail_width if type(rail_width) is int and 300 <= rail_width <= 640 else 360,
    )


def save_preferences(preferences: PanelPreferences, path: Path = PREFERENCE) -> None:
    values: dict[str, int | bool | str | None] = {
        "pinned": preferences.pinned, "edge": preferences.edge,
        "translucent": preferences.translucent, "rail_width": preferences.rail_width,
    }
    if preferences.bounds is not None:
        bounds = preferences.bounds
        values.update(x=bounds.left, y=bounds.top, width=bounds.width, height=bounds.height)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(values) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def fit_panel(bounds: Rect, work: Rect, minimum: tuple[int, int], *, margin: int = 16, title_height: int = 40) -> Rect:
    """Fit on the nearest remaining monitor, leaving room for the native title bar."""
    width = min(max(minimum[0], bounds.width), max(1, work.width - margin * 2))
    height = min(max(minimum[1], bounds.height), max(1, work.height - margin * 2 - title_height))
    left = max(work.left + margin, min(bounds.left, work.right - margin - width))
    top = max(work.top + margin, min(bounds.top, work.bottom - margin - height - title_height))
    return Rect(left, top, left + width, top + height)


def dock_panel(width: int, work: Rect, edge: Edge) -> Rect:
    """A rail occupies the work area's height, leaving taskbars accessible."""
    width = min(max(1, width), work.width)
    left = work.left if edge == "left" else work.right - width
    return Rect(left, work.top, left + width, work.bottom)


def snap_panel(bounds: Rect, work: Rect, minimum: tuple[int, int], *, distance: int = 28) -> tuple[Rect, Edge | None]:
    """Snap only near a side; elsewhere keep a free window within reach."""
    left, right = abs(bounds.left - work.left), abs(work.right - bounds.right)
    if min(left, right) <= distance:
        edge: Edge = "left" if left < right else "right"
        return dock_panel(max(minimum[0], bounds.width), work, edge), edge
    return fit_panel(bounds, work, minimum, margin=0, title_height=0), None
