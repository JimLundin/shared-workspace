"""Everything about keys that is fixed: the settings, the vocabulary (keys,
modifiers, layers, actions), the layout written in it, and the lookup tables
the engine reads.

All of it is built and checked once, when the module is imported. A layout
mistake (a row of the wrong length, a key used twice) fails the import, so a
broken layout never starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum, auto
from typing import Final, Generic, NewType, TypeAlias, TypeVar

# ---- Settings ------------------------------------------------------------------

TAPPING_TERM_MS: Final = 200
"""Hold a tap-hold key longer than this and it becomes its modifier or layer."""
STREAK_MS: Final = 150
"""Typing streak: a home row mod pressed this soon after a typed character is a
plain letter, sent on press. 0 turns it off."""
QUICK_TAP_MS: Final = 150
"""Quick tap: tap a tap-hold key, then press it again within this long, and it
is that key held down (so it repeats), not its modifier or layer."""
QUIT_HOLD_MS: Final = 2000
"""Hold the escape key this long to quit."""

Scancode = NewType("Scancode", int)
"""A hardware scancode identifying a physical key."""

ESCAPE: Final = Scancode(0x29)
"""The escape key (the key left of 1, § on a Nordic keyboard)."""


# ---- Vocabulary ----------------------------------------------------------------


Key = NewType("Key", int)
"""A Windows virtual key code (US English layout)."""

A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, U, V, W, X, Y, Z = (Key(vk) for vk in range(0x41, 0x5B))  # noqa: E741
N0, N1, N2, N3, N4, N5, N6, N7, N8, N9 = (Key(vk) for vk in range(0x30, 0x3A))
GRAVE, MINUS, EQUAL, LBRACKET, RBRACKET, BACKSLASH = Key(0xC0), Key(0xBD), Key(0xBB), Key(0xDB), Key(0xDD), Key(0xDC)
SEMICOLON, QUOTE, COMMA, PERIOD, SLASH = Key(0xBA), Key(0xDE), Key(0xBC), Key(0xBE), Key(0xBF)
ESC, SPACE, TAB, ENTER, BACKSPACE, CAPS_LOCK = Key(0x1B), Key(0x20), Key(0x09), Key(0x0D), Key(0x08), Key(0x14)
DELETE, INSERT, HOME, END, PAGE_UP, PAGE_DOWN = Key(0x2E), Key(0x2D), Key(0x24), Key(0x23), Key(0x21), Key(0x22)
LEFT, UP, RIGHT, DOWN = Key(0x25), Key(0x26), Key(0x27), Key(0x28)


def is_letter(key: Key) -> bool:
    return A <= key <= Z


def continues_word(key: Key) -> bool:
    """Keys that keep caps word going without being capitalised."""
    return N0 <= key <= N9 or key in (BACKSPACE, DELETE)


class Mod(IntEnum):
    """The left-hand modifier keys. The value is the VK code."""

    GUI = 0x5B
    ALT = 0xA4
    CTRL = 0xA2
    SHIFT = 0xA0


def is_extended(vk: int) -> bool:
    """Keys Windows expects with the "extended" flag when sent."""
    return 0x21 <= vk <= 0x28 or vk in (0x2D, 0x2E, 0x5B)


class LayerId(IntEnum):
    BASE = 0
    NAV = 1
    NUM = 2
    SYM = 3


Hold: TypeAlias = Mod | LayerId
"""What a tap-hold key does while held."""


@dataclass(frozen=True, slots=True)
class Tap:
    """Press and release a key."""

    key: Key


@dataclass(frozen=True, slots=True)
class Shifted:
    """The key with Shift, e.g. `Shifted(N1)` is `!`."""

    key: Key


@dataclass(frozen=True, slots=True)
class Ctrl:
    """Ctrl + key, tapped (clipboard shortcuts)."""

    key: Key


@dataclass(frozen=True, slots=True)
class Modifier:
    """A modifier held while the key is held."""

    mod: Mod


@dataclass(frozen=True, slots=True)
class Layer:
    """A layer active while the key is held."""

    layer: LayerId


@dataclass(frozen=True, slots=True)
class TapHold:
    """Tap = the key, hold = the `Hold`."""

    tap: Key
    hold: Hold


@dataclass(frozen=True, slots=True)
class LockLayer:
    """Keep the held layer on after its thumb is released; tap again to unlock."""


@dataclass(frozen=True, slots=True)
class NoAction:
    """Does nothing."""


Action: TypeAlias = Tap | Shifted | Ctrl | Modifier | Layer | TapHold | LockLayer | NoAction
"""What one key on one layer does."""


def held(hold: Hold) -> Modifier | Layer:
    """The same thing as a plain held key."""
    match hold:
        case Mod():
            return Modifier(hold)
        case LayerId():
            return Layer(hold)


# ---- Board shape ---------------------------------------------------------------

CellT = TypeVar("CellT")

CELLS: Final = 36
"""Keys on the board: three rows of ten, plus six thumb keys."""


@dataclass(frozen=True, slots=True)
class Row(Generic[CellT]):
    """One row across the keyboard: five keys for the left hand, five for the right."""

    left: tuple[CellT, CellT, CellT, CellT, CellT]
    right: tuple[CellT, CellT, CellT, CellT, CellT]


@dataclass(frozen=True, slots=True)
class Thumbs(Generic[CellT]):
    """The thumb keys: three per hand."""

    left: tuple[CellT, CellT, CellT]
    right: tuple[CellT, CellT, CellT]


@dataclass(frozen=True, slots=True)
class Board(Generic[CellT]):
    """The whole board, written the way it looks. Used for the physical keys
    (`Board[Scancode]`) and for every layer (`Board[Action]`), so they line up."""

    top: Row[CellT]
    home: Row[CellT]
    bottom: Row[CellT]
    thumbs: Thumbs[CellT]

    def cells(self) -> tuple[CellT, ...]:
        """All 36 keys in order: rows top to bottom, left to right, then thumbs."""
        rows = (self.top, self.home, self.bottom)
        return (
            *(key for row in rows for key in (*row.left, *row.right)),
            *self.thumbs.left,
            *self.thumbs.right,
        )


def is_left_hand(cell: int) -> bool:
    """Which hand key number `cell` belongs to (the numbering `Board.cells` uses)."""
    return cell % 10 < 5 if cell < 30 else cell < 33


# ---- Short names used in the tables --------------------------------------------

XX: Final = NoAction()
LOCK: Final = LockLayer()
GUI, ALT, CTL, SFT = Mod.GUI, Mod.ALT, Mod.CTRL, Mod.SHIFT
NAV, NUM, SYM = LayerId.NAV, LayerId.NUM, LayerId.SYM
UNDO, REDO, COPY, PASTE, CUT = Ctrl(Z), Ctrl(Y), Ctrl(C), Ctrl(V), Ctrl(X)
k, sh, ht, hold, sc = Tap, Shifted, TapHold, held, Scancode


# ---- The layout ----------------------------------------------------------------
# Rows read across the keyboard: left hand, then right hand. Kept in columns by
# hand, so the formatter is told to leave this section alone.

# fmt: off
PHYSICAL: Final[Board[Scancode]] = Board(
    # Physical keys of the ISO laptop keyboard (Miryoku KMonad mapping: rows moved
    # up one, reverse angle on the left, wide spacing). Nordic keycaps on the right.
    top=      Row((sc(0x03), sc(0x04), sc(0x05), sc(0x06), sc(0x07)), (sc(0x09), sc(0x0A), sc(0x0B), sc(0x0C), sc(0x0D))),  # 2 3 4 5 6    | 8 9 0 + acute
    home=     Row((sc(0x10), sc(0x11), sc(0x12), sc(0x13), sc(0x14)), (sc(0x17), sc(0x18), sc(0x19), sc(0x1A), sc(0x1B))),  # Q W E R T    | I O P Å ¨
    bottom=   Row((sc(0x3A), sc(0x1E), sc(0x1F), sc(0x20), sc(0x21)), (sc(0x25), sc(0x26), sc(0x27), sc(0x28), sc(0x2B))),  # Caps A S D F | K L Ö Ä '
    thumbs=Thumbs((                    sc(0x2D), sc(0x2E), sc(0x2F)), (sc(0x33), sc(0x34), sc(0x35))),                      # X C V        | , . -
)

UNUSED: Final = frozenset(
    # Keys between and below the hands (1 7 Y U G H J Z B N M <): blocked.
    sc(code) for code in (0x02, 0x08, 0x15, 0x16, 0x22, 0x23, 0x24, 0x2C, 0x30, 0x31, 0x32, 0x56)
)

BASE: Final[Board[Action]] = Board(
    # Colemak-DH with home row mods (Win Alt Ctrl Shift).
    top=      Row((k(Q),       k(W),       k(F),       k(P),           k(B)  ), (k(J),           k(L),               k(U),       k(Y),       k(QUOTE)  )),
    home=     Row((ht(A, GUI), ht(R, ALT), ht(S, CTL), ht(T, SFT),     k(G)  ), (k(M),           ht(N, SFT),         ht(E, CTL), ht(I, ALT), ht(O, GUI))),
    bottom=   Row((k(Z),       k(X),       k(C),       k(D),           k(V)  ), (k(K),           k(H),               k(COMMA),   k(PERIOD),  k(SLASH)  )),
    thumbs=Thumbs((                        k(ESC),     ht(SPACE, NAV), k(TAB)), (ht(ENTER, SYM), ht(BACKSPACE, NUM), k(DELETE) )),
)

NAV_LAYER: Final[Board[Action]] = Board(
    # Hold the Space thumb.
    top=      Row((LOCK,      XX,        XX,        XX,        XX), (REDO,         PASTE,        COPY,         CUT,        UNDO    )),
    home=     Row((hold(GUI), hold(ALT), hold(CTL), hold(SFT), XX), (k(CAPS_LOCK), k(LEFT),      k(DOWN),      k(UP),      k(RIGHT))),
    bottom=   Row((XX,        XX,        XX,        XX,        XX), (k(INSERT),    k(HOME),      k(PAGE_DOWN), k(PAGE_UP), k(END)  )),
    thumbs=Thumbs((                      XX,        XX,        XX), (k(ENTER),     k(BACKSPACE), k(DELETE)   )),
)

NUM_LAYER: Final[Board[Action]] = Board(
    # Hold the Backspace thumb.
    top=      Row((k(LBRACKET),  k(N7), k(N8),     k(N9), k(RBRACKET) ), (XX, XX,        XX,        XX,        LOCK     )),  # [ 7 8 9 ]
    home=     Row((k(SEMICOLON), k(N4), k(N5),     k(N6), k(EQUAL)    ), (XX, hold(SFT), hold(CTL), hold(ALT), hold(GUI))),  # ; 4 5 6 =
    bottom=   Row((k(GRAVE),     k(N1), k(N2),     k(N3), k(BACKSLASH)), (XX, XX,        XX,        XX,        XX       )),  # ` 1 2 3 \
    thumbs=Thumbs((                     k(PERIOD), k(N0), k(MINUS)    ), (XX, XX,        XX       )),                        # . 0 -
)

SYM_LAYER: Final[Board[Action]] = Board(
    # Hold the Enter thumb.
    top=      Row((sh(LBRACKET),  sh(N7), sh(N8), sh(N9), sh(RBRACKET) ), (XX, XX,        XX,        XX,        LOCK     )),  # { & * ( }
    home=     Row((sh(SEMICOLON), sh(N4), sh(N5), sh(N6), sh(EQUAL)    ), (XX, hold(SFT), hold(CTL), hold(ALT), hold(GUI))),  # : $ % ^ +
    bottom=   Row((sh(GRAVE),     sh(N1), sh(N2), sh(N3), sh(BACKSLASH)), (XX, XX,        XX,        XX,        XX       )),  # ~ ! @ # |
    thumbs=Thumbs((                       sh(N9), sh(N0), sh(MINUS)    ), (XX, XX,        XX       )),                        # ( ) _
)
# fmt: on


# ---- Lookups the engine uses (built and checked at import) ------------------------


class LayoutError(Exception):
    """The layout is inconsistent; raised at import, never at runtime."""


def _flatten(board: Board[CellT]) -> tuple[CellT, ...]:
    cells = board.cells()
    if len(cells) != CELLS:
        raise LayoutError(f"a board has {len(cells)} keys, expected {CELLS}")
    return cells


TABLE: Final = (_flatten(BASE), _flatten(NAV_LAYER), _flatten(NUM_LAYER), _flatten(SYM_LAYER))
"""Every layer flattened, indexed `[LayerId][cell]`."""


def action(layer: LayerId, cell: int) -> Action:
    """What key `cell` does on `layer`."""
    return TABLE[layer][cell]


class Unmapped(Enum):
    FREE = auto()
    """Not ours: passes through."""
    BLOCKED = auto()
    """Between the hands: swallowed, does nothing."""


def _build_slots() -> dict[Scancode, int | Unmapped]:
    slots: dict[Scancode, int | Unmapped] = {}
    for cell, scancode in enumerate(_flatten(PHYSICAL)):
        if scancode in slots:
            raise LayoutError(f"physical key {scancode:#x} is used twice")
        slots[scancode] = cell
    for scancode in UNUSED:
        if scancode in slots:
            raise LayoutError(f"blocked key {scancode:#x} is also in the layout")
        slots[scancode] = Unmapped.BLOCKED
    return slots


SLOTS: Final = _build_slots()
"""Scancode -> its cell number, or BLOCKED. Anything else is FREE."""


def slot(scancode: Scancode) -> int | Unmapped:
    return SLOTS.get(scancode, Unmapped.FREE)
