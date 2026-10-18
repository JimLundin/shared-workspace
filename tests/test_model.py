"""The vocabulary the layout is written in: what it can and can't express."""

from __future__ import annotations

from miryoku.layout import Chord, Command, Key, Layer, Mod, TapHold


def test_layers_have_no_base() -> None:
    # Holding "the base layer" means nothing, so it can't be written: no layer held is None.
    assert [layer.name for layer in Layer] == ["NAV", "NUM", "SYM", "TOOLS", "APPS"]


def test_keys_carry_their_windows_details() -> None:
    assert Key.A.vk == 0x41
    assert not Key.A.extended
    assert Key.LEFT.extended
    assert Mod.GUI.extended


def test_a_chord_can_hold_several_modifiers() -> None:
    redo = Chord(Key.Z, frozenset({Mod.CTRL, Mod.SHIFT}))
    assert redo.mods == {Mod.CTRL, Mod.SHIFT}


def test_a_tap_hold_key_can_tap_a_chord() -> None:
    paren = Chord(Key.N9, frozenset({Mod.SHIFT}))
    assert TapHold(paren, Layer.SYM).tap == paren


def test_engine_commands_are_named_values() -> None:
    assert {command.name for command in Command} == {"NOTHING", "LOCK_LAYER"}
