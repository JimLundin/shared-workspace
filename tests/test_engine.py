"""Engine tests: scripted key presses in, Windows key events out. Run anywhere with
`python -m unittest`."""

from __future__ import annotations

import unittest

from miryoku.engine import MAX_BUFFERED, Engine, Signal, Verdict
from miryoku.layout import ESCAPE, QUICK_TAP_MS, QUIT_HOLD_MS, STREAK_MS, Scancode

NAMES = {0xA0: "SFT", 0xA2: "CTL", 0xA4: "ALT", 0x20: "spc", 0x25: "left", 0x08: "bksp", 0xDB: "[", 0xC0: "`", 0xBE: "."}

PHYSICAL = {  # physical key by its QWERTY name
    "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07,
    "8": 0x09, "9": 0x0A, "0": 0x0B, "-": 0x0C, "=": 0x0D,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14,
    "i": 0x17, "o": 0x18, "p": 0x19, "[": 0x1A, "]": 0x1B,
    "caps": 0x3A, "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21,
    "k": 0x25, "l": 0x26, ";": 0x27, "'": 0x28, "#": 0x2B,
    "x": 0x2D, "c": 0x2E, "v": 0x2F, ",": 0x33, ".": 0x34, "/": 0x35,
}  # fmt: skip

ESC = ESCAPE
SPACEBAR = Scancode(0x39)


def sc(name: str) -> Scancode:
    return Scancode(PHYSICAL[name])


class FakeHost:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.timer = 0

    def send(self, vk: int, down: bool) -> None:
        if vk in NAMES:
            name = NAMES[vk]
        elif 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
            name = chr(vk).lower()
        else:
            name = f"{vk:#x}"
        self.log.append(("+" if down else "-") + name)

    def start_timer(self) -> int:
        self.timer += 1
        return self.timer

    def stop_timer(self, timer: int) -> None:
        pass

    def signal(self, signal: Signal) -> None:
        self.log.append(f"!{signal.name}")


class Harness:
    def __init__(self) -> None:
        self.e = Engine()
        self.h = FakeHost()
        self.now = 0

    def key(self, scancode: Scancode, down: bool, now: int) -> Verdict:
        return self.e.on_key(self.h, scancode, down, now)

    def at(self, ms: int) -> Harness:
        self.now = ms
        return self

    def d(self, name: str) -> None:
        self.key(sc(name), True, self.now)

    def u(self, name: str) -> None:
        self.key(sc(name), False, self.now)

    def tap(self, name: str) -> None:
        self.d(name)
        self.u(name)

    def timeout_last(self) -> None:
        """Fire the most recently started timer."""
        self.e.timeout(self.h, self.h.timer)

    def take(self) -> str:
        log, self.h.log = self.h.log, []
        return " ".join(log)


def both_shifts(t: Harness) -> None:
    """Hold both Shift keys (t and n) past the tapping term."""
    t.d("r")
    t.d("o")
    t.timeout_last()  # t: Shift; n now undecided
    t.timeout_last()  # n: Shift too
    t.u("o")
    t.u("r")
    t.take()


class Basics(unittest.TestCase):
    def test_plain_letters(self) -> None:
        t = Harness()
        for name in ("t", "2", "4"):
            t.tap(name)
        self.assertEqual(t.take(), "+g -g +q -q +f -f")

    def test_home_row_tap(self) -> None:
        t = Harness()
        t.tap("r")
        self.assertEqual(t.take(), "+t -t")

    def test_roll_is_two_taps(self) -> None:
        t = Harness()
        t.d("r")
        t.d("i")
        t.u("r")
        t.u("i")
        self.assertEqual(t.take(), "+t -t +m -m")

    def test_hold_shift(self) -> None:
        t = Harness()
        t.d("r")
        t.tap("8")
        t.u("r")  # t + j (other hand)
        self.assertEqual(t.take(), "+SFT +j -j -SFT")

    def test_timeout_hold(self) -> None:
        t = Harness()
        t.d("e")
        t.timeout_last()
        t.tap("caps")
        t.u("e")
        self.assertEqual(t.take(), "+CTL +z -z -CTL")

    def test_nav_layer(self) -> None:
        t = Harness()
        t.d("c")
        t.tap("o")
        t.tap("0")
        t.u("c")
        self.assertEqual(t.take(), "+left -left +CTL +c -c -CTL")

    def test_space_tap(self) -> None:
        t = Harness()
        t.tap("c")
        self.assertEqual(t.take(), "+spc -spc")

    def test_num_layer(self) -> None:
        t = Harness()
        t.d(".")
        t.tap("w")
        t.tap("x")
        t.u(".")
        self.assertEqual(t.take(), "+4 -4 +. -.")

    def test_sym_layer(self) -> None:
        t = Harness()
        t.d(",")
        t.tap("2")
        t.tap("caps")
        t.u(",")
        self.assertEqual(t.take(), "+SFT +[ -SFT -[ +SFT +` -SFT -`")

    def test_mod_plus_layer(self) -> None:
        t = Harness()
        t.d("r")
        t.d(".")
        t.tap("w")
        t.u(".")
        t.u("r")
        self.assertEqual(t.take(), "+SFT +4 -4 -SFT")

    def test_release_all_lets_go(self) -> None:
        t = Harness()
        t.d("r")
        t.tap("8")
        t.take()
        t.e.release_all(t.h)
        self.assertEqual(t.take(), "-SFT")

    def test_unrelated_keys_pass(self) -> None:
        t = Harness()
        self.assertEqual(t.key(SPACEBAR, True, 0), Verdict.PASS)
        self.assertEqual(t.take(), "")

    def test_unused_keys_are_blocked(self) -> None:
        t = Harness()
        self.assertEqual(t.key(Scancode(0x23), True, 0), Verdict.SWALLOW)  # QWERTY H
        self.assertEqual(t.take(), "")


class EscapeKey(unittest.TestCase):
    def test_tap_toggles_off_and_on(self) -> None:
        t = Harness()
        self.assertEqual(t.key(ESC, True, 100), Verdict.SWALLOW)
        self.assertEqual(t.key(ESC, False, 150), Verdict.SWALLOW)
        self.assertEqual(t.key(sc("q"), True, 200), Verdict.PASS)  # off: plain keyboard
        self.assertEqual(t.key(sc("q"), False, 210), Verdict.PASS)
        t.key(ESC, True, 300)
        t.key(ESC, False, 350)
        t.tap("q")  # on again: Colemak-DH
        self.assertEqual(t.take(), "!OFF !ON +a -a")

    def test_hold_quits(self) -> None:
        t = Harness()
        t.key(ESC, True, 1000)
        t.key(ESC, True, 1000 + QUIT_HOLD_MS // 2)  # auto-repeat doesn't restart the clock
        self.assertEqual(t.key(ESC, False, 1000 + QUIT_HOLD_MS), Verdict.QUIT)

    def test_survives_timer_wraparound(self) -> None:
        t = Harness()
        t.key(ESC, True, 0xFFFF_FFFF - 100)
        self.assertEqual(t.key(ESC, False, 50), Verdict.SWALLOW)  # 151 ms: a tap

    def test_switching_off_releases_held_mods(self) -> None:
        t = Harness()
        t.d("r")
        t.tap("8")
        t.take()  # Shift is now held
        t.key(ESC, True, 0)
        t.key(ESC, False, 10)
        self.assertEqual(t.take(), "-SFT !OFF")


class TypingStreak(unittest.TestCase):
    def test_sends_home_row_letter_on_press(self) -> None:
        t = Harness()
        t.at(0).tap("t")  # g
        t.at(STREAK_MS // 2).d("w")  # r/ALT, mid-word
        self.assertEqual(t.take(), "+g -g +r")  # r is out before release
        t.u("w")
        self.assertEqual(t.take(), "-r")

    def test_expires(self) -> None:
        t = Harness()
        t.at(0).tap("t")
        t.at(STREAK_MS * 3).d("w")  # pause: a normal tap-hold again
        self.assertEqual(t.take(), "+g -g")
        t.tap("8")
        t.u("w")
        self.assertEqual(t.take(), "+ALT +j -j -ALT")

    def test_never_blocks_shift(self) -> None:
        t = Harness()
        t.at(0).tap("c")  # space
        t.at(STREAK_MS // 3).d("r")
        t.tap("8")
        t.u("r")  # Shift+j right after a space
        self.assertEqual(t.take(), "+spc -spc +SFT +j -j -SFT")

    def test_never_blocks_layers(self) -> None:
        t = Harness()
        t.at(0).tap("t")
        t.at(STREAK_MS // 3).d(".")
        t.tap("w")
        t.u(".")  # Num layer right after a letter
        self.assertEqual(t.take(), "+g -g +4 -4")

    def test_counts_rolled_taps(self) -> None:
        t = Harness()
        t.at(0).d("r")
        t.at(STREAK_MS // 4).d("w")
        t.at(STREAK_MS // 3).u("r")  # roll t -> r
        self.assertEqual(t.take(), "+t -t +r")  # r came out on press via the streak

    def test_holds_do_not_start_a_streak(self) -> None:
        t = Harness()
        t.at(0).d("e")
        t.timeout_last()  # Ctrl held
        t.at(50).d("w")
        t.timeout_last()  # Alt held
        t.at(70).tap("2")  # Ctrl + Alt + q
        self.assertEqual(t.take(), "+CTL +ALT +q -q")


class Capacity(unittest.TestCase):
    def test_auto_repeat_is_not_buffered(self) -> None:
        t = Harness()
        t.d("e")  # s/CTL, undecided
        for _ in range(100):
            t.d("8")  # j held (other hand), auto-repeating
        self.assertEqual(t.take(), "", "still undecided: repeats did not fill the buffer")
        t.u("e")
        self.assertEqual(t.take(), "+s -s +j", "tap s, then j replayed once")

    def test_full_buffer_decides_hold(self) -> None:
        # More distinct keys down than nine fingers can press: decide as a hold.
        plain = ["2", "3", "4", "5", "6", "8", "9", "0", "-", "=", "t", "i", "caps", "s", "d", "f", "k", "l", ";"]
        self.assertGreater(len(plain), MAX_BUFFERED)
        t = Harness()
        t.d("o")  # n/SFT (right hand); the first key, q, is on the other hand
        for name in plain:
            t.d(name)
        self.assertTrue(t.take().startswith("+SFT +q"), "a full buffer resolves the key as a hold")


class CapsWord(unittest.TestCase):
    def test_capitalises_until_space(self) -> None:
        t = Harness()
        both_shifts(t)
        t.tap("2")
        t.tap("3")  # q w
        self.assertEqual(t.take(), "+SFT +q -SFT -q +SFT +w -SFT -w")
        t.tap("c")
        t.tap("2")  # space ends it
        self.assertEqual(t.take(), "+spc -spc +q -q")

    def test_keeps_going_through_digits_and_underscore(self) -> None:
        t = Harness()
        both_shifts(t)
        t.d(".")
        t.tap("w")
        t.u(".")  # Num layer: 4
        t.d(",")
        t.tap("v")
        t.u(",")  # Sym layer: _
        t.tap("2")
        self.assertEqual(t.take(), "+4 -4 +SFT +0xbd -SFT -0xbd +SFT +q -SFT -q")

    def test_toggles_off_and_clears_on_switch_off(self) -> None:
        t = Harness()
        both_shifts(t)
        both_shifts(t)  # off again
        t.tap("2")
        self.assertEqual(t.take(), "+q -q")
        both_shifts(t)
        t.key(ESC, True, 0)
        t.key(ESC, False, 10)  # off
        t.key(ESC, True, 20)
        t.key(ESC, False, 30)  # on: caps word is gone
        t.tap("2")
        self.assertEqual(t.take(), "!OFF !ON +q -q")

    def test_signals_when_turned_on(self) -> None:
        t = Harness()
        t.d("r")
        t.d("o")
        t.timeout_last()
        t.timeout_last()
        self.assertIn("!CAPS_WORD", t.take())


class LayerLock(unittest.TestCase):
    def test_keeps_nav_until_tapped_again(self) -> None:
        t = Harness()
        t.d("c")
        t.tap("2")
        t.u("c")  # hold Space thumb, tap lock, let go
        t.tap("o")  # still Nav: left
        self.assertEqual(t.take(), "+left -left")
        t.tap("2")  # lock key again: unlocked
        t.tap("o")  # base: n
        self.assertEqual(t.take(), "+n -n")

    def test_on_num_and_cleared_by_escape(self) -> None:
        t = Harness()
        t.d(".")
        t.tap("=")
        t.u(".")  # hold Backspace thumb, tap lock (right pinky)
        t.tap("w")  # still Num: 4
        self.assertEqual(t.take(), "+4 -4")
        t.key(ESC, True, 0)
        t.key(ESC, False, 10)  # off
        t.key(ESC, True, 20)
        t.key(ESC, False, 30)  # on: lock is gone
        t.tap("w")
        self.assertEqual(t.take(), "!OFF !ON +r -r")


class ChordalHold(unittest.TestCase):
    def test_same_hand_roll_is_two_taps_at_once(self) -> None:
        t = Harness()
        t.d("e")
        t.d("4")  # s/CTL then f, same hand
        self.assertEqual(t.take(), "+s -s +f", "decided on the second key's press")
        t.u("4")
        t.u("e")
        self.assertEqual(t.take(), "-f")

    def test_other_hand_key_still_gets_the_modifier(self) -> None:
        t = Harness()
        t.d("e")
        t.tap("8")
        t.u("e")  # Ctrl + j
        self.assertEqual(t.take(), "+CTL +j -j -CTL")

    def test_same_hand_shortcut_works_after_holding(self) -> None:
        t = Harness()
        t.d("e")
        t.timeout_last()  # held past the tapping term
        t.tap("caps")
        t.u("e")  # Ctrl + z, one hand
        self.assertEqual(t.take(), "+CTL +z -z -CTL")

    def test_layer_thumbs_are_not_affected(self) -> None:
        t = Harness()
        t.d("c")
        t.tap("e")
        t.u("c")  # Nav thumb (left) + left-hand Ctrl key
        self.assertFalse(t.take().startswith("+spc"), "the Nav thumb was not turned into a tap")


class QuickTap(unittest.TestCase):
    def test_backspace_thumb_repeats_after_a_tap(self) -> None:
        t = Harness()
        t.at(0).tap(".")  # tap: backspace
        t.at(QUICK_TAP_MS // 2).d(".")  # again quickly and hold
        t.d(".")
        t.d(".")  # auto-repeat
        t.u(".")
        self.assertEqual(t.take(), "+bksp -bksp +bksp +bksp +bksp -bksp")

    def test_after_a_pause_the_thumb_is_a_layer_again(self) -> None:
        t = Harness()
        t.at(0).tap(".")
        t.at(QUICK_TAP_MS * 2).d(".")
        t.tap("w")
        t.u(".")  # Num layer: 4
        self.assertEqual(t.take(), "+bksp -bksp +4 -4")

    def test_home_row_letter_repeats_after_a_tap(self) -> None:
        t = Harness()
        t.at(1000).tap("r")  # t (Shift key)
        t.at(1000 + QUICK_TAP_MS // 2).d("r")
        t.d("r")
        self.assertEqual(t.take(), "+t -t +t +t", "held t repeats instead of becoming Shift")


if __name__ == "__main__":
    unittest.main()
