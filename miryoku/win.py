"""Windows glue: installs a low-level keyboard hook, feeds keys to the engine,
sends the results, passes tool and app keys on to the assistant, and runs the
message loop. Win32 is reached only through the typed bindings in `winapi`.
"""

import contextlib
import gc
import sys
import traceback
from enum import Enum, auto
from typing import Final

import keeper
import usage
from config import APPS
from protocol import (
    ASSISTANT_WINDOW,
    LAYOUT_MAGIC,
    LAYOUT_WINDOW,
    MAGIC,
    WM_INDICATOR,
    WM_KEY_STATE,
    WM_LAYOUT_STATE,
    WM_QUERY_LAYOUT_STATE,
    WM_QUIT_LAYOUT,
    WM_REQUEST,
    WM_TOGGLE_LAYOUT,
    LayoutState,
    Request,
    encode,
    encode_indicator,
)
from winapi import inputs, system, windows

from .engine import Engine, Indicator, Signal, Verdict
from .layout import ESCAPE, TAPPING_TERM_MS, VOICE_KEYS, Key, Mod, Scancode, Unmapped, slot

LOCK_NAME: Final = "Local\\miryoku"
"""Name of the lock that stops a second copy from running in this Windows session."""
USAGE_FLUSH_MS: Final = 10 * 60 * 1000
"""How often usage counts are written to the usage log."""


class WinHost:
    """What the engine needs, done with Windows calls."""

    __slots__ = ("assistant", "counts")

    def __init__(self) -> None:
        self.assistant: int | None = None
        """The assistant's window, once found."""
        self.counts: dict[str, int] = {}
        """Feature uses since the last flush."""

    def send(self, key: Key | Mod, down: bool) -> None:
        flags = (0 if down else inputs.KEYEVENTF_KEYUP) | (inputs.KEYEVENTF_EXTENDEDKEY if key.extended else 0)
        # The scancode, which some programs read, from Windows' own layout table.
        inputs.send_key(key.vk, inputs.scancode_for(key.vk), flags, LAYOUT_MAGIC)

    def start_timer(self) -> int:
        return system.set_timer(TAPPING_TERM_MS)

    def stop_timer(self, timer: int) -> None:
        system.kill_timer(timer)

    def signal(self, signal: Signal) -> None:
        match signal:
            case Signal.ON:
                system.beep(system.MB_OK)
                self.tell_assistant(WM_LAYOUT_STATE, LayoutState.ON.value, 0)
            case Signal.OFF:
                system.beep(system.MB_ICONHAND)
                self.tell_assistant(WM_LAYOUT_STATE, LayoutState.OFF.value, 0)
            case Signal.CAPS_WORD:
                system.beep(system.MB_ICONASTERISK)

    def request(self, request: Request, down: bool) -> None:
        self.tell_assistant(WM_REQUEST, encode(request, APPS), int(down))

    def type_text(self, text: str) -> None:
        inputs.type_text(text, LAYOUT_MAGIC)

    def used(self, feature: str) -> None:
        self.counts[feature] = self.counts.get(feature, 0) + 1

    def indicate(self, indicator: Indicator) -> None:
        layer: int = indicator.layer.value if indicator.layer else 0
        self.tell_assistant(WM_INDICATOR, encode_indicator(layer, indicator.locked, indicator.caps_word), 0)

    def tell_assistant(self, message: int, wparam: int, lparam: int) -> None:
        """Posts to the assistant's window; nothing happens if it isn't running."""
        for _ in range(2):  # a cached window may be gone (the assistant restarted): look again
            if self.assistant is None:
                self.assistant = windows.find(ASSISTANT_WINDOW)
                if self.assistant is None:
                    return
            if system.post_message(self.assistant, message, wparam, lparam):
                return
            self.assistant = None

    def flush_usage(self) -> None:
        counts, self.counts = self.counts, {}
        with contextlib.suppress(OSError):  # the log is a convenience: never let it stop the keyboard
            usage.record_counts("layout", counts)


class Exit(Enum):
    QUIT = auto()
    """Quit normally (the escape key was held, or the assistant asked)."""
    ALREADY_RUNNING = auto()
    HOOK_REFUSED = auto()
    """Windows refused the keyboard hook."""


class Keyboard:
    """The engine, plus what the hook callback needs around it."""

    __slots__ = ("busy", "engine", "foreground", "host", "pointer", "pressed")

    def __init__(self) -> None:
        self.engine = Engine()
        self.host = WinHost()
        self.foreground = 0
        self.pointer = inputs.cursor_position()
        self.pressed: set[Scancode] = set()
        """Physical keys for the overlay, independent of delayed tap/hold decisions."""
        self.busy = False
        """Set while the engine runs; a re-entrant call lets its key through."""

    def on_message(self, message: int) -> None:
        """Tray requests; toggling uses the same re-entrancy guard as physical keys."""
        if message == WM_QUERY_LAYOUT_STATE:
            state = LayoutState.ON if self.engine.enabled else LayoutState.OFF
            self.host.tell_assistant(WM_LAYOUT_STATE, state.value, 0)
            self.host.indicate(self.engine.shown)
            self.host.tell_assistant(WM_KEY_STATE, 0, 0)
            for scancode in sorted(self.pressed):
                self.host.tell_assistant(WM_KEY_STATE, scancode, 1)
        if message != WM_TOGGLE_LAYOUT:
            return
        self.busy = True
        try:
            self.engine.toggle(self.host)
            self.clear_pressed()
        except Exception:
            self.input_failed()
        finally:
            self.busy = False

    def input_failed(self) -> None:
        """Report an input failure and leave the physical keyboard usable."""
        traceback.print_exc(file=sys.stderr)
        self.engine.enabled = False
        self.engine.typed = ""
        self.clear_pressed()
        with contextlib.suppress(OSError):
            self.engine.release_all(self.host)
        self.host.signal(Signal.OFF)

    def show_key(self, scancode: Scancode, down: bool) -> None:
        """Send each physical transition once; repeats and unpictured keys add nothing."""
        if scancode != ESCAPE and scancode not in VOICE_KEYS and slot(scancode) is Unmapped.FREE:
            return
        if (scancode in self.pressed) == down:
            return
        if down:
            self.pressed.add(scancode)
        else:
            self.pressed.discard(scancode)
        self.host.tell_assistant(WM_KEY_STATE, scancode, int(down))

    def clear_pressed(self) -> None:
        self.pressed.clear()
        self.host.tell_assistant(WM_KEY_STATE, 0, 0)

    def hook_proc(self, code: int, wparam: int, lparam: int) -> int:
        """Called by Windows for every key, before any application sees it."""
        if code == 0 and wparam in (system.WM_KEYDOWN, system.WM_SYSKEYDOWN, system.WM_KEYUP, system.WM_SYSKEYUP):
            down = wparam in (system.WM_KEYDOWN, system.WM_SYSKEYDOWN)
            try:
                if self.swallow(inputs.read_key_event(lparam), down):
                    return 1
            except Exception:
                # A bug must never eat the keyboard: report it and let the key through.
                self.input_failed()
        return inputs.call_next_hook(code, wparam, lparam)

    def swallow(self, key: inputs.KeyEvent, down: bool) -> bool:
        """Runs the engine on one key; True if the original key should be blocked."""
        # Our own keys, and extended keys (arrows, numpad Enter...), which share
        # scancodes with main keys, are never ours.
        if self.busy or key.extra == LAYOUT_MAGIC:
            return False
        foreground = windows.foreground()
        pointer = inputs.cursor_position()
        if foreground != self.foreground or pointer != self.pointer or key.flags & inputs.LLKHF_EXTENDED or key.extra == MAGIC:
            self.engine.typed = ""
        self.foreground = foreground
        self.pointer = pointer
        if key.extra == MAGIC or key.flags & inputs.LLKHF_EXTENDED:
            return False
        self.busy = True
        try:
            was_enabled = self.engine.enabled
            if was_enabled and not key.flags & inputs.LLKHF_INJECTED:
                self.show_key(Scancode(key.scancode), down)
            verdict = self.engine.on_key(self.host, Scancode(key.scancode), down, key.time)
            if self.engine.enabled != was_enabled or verdict is Verdict.QUIT:
                self.clear_pressed()
        finally:
            self.busy = False
        match verdict:
            case Verdict.PASS:
                return False
            case Verdict.SWALLOW:
                return True
            case Verdict.QUIT:
                system.post_quit()
                return True


def run() -> Exit:
    """Blocks until the layout quits."""
    lock = system.create_lock(LOCK_NAME)
    if lock is None:
        return Exit.ALREADY_RUNNING
    window = windows.create_hidden(LAYOUT_WINDOW)  # receives control requests from the assistant and keeper
    try:
        keyboard = Keyboard()
        hook = inputs.set_keyboard_hook(keyboard.hook_proc)
        if hook is None:
            return Exit.HOOK_REFUSED
        flush_timer = system.set_timer(USAGE_FLUSH_MS)
        keeper.ready()  # tells the keeper this version came up
        keyboard.host.tell_assistant(WM_LAYOUT_STATE, LayoutState.ON.value, 0)
        # Nothing here creates reference cycles, so the cycle collector has nothing to
        # do; turning it off means it can never pause the hook mid-keypress.
        gc.collect()
        gc.freeze()
        gc.disable()
        try:
            # Windows only delivers hook calls and timer ticks while this loop waits.
            for message in system.messages():
                if message.message == system.WM_TIMER:
                    if message.wparam == flush_timer:
                        keyboard.host.flush_usage()
                    else:
                        keyboard.engine.timeout(keyboard.host, message.wparam)
                elif message.message == WM_QUIT_LAYOUT:
                    break
                else:
                    keyboard.on_message(message.message)
        finally:
            inputs.remove_hook(hook)
            keyboard.clear_pressed()
            keyboard.engine.release_all(keyboard.host)  # let go of anything still held
            keyboard.host.flush_usage()
            keyboard.host.tell_assistant(WM_LAYOUT_STATE, LayoutState.QUIT.value, 0)
            gc.enable()
        return Exit.QUIT
    finally:
        windows.destroy(window)
        system.release_lock(lock)


def show_error(message: str) -> None:
    system.message_box(message, "Miryoku", system.MB_ICONHAND)
