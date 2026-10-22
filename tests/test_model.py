"""The vocabulary the layout is written in: what it can and can't express."""

from miryoku.layout import Command, Key, Layer, Mod


def test_layers_have_no_base() -> None:
    # Holding "the base layer" means nothing, so it can't be written: no layer held is None.
    assert [layer.name for layer in Layer] == ["NAV", "NUM", "SYM", "TOOLS", "APPS"]


def test_keys_carry_their_windows_details() -> None:
    assert Key.A.vk == 0x41
    assert not Key.A.extended
    assert Key.LEFT.extended
    assert Mod.GUI.extended


def test_engine_commands_are_named_values() -> None:
    assert {command.name for command in Command} == {"NOTHING", "LOCK_LAYER"}
