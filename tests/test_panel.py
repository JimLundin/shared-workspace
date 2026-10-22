"""Placement survives restarts, broken preferences and disconnected monitors."""

from pathlib import Path

from assistant.overlay import nearest_work_area
from assistant.panel import PanelPreferences, dock_panel, fit_panel, load_preferences, save_preferences, snap_panel
from winapi.shapes import Rect


def test_panel_remembers_negative_monitor_position_size_and_pin(tmp_path: Path) -> None:
    path = tmp_path / "agent-panel.json"
    preferences = PanelPreferences(Rect(-1000, 30, -570, 720), pinned=False, edge="left", translucent=False, rail_width=430)
    save_preferences(preferences, path)
    assert load_preferences(path) == preferences
    assert not path.with_suffix(".tmp").exists()


def test_bad_preferences_fall_back_without_losing_a_valid_pin_choice(tmp_path: Path) -> None:
    path = tmp_path / "agent-panel.json"
    assert load_preferences(path) == PanelPreferences()
    for data in ('{', '[]', '{"x": true, "y": 0, "width": 430, "height": 690, "pinned": false}'):
        path.write_text(data, encoding="utf-8")
        expected = PanelPreferences(pinned=False) if '"pinned": false' in data else PanelPreferences()
        assert load_preferences(path) == expected


def test_panel_returns_to_a_remaining_monitor_after_one_is_unplugged() -> None:
    saved = Rect(-1800, 120, -1200, 920)
    remaining = Rect(0, 0, 1280, 720)
    work = nearest_work_area((saved.left, saved.top), [remaining])
    placed = fit_panel(saved, work, (350, 440))
    assert placed.width == 600
    assert work.contains(placed.left, placed.top)
    assert placed.bottom + 40 <= work.bottom


def test_panel_stays_on_a_monitor_to_the_left_of_the_primary() -> None:
    work = Rect(-1920, -200, 0, 840)
    placed = fit_panel(Rect(-500, -180, -70, 510), work, (350, 440))
    assert placed == Rect(-500, -180, -70, 510)


def test_a_rail_snaps_to_either_work_area_edge_without_covering_the_taskbar() -> None:
    work = Rect(-1920, -200, 0, 840)
    left, edge = snap_panel(Rect(-1900, 30, -1540, 670), work, (300, 360))
    assert edge == "left" and left == Rect(-1920, -200, -1560, 840)
    right, edge = snap_panel(Rect(-375, 30, -15, 670), work, (300, 360))
    assert edge == "right" and right == Rect(-360, -200, 0, 840)
    free = Rect(-1000, 30, -640, 670)
    assert snap_panel(free, work, (300, 360)) == (free, None)


def test_snap_distance_scales_and_a_saved_rail_fits_a_smaller_monitor() -> None:
    work = Rect(0, 0, 1280, 720)
    near = Rect(700, 20, 1240, 620)
    assert snap_panel(near, work, (450, 540), distance=28)[1] is None
    assert snap_panel(near, work, (450, 540), distance=42) == (Rect(740, 0, 1280, 720), "right")
    assert dock_panel(1600, work, "right") == work


def test_old_preferences_gain_rail_defaults_and_invalid_display_options_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "agent-panel.json"
    path.write_text('{"x": -1000, "y": 30, "width": 500, "height": 760, "pinned": false}')
    loaded = load_preferences(path)
    assert loaded.bounds == Rect(-1000, 30, -500, 790) and not loaded.pinned
    assert loaded.edge == "right" and loaded.translucent and loaded.rail_width == 360
    path.write_text('{"edge": "top", "translucent": "false", "rail_width": true}')
    assert load_preferences(path) == PanelPreferences()
    free = PanelPreferences(Rect(100, 100, 460, 740), edge=None)
    save_preferences(free, path)
    assert load_preferences(path) == free
