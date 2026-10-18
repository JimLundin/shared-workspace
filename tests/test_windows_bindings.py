"""Windows bindings at their boundaries, without touching the real keyboard."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows bindings")

from miryoku.win import Keyboard
from protocol import LAYOUT_MAGIC, MAGIC
from tests.test_engine import Harness
from winapi import inputs


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


def test_snippets_still_expand_with_tagged_layout_output() -> None:
    rig = Harness()
    for name in (";", ";", "r", "l", "a"):
        rig.tap(name)
    assert "~Thanks!" in rig.take()
