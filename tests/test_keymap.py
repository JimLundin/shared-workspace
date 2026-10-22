"""The key map: what each physical key does, per layer; and the indicator's words."""

from assistant.keymap import ROWS, Kind, indicator_text, keymap
from miryoku.layout import Layer
from protocol import Tool


def caps(layer: Layer | None) -> dict[str, tuple[str, str, Kind]]:
    return {cap.physical: (cap.label, cap.hold, cap.kind) for row in keymap(layer) for cap in row}


def test_rows_are_the_nordic_iso_keyboard() -> None:
    assert [len(row) for row in ROWS] == [13, 12, 13, 11]
    assert ROWS[0][0][1] == "§"
    assert ROWS[3][0][1] == "<"


def test_base_layer() -> None:
    base = caps(None)
    assert base["2"] == ("Q", "", Kind.KEY)
    assert base["Q"] == ("A", "Win", Kind.KEY)  # home row mod: tap A, hold Win
    assert base["C"] == ("Space", "Nav", Kind.KEY)
    assert base["M"] == ("Enter", "Sym", Kind.KEY)
    assert base[","] == ("Bksp", "Num", Kind.KEY)
    assert base["."] == ("Del", "Apps", Kind.KEY)
    assert base["-"] == ("", "", Kind.BLANK)
    assert base["§"] == ("On/off", "hold: quit", Kind.SPECIAL)
    assert base["B"] == ("Talk", "hold", Kind.TOOL)
    assert base["N"] == ("Agent", "hold", Kind.TOOL)
    assert base["Y"] == ("", "", Kind.BLANK)


def test_agent_button_sits_beside_talk_and_stays_visible_on_every_layer() -> None:
    layers: list[Layer | None] = [None, *list(Layer)]
    for layer in layers:
        thumbs = keymap(layer)[-1]
        talk = next(i for i, cap in enumerate(thumbs) if cap.physical == "B")
        agent = thumbs[talk + 1]
        assert (agent.physical, agent.label, agent.hold, agent.kind) == ("N", "Agent", "hold", Kind.TOOL)


def test_layers_show_their_keys_and_the_thumb_holding_them() -> None:
    nav = caps(Layer.NAV)
    assert nav["O"] == ("←", "", Kind.KEY)
    assert nav["8"] == ("Redo", "", Kind.KEY)
    assert nav["Q"] == ("Win", "", Kind.MOD)
    assert nav["C"][2] is Kind.HELD
    sym = caps(Layer.SYM)
    assert sym["2"] == ("{", "", Kind.KEY)
    assert sym["M"][2] is Kind.HELD
    assert caps(Layer.NUM)[","][2] is Kind.HELD


def test_tools_and_apps_layers() -> None:
    tools = caps(Layer.TOOLS)
    assert tools["9"] == ("Fix", "", Kind.TOOL)
    assert tools["I"] == ("Talk tidy", "hold", Kind.TOOL)
    assert tools["V"][2] is Kind.HELD
    assert tools["M"] == ("Talk", "hold", Kind.TOOL)
    apps = caps(Layer.APPS)
    assert apps["2"] == ("Terminal", "", Kind.APP)
    assert apps["Caps"] == ("Snap ←", "", Kind.TOOL)
    assert apps["."][2] is Kind.HELD


def test_every_tool_has_a_label() -> None:
    layers: list[Layer | None] = [None, *list(Layer)]
    labels = {cap.label for layer in layers for row in keymap(layer) for cap in row if cap.kind is Kind.TOOL}
    assert len(labels) == len(Tool)  # every tool is somewhere on the keyboard


def test_indicator_text() -> None:
    assert indicator_text(None, locked=False, caps_word=False) is None
    assert indicator_text(Layer.NAV, locked=False, caps_word=False) == "NAV"
    assert indicator_text(Layer.NUM, locked=True, caps_word=False) == "NUM · locked"
    assert indicator_text(None, locked=False, caps_word=True) == "CAPS WORD"
    assert indicator_text(Layer.SYM, locked=False, caps_word=True) == "SYM · CAPS WORD"
