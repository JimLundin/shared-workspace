"""Windows keyboard logic: decides what each physical key press or release turns
into, in Windows terms (virtual keys, modifiers). It makes no Win32 calls itself;
those go through a `Host`, so the logic can be tested without Windows.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final, Protocol, assert_never

from config import SNIPPETS
from protocol import App, Request, Tool

from .layout import (
    CELLS,
    ESCAPE,
    QUICK_TAP_MS,
    QUIT_HOLD_MS,
    SHIFT_ONLY,
    STREAK_MS,
    TABLE,
    VOICE_KEYS,
    Action,
    Chord,
    Command,
    Hold,
    Key,
    Layer,
    Mod,
    Output,
    Scancode,
    TapHold,
    Unmapped,
    character,
    describe,
    is_left_hand,
    slot,
)


class Host(Protocol):
    """What the engine needs from Windows."""

    def send(self, key: Key | Mod, down: bool) -> None: ...
    def start_timer(self) -> int: ...
    def stop_timer(self, timer: int) -> None: ...
    def signal(self, signal: Signal) -> None:
        """Let the user know about a change they can't otherwise see."""

    def request(self, request: Request, down: bool) -> None:
        """A tool or app key went down or up: pass it on to the assistant."""

    def type_text(self, text: str) -> None:
        """Type text as characters, whatever the keyboard layout."""

    def used(self, feature: str) -> None:
        """Count a use of a feature, for the usage log."""

    def indicate(self, indicator: Indicator) -> None:
        """What the layer indicator should show now (it changed)."""


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


class Decision(Enum):
    """How an undecided tap-hold key is resolved."""

    TAP = auto()
    HOLD = auto()


MAX_LAYERS = 5
"""Layers held at once: the layout has five layer keys."""
MAX_BUFFERED = 18
"""Key events that can wait on one undecided tap-hold key. One finger holds that
key; each of the other nine can add at most one press, plus the release of a key
it was already holding (auto-repeats are not buffered): 9 x 2 = 18. Anything
beyond that decides the key as a hold rather than growing."""


type Snippet = str | Callable[[], str]


@dataclass(frozen=True, slots=True)
class Configuration:
    """Resolve tables and buffer bounds once, before handling keyboard events."""

    actions: Mapping[Layer | None, tuple[Action, ...]]
    snippets: tuple[tuple[str, Snippet], ...]
    streak_ms: int = STREAK_MS
    quick_tap_ms: int = QUICK_TAP_MS
    quit_hold_ms: int = QUIT_HOLD_MS
    max_trigger: int = field(init=False)

    def __post_init__(self) -> None:
        actions = {layer: tuple(row) for layer, row in self.actions.items()}
        snippets = tuple((trigger, value) for trigger, value in self.snippets)
        layers: set[Layer | None] = {None}
        layers.update(Layer)
        if set(actions) != layers or any(len(row) != CELLS for row in actions.values()):
            raise ValueError("Every layer must define one action per keyboard cell.")
        if any(not trigger for trigger, _value in snippets):
            raise ValueError("Snippet triggers must not be empty.")
        if self.streak_ms < 0 or self.quick_tap_ms < 0 or self.quit_hold_ms <= 0:
            raise ValueError("Keyboard timings must be nonnegative, with a positive quit hold.")
        # Pyright 1.1.414's stub incorrectly gives frozendict.__init__ no arguments.
        table = frozendict(actions)  # pyright: ignore[reportCallIssue]
        object.__setattr__(self, "actions", table)
        object.__setattr__(self, "snippets", snippets)
        object.__setattr__(self, "max_trigger", max((len(trigger) for trigger, _value in snippets), default=0))


DEFAULT_CONFIGURATION: Final = Configuration(TABLE, tuple(SNIPPETS.items()))


@dataclass(frozen=True, slots=True)
class Indicator:
    """State the user can't see on the keys: the layer keys come from, and caps word."""

    layer: Layer | None
    locked: bool
    caps_word: bool


QUIET: Final = Indicator(None, locked=False, caps_word=False)


@dataclass(frozen=True, slots=True)
class Pending:
    """A tap-hold key that is down but not yet decided."""

    cell: int
    tap: Output
    hold: Hold
    timer: int
    pressed_at: int


@dataclass(frozen=True, slots=True)
class KeyDown:
    cell: int
    at: int


@dataclass(frozen=True, slots=True)
class KeyUp:
    cell: int
    at: int


type Event = KeyDown | KeyUp
"""A mapped physical key event, including repeats and buffered events."""


@dataclass(frozen=True, slots=True)
class LastTap:
    cell: int
    at: int


def elapsed(now: int, then: int) -> int:
    """Milliseconds between two Windows timestamps, which wrap at 2**32."""
    return (now - then) & 0xFFFF_FFFF


def no_keys() -> list[Action | None]:
    return [None] * CELLS


def is_typing(a: Action) -> bool:
    """A typed character (for the typing streak): a key, maybe shifted, but not a shortcut."""
    return isinstance(a, Key) or (isinstance(a, Chord) and a.mods <= SHIFT_ONLY)


@dataclass(slots=True)
class Modifiers:
    """Which modifiers Windows has down, and how many held keys want each one.
    Windows is told only when a count goes from 0 to 1 or back, so one key can't
    release another's modifier."""

    counts: dict[Mod, int] = field(default_factory=dict[Mod, int])

    def hold(self, host: Host, mod: Mod) -> None:
        count = self.counts.get(mod, 0)
        if count == 0:
            host.send(mod, True)
        self.counts[mod] = count + 1

    def release(self, host: Host, mod: Mod) -> None:
        count = self.counts.pop(mod, 0)
        if count > 1:
            self.counts[mod] = count - 1
        elif count == 1:
            host.send(mod, False)

    def chord(self, host: Host, chord: Chord) -> None:
        """The chord's key goes down with its modifiers around it. Modifiers already
        held are left alone; the ones added are released right after."""
        added = [mod for mod in Mod if mod in chord.mods and mod not in self.counts]  # in a fixed order
        for mod in added:
            host.send(mod, True)
        host.send(chord.key, True)
        for mod in reversed(added):
            host.send(mod, False)

    def release_all(self, host: Host) -> None:
        for mod in self.counts:
            host.send(mod, False)
        self.counts = {}


@dataclass(slots=True)
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

    Voice keys: hold B to dictate or N to speak an agent task; releasing the key
    (or switching the layout off) asks the assistant to stop listening. Tool and
    app keys on the Tools and Apps layers are passed on the same way.

    Snippets: the last characters typed are remembered; once they end in a
    snippet's trigger, the trigger is erased and the snippet typed instead.

    Layer lock: the lock key on a layer keeps that layer on after its thumb is
    released. Tap the lock key again, or the escape key, to unlock.
    """

    configuration: Configuration = field(default=DEFAULT_CONFIGURATION, kw_only=True)
    escape_down_at: int | None = None
    """When the escape key went down, if it is down."""
    enabled: bool = True
    """False while switched off with the escape key or tray toggle."""
    pressed: list[Action | None] = field(default_factory=no_keys)
    """What each held layout key turned into, by cell."""
    active_layers: list[Layer] = field(default_factory=list[Layer])
    pending: Pending | None = None
    buffer: list[Event] = field(default_factory=list[Event])
    """Events that arrived while a tap-hold key was undecided."""
    last_typed_at: int | None = None
    """When the last character was typed (for the typing streak)."""
    last_tap: LastTap | None = None
    """The last tap-hold key tapped, and when (for quick tap)."""
    caps_word: bool = False
    locked: Layer | None = None
    mods: Modifiers = field(default_factory=Modifiers)
    voice_held: set[Scancode] = field(default_factory=set[Scancode])
    """Dedicated voice keys currently held, independent of the layout's layers."""
    typed: str = ""
    """The last characters typed, for snippets. Anything that moves the cursor, or a
    shortcut, clears it."""
    shown: Indicator = QUIET
    """What the indicator was last told."""

    def on_key(self, host: Host, scancode: Scancode, down: bool, now: int) -> Verdict:
        """Decode native key fields; mapped keys become KeyDown or KeyUp once.
        `now` is the event's timestamp in ms (it may wrap around)."""
        verdict = self.key(host, scancode, down, now)
        self.refresh(host)
        return verdict

    def refresh(self, host: Host) -> None:
        """Tells the indicator if what it should show has changed."""
        now = Indicator(self.layer(), self.locked is not None, self.caps_word)
        if now != self.shown:
            self.shown = now
            host.indicate(now)

    def toggle(self, host: Host) -> None:
        """Switch the layout on/off, releasing held input and clearing its indicator."""
        self.escape_down_at = None
        self.release_all(host)
        self.enabled = not self.enabled
        if not self.enabled:
            host.used("switch_off")
        host.signal(Signal.ON if self.enabled else Signal.OFF)
        self.refresh(host)

    def key(self, host: Host, scancode: Scancode, down: bool, now: int) -> Verdict:
        # Escape key: tap = on/off, hold = quit. Checked first, works in both states.
        if scancode == ESCAPE:
            if down:
                if self.escape_down_at is None:
                    self.escape_down_at = now
            elif self.escape_down_at is not None:
                pressed_at, self.escape_down_at = self.escape_down_at, None
                if elapsed(now, pressed_at) >= self.configuration.quit_hold_ms:
                    self.release_all(host)
                    return Verdict.QUIT
                self.toggle(host)
            return Verdict.SWALLOW
        if not self.enabled:
            return Verdict.PASS
        # Voice buttons act immediately and never decide a pending tap-hold key.
        if (tool := VOICE_KEYS.get(scancode)) is not None:
            if down != (scancode in self.voice_held):
                if down:
                    self.voice_held.add(scancode)
                else:
                    self.voice_held.remove(scancode)
                host.request(tool, down)
            return Verdict.SWALLOW
        match slot(scancode):
            case Unmapped.FREE:
                self.typed = ""
                return Verdict.PASS
            case Unmapped.BLOCKED:
                return Verdict.SWALLOW
            case int(cell):
                self.event(host, KeyDown(cell, now) if down else KeyUp(cell, now))
                return Verdict.SWALLOW

    def in_streak(self, now: int) -> bool:
        return self.configuration.streak_ms > 0 and self.last_typed_at is not None and elapsed(now, self.last_typed_at) < self.configuration.streak_ms

    def quick_tap(self, cell: int, now: int) -> bool:
        return self.last_tap is not None and self.last_tap.cell == cell and elapsed(now, self.last_tap.at) < self.configuration.quick_tap_ms

    def layer(self) -> Layer | None:
        """The layer keys are read from: the top held layer, else a locked one, else
        None for the base layer."""
        return self.active_layers[-1] if self.active_layers else self.locked

    def event(self, host: Host, e: Event) -> None:
        if (p := self.pending) is not None:
            self.event_while_pending(host, p, e)
            return

        match e:
            case KeyUp(cell):
                if (current := self.pressed[cell]) is not None:
                    self.pressed[cell] = None
                    self.release(host, current)
            case KeyDown(cell, now):
                if (current := self.pressed[cell]) is not None:
                    # Auto-repeat: repeat the output already chosen on the first press.
                    if isinstance(current, (Key, Chord)):
                        self.press(host, current, now)
                    return
                layer = self.layer()
                match self.configuration.actions[layer][cell]:
                    case TapHold(tap) if self.quick_tap(cell, now):
                        a: Action = tap
                    case TapHold(tap, Mod() as mod) if mod != Mod.SHIFT and self.in_streak(now):
                        a = tap
                    case TapHold(tap, hold):
                        self.pending = Pending(cell, tap, hold, host.start_timer(), now)
                        self.buffer = []
                        return
                    case other:
                        a = other
                if layer is not None and a is not Command.NOTHING:
                    host.used(f"{layer.name.lower()}:{describe(a)}")
                self.pressed[cell] = a
                self.press(host, a, now)
            case _:
                assert_never(e)

    def event_while_pending(self, host: Host, p: Pending, e: Event) -> None:
        if e.cell == p.cell:
            match e:
                case KeyUp(_, now):
                    self.resolve(host, Decision.TAP, now=now)
                case KeyDown():
                    pass
                case _:
                    assert_never(e)
            return  # auto-repeat of the undecided key is ignored
        match e:
            case KeyDown(cell, now):
                released_meanwhile = any(isinstance(waiting, KeyUp) and waiting.cell == cell for waiting in self.buffer)
                if not released_meanwhile:
                    # A key held since before is already decided: repeat it now.
                    if (current := self.pressed[cell]) is not None:
                        if isinstance(current, (Key, Chord)):
                            self.press(host, current, now)
                        return
                    # A buffered key's repeated down adds nothing; reuse the queued event.
                    for waiting in reversed(self.buffer):
                        if waiting.cell == cell:
                            if isinstance(waiting, KeyDown):
                                return
                            break
                # The first press on the modifier's own hand is a roll.
                first_press = not any(isinstance(waiting, KeyDown) for waiting in self.buffer)
                if first_press and isinstance(p.hold, Mod) and is_left_hand(cell) == is_left_hand(p.cell):
                    self.resolve(host, Decision.TAP, now=now)
                    self.event(host, e)
                    return
            case KeyUp():
                pass
            case _:
                assert_never(e)
        if len(self.buffer) >= MAX_BUFFERED:
            # Too many keys waiting: decide it as a hold, then handle this one.
            self.resolve(host, Decision.HOLD, now=e.at)
            self.event(host, e)
            return
        self.buffer.append(e)
        if isinstance(e, KeyUp) and any(isinstance(waiting, KeyDown) and waiting.cell == e.cell for waiting in self.buffer):
            self.resolve(host, Decision.HOLD, now=e.at)

    def timeout(self, host: Host, timer: int) -> None:
        if self.pending is not None and self.pending.timer == timer:
            self.resolve(host, Decision.HOLD, now=self.pending.pressed_at)
            self.refresh(host)

    def resolve(self, host: Host, decision: Decision, *, now: int) -> None:
        """Decide the undecided key. `now` is when the decision happened."""
        p, self.pending = self.pending, None
        if p is None:
            return
        host.stop_timer(p.timer)
        match decision:
            case Decision.HOLD:
                host.used(f"hold:{p.hold.name.lower()}" if isinstance(p.hold, Mod) else f"layer:{p.hold.name.lower()}")
                self.pressed[p.cell] = p.hold
                self.press(host, p.hold, p.pressed_at)
            case Decision.TAP:
                self.press(host, p.tap, p.pressed_at)
                self.release(host, p.tap)
                self.last_tap = LastTap(p.cell, now)
            case _:
                assert_never(decision)
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
        self.mods.release_all(host)  # nothing should be left, but never leave one stuck
        self.typed = ""
        held_voice, self.voice_held = self.voice_held, set()
        for scancode in sorted(held_voice):
            host.request(VOICE_KEYS[scancode], False)

    def apply_caps_word(self, a: Action) -> Action:
        """Caps word: capitalise letters, let word characters through, end on anything else."""
        if not self.caps_word:
            return a
        match a:
            case Key() if a.is_letter():
                return Chord(a, SHIFT_ONLY)
            case Key() if a.continues_word():
                return a
            case Chord(Key.MINUS, mods) if mods == SHIFT_ONLY:  # _
                return a
            case Key() | Chord():
                self.caps_word = False
                return a
            case Mod() | Layer() | TapHold() | Command() | Tool() | App():
                return a

    def remember(self, host: Host, a: Output) -> None:
        """Snippets: keep the last characters typed, and expand a complete trigger."""
        if not self.configuration.max_trigger:
            self.typed = ""
            return
        key, mods = (a, frozenset[Mod]()) if isinstance(a, Key) else (a.key, a.mods)
        held = frozenset(self.mods.counts) | mods
        if key is Key.BACKSPACE and not held:
            self.typed = self.typed[:-1]
            return
        typed = character(key, Mod.SHIFT in held) if held <= SHIFT_ONLY else None
        if typed is None:
            self.typed = ""
            return
        self.typed = (self.typed + typed)[-self.configuration.max_trigger:]
        for trigger, text in self.configuration.snippets:
            if self.typed.endswith(trigger):
                self.typed = ""
                for _ in trigger:
                    host.send(Key.BACKSPACE, True)
                    host.send(Key.BACKSPACE, False)
                host.type_text(text() if callable(text) else text)
                host.used(f"snippet:{trigger}")
                return

    def press(self, host: Host, a: Action, now: int) -> None:
        # Both Shift keys held (this one is already recorded as pressed): toggle caps word.
        if a == Mod.SHIFT and self.pressed.count(Mod.SHIFT) >= 2:
            self.caps_word = not self.caps_word
            if self.caps_word:
                host.used("caps_word")
                host.signal(Signal.CAPS_WORD)
        a = self.apply_caps_word(a)
        match a:
            case Key():
                host.send(a, True)
                self.remember(host, a)
            case Chord():
                self.mods.chord(host, a)
                self.remember(host, a)
            case Mod():
                self.mods.hold(host, a)
            case Layer():
                # A layer beyond MAX_LAYERS is ignored rather than stored.
                if len(self.active_layers) < MAX_LAYERS:
                    self.active_layers.append(a)
            case Command.LOCK_LAYER:
                if self.locked is not None:
                    self.locked = None
                elif self.active_layers:
                    self.locked = self.active_layers[-1]
                    host.used("layer_lock")
            case Tool() | App():
                self.typed = ""
                host.request(a, True)
            case Command.NOTHING | TapHold():
                pass
            case _:
                assert_never(a)
        if is_typing(a):
            self.last_typed_at = now

    def release(self, host: Host, a: Action) -> None:
        match a:
            case Key():
                host.send(a, False)
            case Chord():
                host.send(a.key, False)
            case Mod():
                self.mods.release(host, a)
            case Layer():
                if a in self.active_layers:
                    # Remove the most recently added copy.
                    index = len(self.active_layers) - 1 - self.active_layers[::-1].index(a)
                    del self.active_layers[index]
            case Tool() | App():
                host.request(a, False)
            case TapHold() | Command():
                pass
            case _:
                assert_never(a)
