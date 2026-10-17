"""Everything about keys that is fixed: the settings, the vocabulary (keys,
modifiers, layers, actions), the layout written in it, and the lookup tables
the engine reads.

All of it is built and checked once, when the module is imported. A layout
mistake (a row of the wrong length, a key used twice) fails the import, so a
broken layout never starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
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
# A bare Key, Mod or LayerId in a layout is itself an action: a Key is tapped, a
# Mod or LayerId is held while the key is held. Only the actions that need more
# than that get a class of their own.


class Key(Enum):
    """A Windows virtual key (US English layout). The value is the VK code."""

    A = 0x41
    B = 0x42
    C = 0x43
    D = 0x44
    E = 0x45
    F = 0x46
    G = 0x47
    H = 0x48
    I = 0x49  # noqa: E741
    J = 0x4A
    K = 0x4B
    L = 0x4C
    M = 0x4D
    N = 0x4E
    O = 0x4F  # noqa: E741
    P = 0x50
    Q = 0x51
    R = 0x52
    S = 0x53
    T = 0x54
    U = 0x55
    V = 0x56
    W = 0x57
    X = 0x58
    Y = 0x59
    Z = 0x5A
    N0 = 0x30
    N1 = 0x31
    N2 = 0x32
    N3 = 0x33
    N4 = 0x34
    N5 = 0x35
    N6 = 0x36
    N7 = 0x37
    N8 = 0x38
    N9 = 0x39
    GRAVE = 0xC0
    MINUS = 0xBD
    EQUAL = 0xBB
    LBRACKET = 0xDB
    RBRACKET = 0xDD
    BACKSLASH = 0xDC
    SEMICOLON = 0xBA
    QUOTE = 0xDE
    COMMA = 0xBC
    PERIOD = 0xBE
    SLASH = 0xBF
    ESC = 0x1B
    SPACE = 0x20
    TAB = 0x09
    ENTER = 0x0D
    BACKSPACE = 0x08
    CAPS_LOCK = 0x14
    DELETE = 0x2E
    INSERT = 0x2D
    HOME = 0x24
    END = 0x23
    PAGE_UP = 0x21
    PAGE_DOWN = 0x22
    LEFT = 0x25
    UP = 0x26
    RIGHT = 0x27
    DOWN = 0x28

    @property
    def vk(self) -> int:
        vk: int = self.value
        return vk

    def is_letter(self) -> bool:
        return Key.A.vk <= self.vk <= Key.Z.vk

    def continues_word(self) -> bool:
        """Keys that keep caps word going without being capitalised."""
        return Key.N0.vk <= self.vk <= Key.N9.vk or self in (Key.BACKSPACE, Key.DELETE)


class Mod(Enum):
    """The left-hand modifier keys. The value is the VK code."""

    GUI = 0x5B
    ALT = 0xA4
    CTRL = 0xA2
    SHIFT = 0xA0

    @property
    def vk(self) -> int:
        vk: int = self.value
        return vk


def is_extended(vk: int) -> bool:
    """Keys Windows expects with the "extended" flag when sent."""
    return 0x21 <= vk <= 0x28 or vk in (0x2D, 0x2E, 0x5B)


class LayerId(Enum):
    BASE = auto()
    NAV = auto()
    NUM = auto()
    SYM = auto()


Hold: TypeAlias = Mod | LayerId
"""What a tap-hold key does while held."""


@dataclass(frozen=True, slots=True)
class Shifted:
    """The key with Shift, e.g. `Shifted(Key.N1)` is `!`."""

    key: Key


@dataclass(frozen=True, slots=True)
class Ctrl:
    """Ctrl + key, tapped (clipboard shortcuts)."""

    key: Key


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


Action: TypeAlias = Key | Mod | LayerId | Shifted | Ctrl | TapHold | LockLayer | NoAction
"""What one key on one layer does."""


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

# fmt: off
A, B, C, D, E, F, G, H, I, J, K, L, M = Key.A, Key.B, Key.C, Key.D, Key.E, Key.F, Key.G, Key.H, Key.I, Key.J, Key.K, Key.L, Key.M  # noqa: E741
N, O, P, Q, R, S, T, U, V, W, X, Y, Z = Key.N, Key.O, Key.P, Key.Q, Key.R, Key.S, Key.T, Key.U, Key.V, Key.W, Key.X, Key.Y, Key.Z  # noqa: E741
N0, N1, N2, N3, N4, N5, N6, N7, N8, N9 = Key.N0, Key.N1, Key.N2, Key.N3, Key.N4, Key.N5, Key.N6, Key.N7, Key.N8, Key.N9
GRAVE, MINUS, EQUAL, LBRACKET, RBRACKET, BACKSLASH = Key.GRAVE, Key.MINUS, Key.EQUAL, Key.LBRACKET, Key.RBRACKET, Key.BACKSLASH
SEMICOLON, QUOTE, COMMA, PERIOD, SLASH = Key.SEMICOLON, Key.QUOTE, Key.COMMA, Key.PERIOD, Key.SLASH
ESC, SPACE, TAB, ENTER, BACKSPACE, CAPS_LOCK = Key.ESC, Key.SPACE, Key.TAB, Key.ENTER, Key.BACKSPACE, Key.CAPS_LOCK
DELETE, INSERT, HOME, END, PAGE_UP, PAGE_DOWN = Key.DELETE, Key.INSERT, Key.HOME, Key.END, Key.PAGE_UP, Key.PAGE_DOWN
LEFT, UP, RIGHT, DOWN = Key.LEFT, Key.UP, Key.RIGHT, Key.DOWN
GUI, ALT, CTL, SFT = Mod.GUI, Mod.ALT, Mod.CTRL, Mod.SHIFT
NAV, NUM, SYM = LayerId.NAV, LayerId.NUM, LayerId.SYM
# fmt: on

XX: Final = NoAction()
LOCK: Final = LockLayer()
UNDO, REDO, COPY, PASTE, CUT = Ctrl(Z), Ctrl(Y), Ctrl(C), Ctrl(V), Ctrl(X)
sh, ht, sc = Shifted, TapHold, Scancode


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
    top=      Row((Q,          W,          F,          P,              B  ), (J,              L,                  U,          Y,          QUOTE     )),
    home=     Row((ht(A, GUI), ht(R, ALT), ht(S, CTL), ht(T, SFT),     G  ), (M,              ht(N, SFT),         ht(E, CTL), ht(I, ALT), ht(O, GUI))),
    bottom=   Row((Z,          X,          C,          D,              V  ), (K,              H,                  COMMA,      PERIOD,     SLASH     )),
    thumbs=Thumbs((                        ESC,        ht(SPACE, NAV), TAB), (ht(ENTER, SYM), ht(BACKSPACE, NUM), DELETE    )),
)

NAV_LAYER: Final[Board[Action]] = Board(
    # Hold the Space thumb.
    top=      Row((LOCK, XX,  XX,  XX,  XX), (REDO,      PASTE,     COPY,      CUT,     UNDO )),
    home=     Row((GUI,  ALT, CTL, SFT, XX), (CAPS_LOCK, LEFT,      DOWN,      UP,      RIGHT)),
    bottom=   Row((XX,   XX,  XX,  XX,  XX), (INSERT,    HOME,      PAGE_DOWN, PAGE_UP, END  )),
    thumbs=Thumbs((           XX,  XX,  XX), (ENTER,     BACKSPACE, DELETE   )),
)

NUM_LAYER: Final[Board[Action]] = Board(
    # Hold the Backspace thumb.
    top=      Row((LBRACKET,  N7, N8,     N9, RBRACKET ), (XX, XX,  XX,  XX,  LOCK)),  # [ 7 8 9 ]
    home=     Row((SEMICOLON, N4, N5,     N6, EQUAL    ), (XX, SFT, CTL, ALT, GUI )),  # ; 4 5 6 =
    bottom=   Row((GRAVE,     N1, N2,     N3, BACKSLASH), (XX, XX,  XX,  XX,  XX  )),  # ` 1 2 3 \
    thumbs=Thumbs((               PERIOD, N0, MINUS    ), (XX, XX,  XX )),             # . 0 -
)

SYM_LAYER: Final[Board[Action]] = Board(
    # Hold the Enter thumb.
    top=      Row((sh(LBRACKET),  sh(N7), sh(N8), sh(N9), sh(RBRACKET) ), (XX, XX,  XX,  XX,  LOCK)),  # { & * ( }
    home=     Row((sh(SEMICOLON), sh(N4), sh(N5), sh(N6), sh(EQUAL)    ), (XX, SFT, CTL, ALT, GUI )),  # : $ % ^ +
    bottom=   Row((sh(GRAVE),     sh(N1), sh(N2), sh(N3), sh(BACKSLASH)), (XX, XX,  XX,  XX,  XX  )),  # ~ ! @ # |
    thumbs=Thumbs((                       sh(N9), sh(N0), sh(MINUS)    ), (XX, XX,  XX )),             # ( ) _
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


TABLE: Final = {
    LayerId.BASE: _flatten(BASE),
    LayerId.NAV: _flatten(NAV_LAYER),
    LayerId.NUM: _flatten(NUM_LAYER),
    LayerId.SYM: _flatten(SYM_LAYER),
}
"""Every layer flattened, by layer then cell."""


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
