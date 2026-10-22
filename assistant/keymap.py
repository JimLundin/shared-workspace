"""What each physical key does, per layer, as the key map window draws it; and the
words the layer indicator shows. Plain Python, read from the layout's own tables."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from miryoku.layout import (
    BASE,
    COPY,
    CUT,
    ESCAPE,
    PASTE,
    REDO,
    UNDO,
    VOICE_KEYS,
    Action,
    Chord,
    Command,
    Key,
    Layer,
    Mod,
    Scancode,
    TapHold,
    Unmapped,
    action,
    character,
    slot,
)
from protocol import App, Tool


def _row(first: int, names: str) -> tuple[tuple[Scancode, str], ...]:
    return tuple((Scancode(first + i), name) for i, name in enumerate(names.split()))


ROWS: Final = (
    ((Scancode(0x29), "\u00a7"), *_row(0x02, "1 2 3 4 5 6 7 8 9 0 + \u00b4")),
    _row(0x10, "Q W E R T Y U I O P \u00c5 \u00a8"),
    ((Scancode(0x3A), "Caps"), *_row(0x1E, "A S D F G H J K L \u00d6 \u00c4"), (Scancode(0x2B), "'")),
    ((Scancode(0x56), "<"), *_row(0x2C, "Z X C V B N M , . -")),
)
"""The keyboard's letter block, row by row: scancode and the Nordic keycap's name."""

PHYSICAL_NAMES: Final = {scancode: name for row in ROWS for scancode, name in row}


class Kind(Enum):
    """What a key does, for its colour."""

    KEY = auto()
    MOD = auto()
    LAYER = auto()
    TOOL = auto()
    APP = auto()
    SPECIAL = auto()
    """The escape key: on/off and quit."""
    HELD = auto()
    """The thumb holding the layer shown."""
    BLANK = auto()


@dataclass(frozen=True, slots=True)
class Cap:
    physical: str
    """The keycap's own name (Nordic)."""
    label: str
    """What it does on this layer; for a tap-hold key, the tap."""
    hold: str
    """What it does held, if that's something else."""
    kind: Kind


_KEY_NAMES: Final = {
    Key.SPACE: "Space", Key.ENTER: "Enter", Key.BACKSPACE: "Bksp", Key.DELETE: "Del", Key.TAB: "Tab", Key.ESC: "Esc",
    Key.CAPS_LOCK: "Caps", Key.INSERT: "Ins", Key.HOME: "Home", Key.END: "End", Key.PAGE_UP: "PgUp", Key.PAGE_DOWN: "PgDn",
    Key.LEFT: "←", Key.RIGHT: "→", Key.UP: "↑", Key.DOWN: "↓",
}  # fmt: skip
_CHORD_NAMES: Final = {UNDO: "Undo", REDO: "Redo", COPY: "Copy", PASTE: "Paste", CUT: "Cut"}
_MOD_NAMES: Final = {Mod.GUI: "Win", Mod.ALT: "Alt", Mod.CTRL: "Ctrl", Mod.SHIFT: "Shift"}
TOOL_NAMES: Final = {
    Tool.DICTATE: "Talk", Tool.DICTATE_CLEAN: "Talk tidy", Tool.DICTATE_COMMAND: "Talk cmd",
    Tool.REWRITE_VOICE: "Rewrite", Tool.FIX: "Fix", Tool.TRANSLATE: "Translate", Tool.SHORTER: "Shorter", Tool.FORMAL: "Formal",
    Tool.ASK: "Ask", Tool.SCREEN_TEXT: "Screen text", Tool.SCREEN_ASK: "Screen ask",
    Tool.AGENT_VOICE: "Agent talk", Tool.AGENT_TYPED: "Agent", Tool.STOP: "Stop",
    Tool.SNAP_LEFT: "Snap ←", Tool.SNAP_RIGHT: "Snap →", Tool.MAXIMIZE: "Max", Tool.MINIMIZE: "Min",
    Tool.NEXT_MONITOR: "Monitor →",
}  # fmt: skip
if set(TOOL_NAMES) != set(Tool):
    raise ImportError("every tool needs a name on the key map")


def _output(key: Key, label: str | None = None) -> str:
    c = character(key, shifted=False)
    return label or _KEY_NAMES.get(key) or (c.upper() if c else key.name.title())


def describe(a: Action) -> tuple[str, Kind]:
    """A key's label and kind, as drawn."""
    match a:
        case Key():
            return _output(a), Kind.KEY
        case Chord(key, mods) if a in _CHORD_NAMES:
            return _CHORD_NAMES[a], Kind.KEY
        case Chord(key, mods) if mods == frozenset({Mod.SHIFT}) and (c := character(key, shifted=True)):
            return c, Kind.KEY
        case Chord(key, mods):
            return "+".join([*(_MOD_NAMES[m] for m in Mod if m in mods), _output(key)]), Kind.KEY
        case TapHold(tap):
            return describe(tap)
        case Mod():
            return _MOD_NAMES[a], Kind.MOD
        case Layer():
            return a.name.title(), Kind.LAYER
        case Command.LOCK_LAYER:
            return "Lock", Kind.LAYER
        case Command.NOTHING:
            return "", Kind.BLANK
        case Tool():
            return TOOL_NAMES[a], Kind.TOOL
        case App():
            return a.name[:1].upper() + a.name[1:], Kind.APP


_HOLD_TO_TALK: Final = frozenset({Tool.DICTATE, Tool.DICTATE_CLEAN, Tool.DICTATE_COMMAND, Tool.REWRITE_VOICE, Tool.AGENT_VOICE})


def _hold(a: Action) -> str:
    if a in _HOLD_TO_TALK:
        return "hold"
    if not isinstance(a, TapHold):
        return ""
    return _MOD_NAMES[a.hold] if isinstance(a.hold, Mod) else a.hold.name.title()


def _holding(layer: Layer | None) -> int | None:
    """The cell of the thumb that holds `layer`."""
    return next((cell for cell, a in enumerate(BASE.cells()) if isinstance(a, TapHold) and a.hold is layer), None)


def keymap(layer: Layer | None) -> list[list[Cap]]:
    """The keyboard, row by row, as `layer` (None: the base layer) has it."""
    held = _holding(layer) if layer is not None else None
    rows: list[list[Cap]] = []
    for row in ROWS:
        caps: list[Cap] = []
        for scancode, name in row:
            if scancode == ESCAPE:
                caps.append(Cap(name, "On/off", "hold: quit", Kind.SPECIAL))
            elif (tool := VOICE_KEYS.get(scancode)) is not None:
                label = "Agent" if tool is Tool.AGENT_VOICE else TOOL_NAMES[tool]
                caps.append(Cap(name, label, "hold", Kind.TOOL))
            else:
                match slot(scancode):
                    case Unmapped():
                        caps.append(Cap(name, "", "", Kind.BLANK))
                    case int(cell) if cell == held and layer is not None:
                        caps.append(Cap(name, layer.name.title(), "held", Kind.HELD))
                    case int(cell):
                        a = action(layer, cell)
                        label, kind = describe(a)
                        caps.append(Cap(name, label, _hold(a), kind))
        rows.append(caps)
    return rows


def indicator_text(layer: Layer | None, *, locked: bool, caps_word: bool) -> str | None:
    """What the layer indicator says; None: nothing to show."""
    parts = [f"{layer.name} · locked" if locked and layer else layer.name] if layer else []
    if caps_word:
        parts.append("CAPS WORD")
    return " · ".join(parts) or None
