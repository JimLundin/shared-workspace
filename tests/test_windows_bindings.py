"""Windows bindings at their boundaries, without touching the real keyboard."""

import sys
import threading
import unittest
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows bindings")

from assistant.activity import ActivityTracker
from assistant.app import AgentRunner, AssistantApp
from assistant.llm import Settings
from assistant.meetings import Meetings
from assistant.overlay import OverlayState
from assistant.ui import Ui
from miryoku.engine import Indicator
from miryoku.layout import AGENT, ESCAPE, TALK, Key, Layer, Scancode
from miryoku.win import Keyboard
from protocol import (
    LAYOUT_MAGIC,
    LAYOUT_WINDOW,
    MAGIC,
    WM_INDICATOR,
    WM_KEY_STATE,
    WM_LAYOUT_STATE,
    WM_QUERY_LAYOUT_STATE,
    WM_REQUEST,
    WM_TOGGLE_LAYOUT,
    LayoutState,
    Tool,
    decode_indicator,
)
from tests.test_engine import Harness
from winapi import inputs, recording, shell, windows
from winapi.shapes import Rect


@contextmanager
def observed_keyboard() -> Generator[tuple[Keyboard, list[tuple[int, int, int]]]]:
    """Capture notifications while keeping keyboard output and timers off the desktop."""
    messages: list[tuple[int, int, int]] = []

    def notify(message: int, wparam: int, lparam: int) -> None:
        messages.append((message, wparam, lparam))

    with (
        patch("miryoku.win.inputs.cursor_position", return_value=(0, 0)),
        patch("miryoku.win.windows.foreground", return_value=1),
        patch("miryoku.win.WinHost.tell_assistant", side_effect=notify),
        patch("miryoku.win.WinHost.send"),
        patch("miryoku.win.WinHost.start_timer", return_value=17),
        patch("miryoku.win.WinHost.stop_timer"),
        patch("miryoku.win.system.beep"),
        patch("miryoku.win.traceback.print_exc"),
    ):
        yield Keyboard(), messages


def test_physical_press_is_reported_before_a_tap_hold_is_resolved() -> None:
    with observed_keyboard() as (keyboard, messages):
        key = inputs.KeyEvent(0x4D, 0x32, 0, 10, 0)  # M: Enter/Symbols thumb
        assert keyboard.swallow(key, True)
        assert messages == [(WM_KEY_STATE, 0x32, 1)]
        assert keyboard.engine.layer() is None
        assert keyboard.pressed == {Scancode(0x32)}
        keyboard.swallow(key, True)  # auto-repeat must not refresh the highlight
        assert messages == [(WM_KEY_STATE, 0x32, 1)]
        keyboard.swallow(inputs.KeyEvent(0x4D, 0x32, 0, 40, 0), False)
        keyboard.swallow(inputs.KeyEvent(0x4D, 0x32, 0, 41, 0), False)
        assert messages == [(WM_KEY_STATE, 0x32, 1), (WM_KEY_STATE, 0x32, 0)]
        assert not keyboard.pressed


def test_generated_and_extended_input_does_not_light_physical_keys() -> None:
    for flags, extra in ((0, LAYOUT_MAGIC), (0, MAGIC), (inputs.LLKHF_INJECTED, 0), (inputs.LLKHF_EXTENDED, 0)):
        with observed_keyboard() as (keyboard, messages):
            keyboard.swallow(inputs.KeyEvent(0x4D, 0x32, flags, 10, extra), True)
            keyboard.swallow(inputs.KeyEvent(0x4D, 0x32, flags, 40, extra), False)
            assert not any(message == WM_KEY_STATE for message, _, _ in messages)
            assert not keyboard.pressed


def test_voice_and_unused_keys_light_up_but_unpictured_keys_do_not() -> None:
    with observed_keyboard() as (keyboard, messages):
        for scancode in (TALK, AGENT, 0x15, 0x56, 0x35, 0x39):  # B, N, Y, ISO <, old thumb position, physical Space
            keyboard.swallow(inputs.KeyEvent(0, scancode, 0, 10, 0), True)
            keyboard.swallow(inputs.KeyEvent(0, scancode, 0, 40, 0), False)
        keys = [(scancode, down) for message, scancode, down in messages if message == WM_KEY_STATE]
        assert keys == [(scancode, down) for scancode in (TALK, AGENT, 0x15, 0x56, 0x35) for down in (1, 0)]


def test_n_starts_agent_voice_immediately_and_updates_its_highlight() -> None:
    with observed_keyboard() as (keyboard, messages):
        key = inputs.KeyEvent(0x4E, 0x31, 0, 10, 0)  # physical N, beside B
        assert keyboard.swallow(key, True)
        assert messages == [(WM_KEY_STATE, 0x31, 1), (WM_REQUEST, Tool.AGENT_VOICE.value, 1)]
        assert keyboard.pressed == {Scancode(0x31)}
        keyboard.swallow(key, True)
        keyboard.swallow(inputs.KeyEvent(0x4E, 0x31, 0, 300, 0), False)
        assert messages == [
            (WM_KEY_STATE, 0x31, 1), (WM_REQUEST, Tool.AGENT_VOICE.value, 1),
            (WM_KEY_STATE, 0x31, 0), (WM_REQUEST, Tool.AGENT_VOICE.value, 0),
        ]
        assert not keyboard.pressed


def test_layout_query_resets_then_replays_all_physically_held_keys() -> None:
    with observed_keyboard() as (keyboard, messages):
        for scancode in (0x32, 0x10):
            keyboard.swallow(inputs.KeyEvent(0, scancode, 0, 10, 0), True)
        messages.clear()
        keyboard.on_message(WM_QUERY_LAYOUT_STATE)
        assert messages[0] == (WM_LAYOUT_STATE, LayoutState.ON.value, 0)
        assert messages[1][0] == WM_INDICATOR
        assert messages[2:] == [(WM_KEY_STATE, 0, 0), (WM_KEY_STATE, 0x10, 1), (WM_KEY_STATE, 0x32, 1)]


def test_toggle_and_input_failure_clear_held_keys() -> None:
    with observed_keyboard() as (keyboard, messages):
        key = inputs.KeyEvent(0x4D, 0x32, 0, 10, 0)
        keyboard.swallow(key, True)
        keyboard.on_message(WM_TOGGLE_LAYOUT)
        assert not keyboard.pressed
        assert messages[-1] == (WM_KEY_STATE, 0, 0)
        messages.clear()
        assert not keyboard.swallow(key, True)
        assert messages == []
        keyboard.on_message(WM_TOGGLE_LAYOUT)
        keyboard.swallow(key, True)
        keyboard.input_failed()
        assert not keyboard.pressed
        assert (WM_KEY_STATE, 0, 0) in messages[-3:]
        assert not keyboard.engine.enabled


def test_escape_toggle_clears_highlights_with_other_keys_still_held() -> None:
    with observed_keyboard() as (keyboard, messages):
        keyboard.swallow(inputs.KeyEvent(0, 0x32, 0, 10, 0), True)
        keyboard.swallow(inputs.KeyEvent(0, ESCAPE, 0, 20, 0), True)
        keyboard.swallow(inputs.KeyEvent(0, ESCAPE, 0, 40, 0), False)
        assert not keyboard.engine.enabled
        assert not keyboard.pressed
        assert messages[-1] == (WM_KEY_STATE, 0, 0)


def test_assistant_routes_physical_chords_and_resets_to_the_overlay() -> None:
    app = menu_app(LayoutState.ON)
    app.ui = Ui.__new__(Ui)
    shown: list[tuple[frozenset[Scancode], bool]] = []

    def show(pressed: frozenset[Scancode], *, reset: bool = False) -> None:
        shown.append((pressed, reset))

    with patch("assistant.app.Ui.show_pressed_keys", side_effect=show):
        for key, down in ((0x32, 1), (0x10, 1), (0x10, 0), (0, 0)):
            assert app.on_message(1, WM_KEY_STATE, key, down) == 0
    assert shown == [
        (frozenset({Scancode(0x32)}), False),
        (frozenset({Scancode(0x32), Scancode(0x10)}), False),
        (frozenset({Scancode(0x32)}), False),
        (frozenset(), True),
    ]


def test_overlay_handle_accepts_mouse_input_without_taking_keyboard_focus() -> None:
    styles: list[int] = []

    def set_style(hwnd: int, index: int, style: int) -> int:
        assert hwnd == 1 and index == -20
        styles.append(style)
        return 0

    with (
        patch("winapi.windows._GetWindowLongW", return_value=0x28),
        patch("winapi.windows._SetWindowLongW", side_effect=set_style),
        patch("winapi.windows._SetWindowSubclass", return_value=True),
    ):
        windows.make_overlay(1)
        windows.make_overlay(1, click_through=False)
    assert styles == [0x0808_00A8, 0x0808_0088]


def test_overlay_refuses_mouse_activation_but_still_delivers_other_messages() -> None:
    delivered: list[int] = []

    def default_message(hwnd: int, message: int, wparam: int, lparam: int) -> int:
        delivered.append(message)
        return 7

    with patch("winapi.windows._DefSubclassProc", side_effect=default_message), patch("winapi.windows._RemoveWindowSubclass"):
        assert windows.overlay_message(1, 0x0021, 0, 0, 0, 0) == 3  # MA_NOACTIVATE, without discarding the click
        assert windows.overlay_message(1, 0x0201, 0, 0, 0, 0) == 7  # button press
        assert windows.overlay_message(1, 0x0082, 0, 0, 0, 0) == 7  # final destruction
    assert delivered == [0x0201, 0x0082]


def test_layout_output_preserves_snippets_but_assistant_input_resets_them() -> None:
    with patch("miryoku.win.inputs.cursor_position", return_value=(0, 0)), patch("miryoku.win.windows.foreground", return_value=1):
        keyboard = Keyboard()
        keyboard.foreground = 1
        keyboard.engine.typed = ",,t"
        assert not keyboard.swallow(inputs.KeyEvent(0x54, 0, 0, 0, LAYOUT_MAGIC), True)
        assert keyboard.engine.typed == ",,t"
        assert not keyboard.swallow(inputs.KeyEvent(0x48, 0, 0, 0, MAGIC), True)
        assert keyboard.engine.typed == ""


def test_physical_extended_arrow_clears_snippets() -> None:
    with patch("miryoku.win.inputs.cursor_position", return_value=(0, 0)), patch("miryoku.win.windows.foreground", return_value=1):
        keyboard = Keyboard()
        keyboard.foreground = 1
        keyboard.engine.typed = ",,t"
        assert not keyboard.swallow(inputs.KeyEvent(0x25, 0x4B, inputs.LLKHF_EXTENDED, 0, 0), True)
        assert keyboard.engine.typed == ""


def test_rejected_sendinput_is_reported_without_real_injection() -> None:
    with patch("winapi.inputs._SendInput", return_value=0):
        try:
            inputs.send_keys([inputs.KeyStroke(0x41, True)], MAGIC)
        except OSError:
            pass
        else:
            raise AssertionError("rejected input must raise")


def test_extra_mouse_buttons_and_horizontal_scroll_preserve_native_flags() -> None:
    events: list[tuple[int, int, int]] = []

    def send(batch: Sequence[inputs.INPUT]) -> int:
        for event in batch:
            mouse = cast(inputs.MOUSEINPUT, cast(inputs.INPUT_UNION, event.u).mi)
            events.append((cast(int, mouse.dwFlags), cast(int, mouse.mouseData), cast(int, mouse.dwExtraInfo)))
        return len(batch)

    with patch("winapi.inputs._send", side_effect=send):
        inputs.mouse_button("x1", True, MAGIC)
        inputs.mouse_button("x1", False, MAGIC)
        inputs.mouse_button("x2", True, MAGIC)
        inputs.mouse_button("x2", False, MAGIC)
        inputs.scroll(2, MAGIC, horizontal=True)
        inputs.scroll(-1, MAGIC)
    assert events == [
        (0x80, 1, MAGIC), (0x100, 1, MAGIC), (0x80, 2, MAGIC), (0x100, 2, MAGIC),
        (0x1000, 240, MAGIC), (0x800, 0xFFFF_FF88, MAGIC),
    ]


def test_snippets_still_expand_with_tagged_layout_output() -> None:
    rig = Harness()
    for name in (";", ";", "r", "l", "a"):
        rig.tap(name)
    assert "~Thanks!" in rig.take()


def test_layout_replies_to_queries_and_tray_toggles_without_key_injection() -> None:
    states: list[int] = []

    def post_message(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
        if message == WM_LAYOUT_STATE:
            states.append(wparam)
        return True

    with (
        patch("miryoku.win.inputs.cursor_position", return_value=(0, 0)),
        patch("miryoku.win.windows.find", return_value=1),
        patch("miryoku.win.system.post_message", side_effect=post_message),
        patch("miryoku.win.system.beep"),
    ):
        keyboard = Keyboard()
        keyboard.on_message(WM_QUERY_LAYOUT_STATE)
        keyboard.on_message(WM_TOGGLE_LAYOUT)
        keyboard.on_message(WM_QUERY_LAYOUT_STATE)
        keyboard.on_message(WM_TOGGLE_LAYOUT)
        assert states == [LayoutState.ON.value, LayoutState.OFF.value, LayoutState.OFF.value, LayoutState.ON.value]
        assert not keyboard.busy


def test_failed_key_release_during_tray_toggle_still_turns_the_layout_off() -> None:
    with (
        patch("miryoku.win.inputs.cursor_position", return_value=(0, 0)),
        patch("miryoku.win.WinHost.send", side_effect=OSError("input was refused")),
        patch("miryoku.win.WinHost.tell_assistant"),
        patch("miryoku.win.system.beep"),
        patch("miryoku.win.traceback.print_exc"),
    ):
        keyboard = Keyboard()
        keyboard.engine.pressed[0] = Key.Q  # an output key held before the tray was opened
        keyboard.on_message(WM_TOGGLE_LAYOUT)
        assert not keyboard.engine.enabled
        assert not keyboard.busy


def test_layout_query_includes_the_current_locked_layer_for_a_new_overlay() -> None:
    layers: list[tuple[int, bool, bool]] = []

    def post_message(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
        if message == WM_INDICATOR:
            layers.append(decode_indicator(wparam))
        return True

    with (
        patch("miryoku.win.inputs.cursor_position", return_value=(0, 0)),
        patch("miryoku.win.windows.find", return_value=1),
        patch("miryoku.win.system.post_message", side_effect=post_message),
    ):
        keyboard = Keyboard()
        keyboard.engine.shown = Indicator(Layer.SYM, True, True)
        keyboard.on_message(WM_QUERY_LAYOUT_STATE)
    assert layers == [(Layer.SYM.value, True, True)]


def menu_app(state: LayoutState | None) -> AssistantApp:
    """Only the state the menu needs; no native windows or microphone are opened."""
    app = AssistantApp.__new__(AssistantApp)
    app.layout = state
    app.overlay = OverlayState(layout_on=state is LayoutState.ON)
    app.window = shell.AppWindow.__new__(shell.AppWindow)
    app.window.handle = 1
    app.agent = AgentRunner.__new__(AgentRunner)
    app.agent.thread = None
    app.agent.settings = Settings("", "", "", "", "", "", "")
    app.meetings = Meetings(app.agent.settings, lambda source: recording.MeetingAudio(), root=Path(__file__).parent / "missing-meeting-fixture")
    return app


def check_layout_toggle(state: LayoutState) -> None:
    requests: list[int] = []

    def choose(window: int, items: Sequence[shell.MenuItem]) -> int:
        item = items[0]
        assert item.text == f"Layout: {state.name.lower()}\tTap §"
        assert item.enabled
        assert item.checked == (state is LayoutState.ON)
        return item.id

    def find(title: str) -> int:
        assert title == LAYOUT_WINDOW
        return 2

    def post_message(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
        assert hwnd == 2
        requests.append(message)
        return True

    with (
        patch("assistant.app.shell.show_menu", side_effect=choose),
        patch("assistant.app.windows.find", side_effect=find),
        patch("assistant.app.system.post_message", side_effect=post_message),
    ):
        menu_app(state).menu()
    assert requests == [WM_TOGGLE_LAYOUT]


def test_layout_status_is_the_toggle_and_shows_its_hotkey() -> None:
    for state in (LayoutState.ON, LayoutState.OFF):
        check_layout_toggle(state)


def test_stopped_layout_has_a_start_action_and_no_active_toggle_hotkey() -> None:
    def inspect_menu(window: int, items: Sequence[shell.MenuItem]) -> int:
        assert items[0].text == "Layout: not running"
        assert not items[0].enabled
        assert not items[0].checked
        assert items[1].text == "Start layout"
        assert items[1].enabled
        return 0

    with patch("assistant.app.shell.show_menu", side_effect=inspect_menu):
        for state in (None, LayoutState.QUIT):
            menu_app(state).menu()


def test_tray_overlay_toggle_updates_and_remembers_the_preference() -> None:
    app = menu_app(LayoutState.ON)
    saved: list[bool] = []
    shown: list[bool] = []

    def choose(window: int, items: Sequence[shell.MenuItem]) -> int:
        item = next(item for item in items if item.text == "Keyboard overlay")
        assert item.checked == app.overlay.enabled
        return item.id

    def show_overlay(work: Rect) -> None:
        shown.append(app.overlay.visible)

    with (
        patch("assistant.app.shell.show_menu", side_effect=choose),
        patch("assistant.app.indicator_area", return_value=Rect(0, 0, 1920, 1040)),
        patch("assistant.app.AssistantApp.show_overlay", side_effect=show_overlay),
        patch("assistant.app.save_enabled", side_effect=saved.append),
    ):
        app.menu()
        app.menu()
    assert shown == [False, True]
    assert saved == [False, True]


def test_assistant_queries_a_layout_that_started_before_it() -> None:
    requests: list[int] = []

    def post_message(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
        assert hwnd == 2
        requests.append(message)
        return True

    app = menu_app(None)
    app.activity = ActivityTracker(lambda: "unavailable", lambda: False, enabled=False)
    app.worker = threading.Thread()
    with (
        patch("assistant.app.windows.find", return_value=2),
        patch("assistant.app.system.post_message", side_effect=post_message),
        patch("assistant.app.system.run_message_loop"),
        patch("assistant.app.keeper.ready"),
        patch("threading.Thread.start"),
    ):
        app.run()
    assert requests == [WM_QUERY_LAYOUT_STATE]
