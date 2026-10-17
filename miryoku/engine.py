"""Platform-independent key logic: decides what each physical key press or
release turns into. No Windows code here, so it is unit-tested on any OS.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import NamedTuple, NoReturn, Protocol

from .layout import (
    CELLS,
    ESCAPE,
    QUICK_TAP_MS,
    QUIT_HOLD_MS,
    STREAK_MS,
    Action,
    Ctrl,
    Key,
    Layer,
    LayerId,
    LockLayer,
    Mod,
    Modifier,
    NoAction,
    Scancode,
    Shifted,
    Tap,
    TapHold,
    Unmapped,
    action,
    held,
    is_left_hand,
    slot,
)


class Host(Protocol):
    """What the engine needs from the operating system."""

    def send(self, vk: int, down: bool) -> None: ...
    def start_timer(self) -> int: ...
    def stop_timer(self, timer: int) -> None: ...
    def signal(self, signal: Signal) -> None:
        """Let the user know about a change they can't otherwise see."""


class Verdict(Enum):
    """What the OS should do with a physical key event."""

    PASS = auto()
    """Not ours: let it through unchanged."""
    SWALLOW = auto()
    """Handled (or deliberately blocked): don't let it through."""
    QUIT = auto()
    """The escape key was held long enough: shut down."""


class Signal(Enum):
    """Changes the user gets a sound for."""

    ON = auto()
    OFF = auto()
    CAPS_WORD = auto()


MAX_LAYERS = 3
"""Layers held at once: the layout has three layer keys."""
MAX_BUFFERED = 18
"""Key events that can wait on one undecided tap-hold key. One finger holds that
key; each of the other nine can add at most one press, plus the release of a key
it was already holding (auto-repeats are not buffered): 9 x 2 = 18. Anything
beyond that decides the key as a hold rather than growing."""


@dataclass(frozen=True, slots=True)
class Pending:
    """A tap-hold key that is down but not yet decided."""

    cell: int
    tap: Key
    hold: Modifier | Layer
    timer: int
    pressed_at: int


class Event(NamedTuple):
    cell: int
    down: bool
    at: int


def elapsed(now: int, then: int) -> int:
    """Milliseconds between two Windows timestamps, which wrap at 2**32."""
    return (now - then) & 0xFFFF_FFFF


def no_keys() -> list[Action | None]:
    return [None] * CELLS


def assert_never(value: NoReturn) -> NoReturn:
    raise AssertionError(f"unhandled: {value!r}")


class Engine:
    """Tap-hold rule (like KMonad's tap-hold-next-release): a tap-hold key becomes
    "hold" if another key is pressed AND released while it is down, or if it is
    held past the tapping term. Otherwise it is a tap.

    Chordal hold: for home row mods, if the first other key pressed is on the same
    hand, it is a tap, decided at once. You use a modifier with the other hand, so
    a same-hand key means a fast roll, not a shortcut. Holding past the tapping
    term still always makes it a modifier.

    Quick tap: tap a tap-hold key and press it again within `QUICK_TAP_MS`, and
    the second press is that key held down, so it repeats (Backspace, Space...).

    Typing streak: right after a typed character, a home row mod (except Shift) is
    a letter immediately. Shift is exempt so capitals after a space still work;
    layer thumbs are exempt so symbols and numbers inside words still work.

    Caps word: holding both Shift keys turns it on (or off). Letters then come out
    capitalised until something that isn't part of a word: a space, punctuation,
    Enter, an arrow... Digits, `_`, Backspace and Delete keep it going.

    Layer lock: the lock key on a layer keeps that layer on after its thumb is
    released. Tap the lock key again, or the escape key, to unlock.
    """

    __slots__ = (
        "active_layers",
        "buffer",
        "caps_word",
        "enabled",
        "escape_down_at",
        "last_tap",
        "last_typed_at",
        "locked",
        "pending",
        "pressed",
    )

    def __init__(self) -> None:
        self.escape_down_at: int | None = None
        """When the escape key went down, if it is down."""
        self.enabled = True
        """False while switched off with the escape key."""
        self.pressed = no_keys()
        """What each held layout key turned into, by cell."""
        self.active_layers: list[LayerId] = []
        self.pending: Pending | None = None
        self.buffer: list[Event] = []
        """Events that arrived while a tap-hold key was undecided."""
        self.last_typed_at: int | None = None
        """When the last character was typed (for the typing streak)."""
        self.last_tap: tuple[int, int] | None = None
        """The last tap-hold key tapped, and when (for quick tap)."""
        self.caps_word = False
        self.locked: LayerId | None = None

    def on_key(self, host: Host, scancode: Scancode, down: bool, now: int) -> Verdict:
        """Entry point for every physical key event. `now` is the event's
        timestamp in ms (it may wrap around)."""
        # Escape key: tap = on/off, hold = quit. Checked first, works in both states.
        if scancode == ESCAPE:
            if down:
                if self.escape_down_at is None:
                    self.escape_down_at = now
            elif self.escape_down_at is not None:
                pressed_at, self.escape_down_at = self.escape_down_at, None
                self.release_all(host)
                if elapsed(now, pressed_at) >= QUIT_HOLD_MS:
                    return Verdict.QUIT
                self.enabled = not self.enabled
                host.signal(Signal.ON if self.enabled else Signal.OFF)
            return Verdict.SWALLOW
        if not self.enabled:
            return Verdict.PASS
        match slot(scancode):
            case Unmapped.FREE:
                return Verdict.PASS
            case Unmapped.BLOCKED:
                return Verdict.SWALLOW
            case int(cell):
                self.event(host, Event(cell, down, now))
                return Verdict.SWALLOW

    def in_streak(self, now: int) -> bool:
        return STREAK_MS > 0 and self.last_typed_at is not None and elapsed(now, self.last_typed_at) < STREAK_MS

    def quick_tap(self, cell: int, now: int) -> bool:
        return self.last_tap is not None and self.last_tap[0] == cell and elapsed(now, self.last_tap[1]) < QUICK_TAP_MS

    def layer(self) -> LayerId:
        """The layer keys are read from: the top held layer, else a locked one, else base."""
        if self.active_layers:
            return self.active_layers[-1]
        return self.locked if self.locked is not None else LayerId.BASE

    def event(self, host: Host, e: Event) -> None:
        cell, down, now = e
        if (p := self.pending) is not None:
            self.event_while_pending(host, p, e)
            return

        current = self.pressed[cell]
        if not down:
            if current is not None:
                self.pressed[cell] = None
                self.release(host, current)
            return
        if current is not None:
            # Auto-repeat: repeat plain keys (arrows, symbols...).
            if isinstance(current, (Tap, Shifted)):
                self.press(host, current, now)
            return
        match action(self.layer(), cell):
            case TapHold(tap) if self.quick_tap(cell, now):
                a: Action = Tap(tap)
            case TapHold(tap, Mod() as mod) if mod != Mod.SHIFT and self.in_streak(now):
                a = Tap(tap)
            case TapHold(tap, hold):
                self.pending = Pending(cell, tap, held(hold), host.start_timer(), now)
                self.buffer = []
                return
            case other:
                a = other
        self.pressed[cell] = a
        self.press(host, a, now)

    def event_while_pending(self, host: Host, p: Pending, e: Event) -> None:
        cell, down, now = e
        if cell == p.cell:
            if not down:
                self.resolve(host, hold=False, now=now)
            return  # auto-repeat of the undecided key is ignored
        released_meanwhile = any(c == cell and not d for c, d, _ in self.buffer)
        if down and not released_meanwhile:
            # Auto-repeat of a key held since before: repeat it now, it's already decided.
            if (current := self.pressed[cell]) is not None:
                if isinstance(current, (Tap, Shifted)):
                    self.press(host, current, now)
                return
            # Auto-repeat of a key already waiting in the buffer adds nothing.
            mine = [d for c, d, _ in self.buffer if c == cell]
            if mine and mine[-1]:
                return
        # Chordal hold: the first key pressed is on the modifier's own hand, so it's a roll.
        first_press = down and not any(d for _, d, _ in self.buffer)
        if first_press and isinstance(p.hold, Modifier) and is_left_hand(cell) == is_left_hand(p.cell):
            self.resolve(host, hold=False, now=now)
            self.event(host, e)
            return
        if len(self.buffer) >= MAX_BUFFERED:
            # Too many keys waiting: decide it as a hold, then handle this one.
            self.resolve(host, hold=True, now=now)
            self.event(host, e)
            return
        self.buffer.append(e)
        if not down and any(c == cell and d for c, d, _ in self.buffer):
            self.resolve(host, hold=True, now=now)

    def timeout(self, host: Host, timer: int) -> None:
        if self.pending is not None and self.pending.timer == timer:
            self.resolve(host, hold=True, now=self.pending.pressed_at)

    def resolve(self, host: Host, *, hold: bool, now: int) -> None:
        """Decide the undecided key. `now` is when the decision happened."""
        p, self.pending = self.pending, None
        if p is None:
            return
        host.stop_timer(p.timer)
        if hold:
            self.pressed[p.cell] = p.hold
            self.press(host, p.hold, p.pressed_at)
        else:
            self.press(host, Tap(p.tap), p.pressed_at)
            self.release(host, Tap(p.tap))
            self.last_tap = (p.cell, now)
        waiting, self.buffer = self.buffer, []
        for e in waiting:
            self.event(host, e)

    def release_all(self, host: Host) -> None:
        """Let go of everything (used when switching off or quitting)."""
        if self.pending is not None:
            host.stop_timer(self.pending.timer)
            self.pending = None
        self.buffer = []
        held_down, self.pressed = self.pressed, no_keys()
        for a in held_down:
            if a is not None:
                self.release(host, a)
        self.active_layers = []
        self.last_typed_at = None
        self.last_tap = None
        self.caps_word = False
        self.locked = None

    def apply_caps_word(self, a: Action) -> Action:
        """Caps word: capitalise letters, let word characters through, end on anything else."""
        if not self.caps_word:
            return a
        match a:
            case Tap(key) if key.is_letter():
                return Shifted(key)
            case Tap(key) if key.continues_word():
                return a
            case Shifted(Key.MINUS):  # _
                return a
            case Tap() | Shifted() | Ctrl():
                self.caps_word = False
                return a
            case Modifier() | Layer() | TapHold() | LockLayer() | NoAction():
                return a

    def press(self, host: Host, a: Action, now: int) -> None:
        # Both Shift keys held (this one is already recorded as pressed): toggle caps word.
        shift = Modifier(Mod.SHIFT)
        if a == shift and self.pressed.count(shift) >= 2:
            self.caps_word = not self.caps_word
            if self.caps_word:
                host.signal(Signal.CAPS_WORD)
        a = self.apply_caps_word(a)
        match a:
            case Tap(key):
                host.send(key, True)
            case Modifier(mod):
                host.send(mod, True)
            case Shifted(key):
                host.send(Mod.SHIFT, True)
                host.send(key, True)
                host.send(Mod.SHIFT, False)
            case Ctrl(key):
                host.send(Mod.CTRL, True)
                host.send(key, True)
                host.send(key, False)
                host.send(Mod.CTRL, False)
            case Layer(layer):
                # A layer beyond MAX_LAYERS is ignored rather than stored.
                if len(self.active_layers) < MAX_LAYERS:
                    self.active_layers.append(layer)
            case LockLayer():
                if self.locked is not None:
                    self.locked = None
                elif self.active_layers:
                    self.locked = self.active_layers[-1]
            case NoAction() | TapHold():
                pass
            case _:
                assert_never(a)
        if isinstance(a, (Tap, Shifted)):
            self.last_typed_at = now

    def release(self, host: Host, a: Action) -> None:
        match a:
            case Tap(key) | Shifted(key):
                host.send(key, False)
            case Modifier(mod):
                host.send(mod, False)
            case Layer(layer):
                if layer in self.active_layers:
                    # Remove the most recently added copy.
                    index = len(self.active_layers) - 1 - self.active_layers[::-1].index(layer)
                    del self.active_layers[index]
            case NoAction() | Ctrl() | TapHold() | LockLayer():
                pass
            case _:
                assert_never(a)
