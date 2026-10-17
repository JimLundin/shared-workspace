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


class Key(IntEnum):
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

    def is_letter(self) -> bool:
        return Key.A <= self <= Key.Z

    def continues_word(self) -> bool:
        """Keys that keep caps word going without being capitalised."""
        return Key.N0 <= self <= Key.N9 or self in (Key.BACKSPACE, Key.DELETE)


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
    """The key with Shift, e.g. `Shifted(Key.N1)` is `!`."""

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
UNDO, REDO = Ctrl(Key.Z), Ctrl(Key.Y)
COPY, PASTE, CUT = Ctrl(Key.C), Ctrl(Key.V), Ctrl(Key.X)


def k(key: Key) -> Action:
    return Tap(key)


def sh(key: Key) -> Action:
    return Shifted(key)


def ht(tap: Key, hold: Hold) -> Action:
    return TapHold(tap, hold)


def hold(h: Hold) -> Action:
    return held(h)


sc = Scancode
A, B, C, D, E, F, G = Key.A, Key.B, Key.C, Key.D, Key.E, Key.F, Key.G
H, I, J, K, L, M, N = Key.H, Key.I, Key.J, Key.K, Key.L, Key.M, Key.N  # noqa: E741
O, P, Q, R, S, T, U = Key.O, Key.P, Key.Q, Key.R, Key.S, Key.T, Key.U  # noqa: E741
V, W, X, Y, Z = Key.V, Key.W, Key.X, Key.Y, Key.Z


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
    top=      Row((k(Q),       k(W),       k(F),       k(P),               k(B)      ), (k(J),               k(L),                   k(U),          k(Y),          k(Key.QUOTE))),
    home=     Row((ht(A, GUI), ht(R, ALT), ht(S, CTL), ht(T, SFT),         k(G)      ), (k(M),               ht(N, SFT),             ht(E, CTL),    ht(I, ALT),    ht(O, GUI)  )),
    bottom=   Row((k(Z),       k(X),       k(C),       k(D),               k(V)      ), (k(K),               k(H),                   k(Key.COMMA),  k(Key.PERIOD), k(Key.SLASH))),
    thumbs=Thumbs((                        k(Key.ESC), ht(Key.SPACE, NAV), k(Key.TAB)), (ht(Key.ENTER, SYM), ht(Key.BACKSPACE, NUM), k(Key.DELETE))),
)

NAV_LAYER: Final[Board[Action]] = Board(
    # Hold the Space thumb.
    top=      Row((LOCK,      XX,        XX,        XX,        XX), (REDO,             PASTE,            COPY,             CUT,            UNDO        )),
    home=     Row((hold(GUI), hold(ALT), hold(CTL), hold(SFT), XX), (k(Key.CAPS_LOCK), k(Key.LEFT),      k(Key.DOWN),      k(Key.UP),      k(Key.RIGHT))),
    bottom=   Row((XX,        XX,        XX,        XX,        XX), (k(Key.INSERT),    k(Key.HOME),      k(Key.PAGE_DOWN), k(Key.PAGE_UP), k(Key.END)  )),
    thumbs=Thumbs((                      XX,        XX,        XX), (k(Key.ENTER),     k(Key.BACKSPACE), k(Key.DELETE)   )),
)

NUM_LAYER: Final[Board[Action]] = Board(
    # Hold the Backspace thumb.
    top=      Row((k(Key.LBRACKET),  k(Key.N7), k(Key.N8),     k(Key.N9), k(Key.RBRACKET) ), (XX, XX,        XX,        XX,        LOCK     )),  # [ 7 8 9 ]
    home=     Row((k(Key.SEMICOLON), k(Key.N4), k(Key.N5),     k(Key.N6), k(Key.EQUAL)    ), (XX, hold(SFT), hold(CTL), hold(ALT), hold(GUI))),  # ; 4 5 6 =
    bottom=   Row((k(Key.GRAVE),     k(Key.N1), k(Key.N2),     k(Key.N3), k(Key.BACKSLASH)), (XX, XX,        XX,        XX,        XX       )),  # ` 1 2 3 \
    thumbs=Thumbs((                             k(Key.PERIOD), k(Key.N0), k(Key.MINUS)    ), (XX, XX,        XX       )),                        # . 0 -
)

SYM_LAYER: Final[Board[Action]] = Board(
    # Hold the Enter thumb.
    top=      Row((sh(Key.LBRACKET),  sh(Key.N7), sh(Key.N8), sh(Key.N9), sh(Key.RBRACKET) ), (XX, XX,        XX,        XX,        LOCK     )),  # { & * ( }
    home=     Row((sh(Key.SEMICOLON), sh(Key.N4), sh(Key.N5), sh(Key.N6), sh(Key.EQUAL)    ), (XX, hold(SFT), hold(CTL), hold(ALT), hold(GUI))),  # : $ % ^ +
    bottom=   Row((sh(Key.GRAVE),     sh(Key.N1), sh(Key.N2), sh(Key.N3), sh(Key.BACKSLASH)), (XX, XX,        XX,        XX,        XX       )),  # ~ ! @ # |
    thumbs=Thumbs((                               sh(Key.N9), sh(Key.N0), sh(Key.MINUS)    ), (XX, XX,        XX       )),                        # ( ) _
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
