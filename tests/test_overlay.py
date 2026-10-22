"""The learning overlay follows the layout and remembers the user's tray choice."""

from pathlib import Path

from assistant.overlay import KeyHighlights, OverlayState, fit_position, load_enabled, load_position, nearest_work_area, save_enabled, save_position
from miryoku.layout import Layer, Scancode
from winapi.shapes import Rect

Q, M = Scancode(0x10), Scancode(0x32)


def test_quick_tap_is_visible_for_at_least_120_milliseconds() -> None:
    highlights = KeyHighlights()
    highlights.update(frozenset({Q}), 1.0)
    assert highlights.visible(1.0) == frozenset({Q})
    assert highlights.delay(1.0) is None  # held keys need no timer
    highlights.update(frozenset(), 1.01)
    assert highlights.visible(1.119) == frozenset({Q})
    assert highlights.delay(1.119) in (1, 2)
    assert highlights.visible(1.121) == frozenset()
    assert highlights.delay(1.121) is None


def test_all_chord_keys_stay_lit_and_long_holds_clear_on_release() -> None:
    highlights = KeyHighlights()
    highlights.update(frozenset({M}), 1.0)
    highlights.update(frozenset({M, Q}), 1.2)
    assert highlights.visible(60.0) == frozenset({M, Q})
    assert highlights.delay(60.0) is None
    highlights.update(frozenset({M}), 60.0)
    assert highlights.visible(60.0) == frozenset({M})
    highlights.update(frozenset(), 60.1)
    assert highlights.visible(60.1) == frozenset()
    assert highlights.delay(60.1) is None


def test_repressing_during_a_flash_gets_a_new_deadline() -> None:
    highlights = KeyHighlights()
    highlights.update(frozenset({Q}), 1.0)
    highlights.update(frozenset(), 1.01)
    highlights.update(frozenset({Q}), 1.1)
    assert highlights.visible(1.13) == frozenset({Q})
    highlights.update(frozenset(), 1.14)
    assert highlights.visible(1.219) == frozenset({Q})
    assert highlights.visible(1.221) == frozenset()


def test_independent_taps_expire_in_order_without_extending_each_other() -> None:
    highlights = KeyHighlights()
    highlights.update(frozenset({Q}), 1.0)
    highlights.update(frozenset(), 1.01)
    highlights.update(frozenset({M}), 1.05)
    highlights.update(frozenset(), 1.06)
    assert highlights.visible(1.119) == frozenset({Q, M})
    assert highlights.visible(1.121) == frozenset({M})
    assert highlights.visible(1.171) == frozenset()


def test_reset_clears_both_held_keys_and_tap_flashes_immediately() -> None:
    highlights = KeyHighlights()
    highlights.update(frozenset({Q, M}), 1.0)
    highlights.update(frozenset({M}), 1.01)
    highlights.clear()
    assert highlights.visible(1.02) == frozenset()
    assert highlights.delay(1.02) is None
    highlights.update(frozenset({M}), 1.03)
    highlights.update(frozenset(), 1.04)
    assert highlights.visible(1.149) == frozenset({M})
    assert highlights.visible(1.151) == frozenset()


def test_overlay_tracks_physical_keys_across_layers_and_while_hidden() -> None:
    overlay = OverlayState(layout_on=True)
    overlay.key(M, True)
    overlay.indicate(Layer.SYM, locked=False, caps_word=False)
    assert overlay.pressed == frozenset({M})
    overlay.enabled = False
    overlay.key(Q, True)
    overlay.key(M, False)
    overlay.enabled = True
    assert overlay.pressed == frozenset({Q})
    overlay.key(Q, True)  # repeat
    overlay.key(M, False)  # duplicate release
    assert overlay.pressed == frozenset({Q})


def test_layout_resets_and_unknown_keys_cannot_leave_stale_highlights() -> None:
    overlay = OverlayState(layout_on=True)
    overlay.key(Scancode(0x7FFF), True)
    assert overlay.pressed == frozenset()
    overlay.key(Q, True)
    overlay.set_layout(False)
    overlay.key(M, True)
    assert overlay.pressed == frozenset()
    overlay.set_layout(True)
    overlay.key(M, True)
    overlay.set_layout(True)  # a restarted layout announces ON again
    assert overlay.pressed == frozenset()
    overlay.key(Q, True)
    overlay.key(Scancode(0), False)  # reset before replaying current holds
    assert overlay.pressed == frozenset()


def test_overlay_stays_visible_on_the_base_and_held_layers() -> None:
    overlay = OverlayState()
    assert not bool(overlay.visible)
    overlay.set_layout(True)
    assert bool(overlay.visible)
    assert overlay.title == "BASE"
    overlay.indicate(Layer.TOOLS, locked=False, caps_word=False)
    assert bool(overlay.visible)
    assert overlay.layer is Layer.TOOLS
    assert overlay.title == "TOOLS"
    overlay.indicate(None, locked=False, caps_word=False)
    assert bool(overlay.visible)
    assert overlay.title == "BASE"


def test_overlay_tracks_locked_layers_and_clears_them_when_layout_turns_off() -> None:
    overlay = OverlayState(layout_on=True)
    overlay.indicate(Layer.NAV, locked=True, caps_word=True)
    assert overlay.title == "NAV · locked · CAPS WORD"
    overlay.set_layout(False)
    assert not bool(overlay.visible)
    overlay.set_layout(True)
    assert bool(overlay.visible)
    assert overlay.layer is None
    assert overlay.title == "BASE"


def test_hiding_overlay_survives_layer_changes_and_switching_layout_on() -> None:
    overlay = OverlayState(enabled=False, layout_on=True)
    overlay.indicate(Layer.NUM, locked=True, caps_word=False)
    assert not bool(overlay.visible)
    overlay.set_layout(False)
    overlay.set_layout(True)
    assert not bool(overlay.visible)
    overlay.enabled = True
    assert bool(overlay.visible)


def test_overlay_preference_is_remembered(tmp_path: Path) -> None:
    preference = tmp_path / "settings" / "overlay.json"
    assert load_enabled(preference)
    save_enabled(False, preference)
    assert not load_enabled(preference)
    save_enabled(True, preference)
    assert load_enabled(preference)
    assert not preference.with_suffix(".tmp").exists()


def test_invalid_overlay_preference_uses_the_visible_default(tmp_path: Path) -> None:
    preference = tmp_path / "overlay.json"
    for text in ('not json', '[]', '{}', '{"enabled": "false"}', '{"enabled": 0}'):
        preference.write_text(text, encoding="utf-8")
        assert load_enabled(preference)


def test_drag_position_and_tray_visibility_are_saved_independently(tmp_path: Path) -> None:
    preference = tmp_path / "settings" / "overlay.json"
    position = preference.with_name("overlay-position.json")
    assert load_position(position) is None
    save_enabled(False, preference)
    save_position((-730, -1200), position)
    assert not load_enabled(preference)
    assert load_position(position) == (-730, -1200)
    save_enabled(True, preference)
    assert load_position(position) == (-730, -1200)
    assert not position.with_suffix(".tmp").exists()


def test_invalid_drag_position_uses_default_placement(tmp_path: Path) -> None:
    position = tmp_path / "overlay-position.json"
    for text in ('not json', '[]', '{}', '{"x": 100}', '{"x": false, "y": 20}', '{"x": 10.5, "y": 20}', '{"x": 10, "y": "20"}'):
        position.write_text(text, encoding="utf-8")
        assert load_position(position) is None


def test_overlay_can_be_placed_on_monitors_left_of_and_above_primary() -> None:
    left = Rect(-1440, -1440, 0, 1120)
    above = Rect(0, -1440, 2560, 0)
    primary = Rect(0, 0, 1920, 1200)
    areas = (left, above, primary)
    for position, expected in (((-720, -1000), left), ((1000, -800), above), ((960, 600), primary)):
        assert nearest_work_area(position, areas) == expected
        assert fit_position(position, 780, 300, expected) == position


def test_overlay_keeps_its_handle_reachable_after_monitor_is_unplugged() -> None:
    primary = Rect(0, 0, 1920, 1200)
    saved = (-720, -1000)
    work = nearest_work_area(saved, (primary,))
    assert fit_position(saved, 780, 300, work) == (390, 0)
    assert fit_position((1910, 1190), 780, 300, work) == (1530, 900)
    assert fit_position(saved, 2400, 1400, work) == (960, 0)
