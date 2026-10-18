"""Engine tests: scripted key presses in, Windows key events out. Run anywhere
with `pytest`."""

from __future__ import annotations

from miryoku.engine import MAX_BUFFERED, Engine, Signal, Verdict
from miryoku.layout import ESCAPE, QUICK_TAP_MS, QUIT_HOLD_MS, STREAK_MS, TALK, Key, Mod, Scancode

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
    __slots__ = ("log", "timer")

    def __init__(self) -> None:
        self.log: list[str] = []
        self.timer = 0

    def send(self, key: Key | Mod, down: bool) -> None:
        vk = key.vk
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
    __slots__ = ("e", "h", "now")

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


# ---- basics ----------------------------------------------------------------
def test_plain_letters() -> None:
    t = Harness()
    for name in ("t", "2", "4"):
        t.tap(name)
    assert t.take() == "+g -g +q -q +f -f"


def test_home_row_tap() -> None:
    t = Harness()
    t.tap("r")
    assert t.take() == "+t -t"


def test_roll_is_two_taps() -> None:
    t = Harness()
    t.d("r")
    t.d("i")
    t.u("r")
    t.u("i")
    assert t.take() == "+t -t +m -m"


def test_hold_shift() -> None:
    t = Harness()
    t.d("r")
    t.tap("8")
    t.u("r")  # t + j (other hand)
    assert t.take() == "+SFT +j -j -SFT"


def test_timeout_hold() -> None:
    t = Harness()
    t.d("e")
    t.timeout_last()
    t.tap("caps")
    t.u("e")
    assert t.take() == "+CTL +z -z -CTL"


def test_nav_layer() -> None:
    t = Harness()
    t.d("c")
    t.tap("o")
    t.tap("0")
    t.u("c")
    assert t.take() == "+left -left +CTL +c -CTL -c"


def test_space_tap() -> None:
    t = Harness()
    t.tap("c")
    assert t.take() == "+spc -spc"


def test_num_layer() -> None:
    t = Harness()
    t.d(".")
    t.tap("w")
    t.tap("x")
    t.u(".")
    assert t.take() == "+4 -4 +. -."


def test_sym_layer() -> None:
    t = Harness()
    t.d(",")
    t.tap("2")
    t.tap("caps")
    t.u(",")
    assert t.take() == "+SFT +[ -SFT -[ +SFT +` -SFT -`"


def test_mod_plus_layer() -> None:
    t = Harness()
    t.d("r")
    t.d(".")
    t.tap("w")
    t.u(".")
    t.u("r")
    assert t.take() == "+SFT +4 -4 -SFT"


def test_release_all_lets_go() -> None:
    t = Harness()
    t.d("r")
    t.tap("8")
    t.take()
    t.e.release_all(t.h)
    assert t.take() == "-SFT"


def test_unrelated_keys_pass() -> None:
    t = Harness()
    assert t.key(SPACEBAR, True, 0) == Verdict.PASS
    assert t.take() == ""


def test_unused_keys_are_blocked() -> None:
    t = Harness()
    assert t.key(Scancode(0x23), True, 0) == Verdict.SWALLOW  # QWERTY H
    assert t.take() == ""


# ---- escape key ------------------------------------------------------------
def test_escape_tap_toggles_off_and_on() -> None:
    t = Harness()
    assert t.key(ESC, True, 100) == Verdict.SWALLOW
    assert t.key(ESC, False, 150) == Verdict.SWALLOW
    assert t.key(sc("q"), True, 200) == Verdict.PASS  # off: plain keyboard
    assert t.key(sc("q"), False, 210) == Verdict.PASS
    t.key(ESC, True, 300)
    t.key(ESC, False, 350)
    t.tap("q")  # on again: Colemak-DH
    assert t.take() == "!OFF !ON +a -a"


def test_escape_hold_quits() -> None:
    t = Harness()
    t.key(ESC, True, 1000)
    t.key(ESC, True, 1000 + QUIT_HOLD_MS // 2)  # auto-repeat doesn't restart the clock
    assert t.key(ESC, False, 1000 + QUIT_HOLD_MS) == Verdict.QUIT


def test_escape_survives_timer_wraparound() -> None:
    t = Harness()
    t.key(ESC, True, 0xFFFF_FFFF - 100)
    assert t.key(ESC, False, 50) == Verdict.SWALLOW  # 151 ms: a tap


def test_escape_switching_off_releases_held_mods() -> None:
    t = Harness()
    t.d("r")
    t.tap("8")
    t.take()  # Shift is now held
    t.key(ESC, True, 0)
    t.key(ESC, False, 10)
    assert t.take() == "-SFT !OFF"


# ---- typing streak ---------------------------------------------------------
def test_streak_sends_home_row_letter_on_press() -> None:
    t = Harness()
    t.at(0).tap("t")  # g
    t.at(STREAK_MS // 2).d("w")  # r/ALT, mid-word
    assert t.take() == "+g -g +r"  # r is out before release
    t.u("w")
    assert t.take() == "-r"


def test_streak_expires() -> None:
    t = Harness()
    t.at(0).tap("t")
    t.at(STREAK_MS * 3).d("w")  # pause: a normal tap-hold again
    assert t.take() == "+g -g"
    t.tap("8")
    t.u("w")
    assert t.take() == "+ALT +j -j -ALT"


def test_streak_never_blocks_shift() -> None:
    t = Harness()
    t.at(0).tap("c")  # space
    t.at(STREAK_MS // 3).d("r")
    t.tap("8")
    t.u("r")  # Shift+j right after a space
    assert t.take() == "+spc -spc +SFT +j -j -SFT"


def test_streak_never_blocks_layers() -> None:
    t = Harness()
    t.at(0).tap("t")
    t.at(STREAK_MS // 3).d(".")
    t.tap("w")
    t.u(".")  # Num layer right after a letter
    assert t.take() == "+g -g +4 -4"


def test_streak_counts_rolled_taps() -> None:
    t = Harness()
    t.at(0).d("r")
    t.at(STREAK_MS // 4).d("w")
    t.at(STREAK_MS // 3).u("r")  # roll t -> r
    assert t.take() == "+t -t +r"  # r came out on press via the streak


def test_streak_holds_do_not_start_a_streak() -> None:
    t = Harness()
    t.at(0).d("e")
    t.timeout_last()  # Ctrl held
    t.at(50).d("w")
    t.timeout_last()  # Alt held
    t.at(70).tap("2")  # Ctrl + Alt + q
    assert t.take() == "+CTL +ALT +q -q"


# ---- capacity --------------------------------------------------------------
def test_auto_repeat_is_not_buffered() -> None:
    t = Harness()
    t.d("e")  # s/CTL, undecided
    for _ in range(100):
        t.d("8")  # j held (other hand), auto-repeating
    assert t.take() == "", "still undecided: repeats did not fill the buffer"
    t.u("e")
    assert t.take() == "+s -s +j", "tap s, then j replayed once"


def test_full_buffer_decides_hold() -> None:
    # More distinct keys down than nine fingers can press: decide as a hold.
    plain = ["2", "3", "4", "5", "6", "8", "9", "0", "-", "=", "t", "i", "caps", "s", "d", "f", "k", "l", ";"]
    assert len(plain) > MAX_BUFFERED
    t = Harness()
    t.d("o")  # n/SFT (right hand); the first key, q, is on the other hand
    for name in plain:
        t.d(name)
    assert t.take().startswith("+SFT +q"), "a full buffer resolves the key as a hold"


# ---- caps word -------------------------------------------------------------
def test_caps_word_capitalises_until_space() -> None:
    t = Harness()
    both_shifts(t)
    t.tap("2")
    t.tap("3")  # q w
    assert t.take() == "+SFT +q -SFT -q +SFT +w -SFT -w"
    t.tap("c")
    t.tap("2")  # space ends it
    assert t.take() == "+spc -spc +q -q"


def test_caps_word_keeps_going_through_digits_and_underscore() -> None:
    t = Harness()
    both_shifts(t)
    t.d(".")
    t.tap("w")
    t.u(".")  # Num layer: 4
    t.d(",")
    t.tap("v")
    t.u(",")  # Sym layer: _
    t.tap("2")
    assert t.take() == "+4 -4 +SFT +0xbd -SFT -0xbd +SFT +q -SFT -q"


def test_caps_word_toggles_off_and_clears_on_switch_off() -> None:
    t = Harness()
    both_shifts(t)
    both_shifts(t)  # off again
    t.tap("2")
    assert t.take() == "+q -q"
    both_shifts(t)
    t.key(ESC, True, 0)
    t.key(ESC, False, 10)  # off
    t.key(ESC, True, 20)
    t.key(ESC, False, 30)  # on: caps word is gone
    t.tap("2")
    assert t.take() == "!OFF !ON +q -q"


def test_caps_word_signals_when_turned_on() -> None:
    t = Harness()
    t.d("r")
    t.d("o")
    t.timeout_last()
    t.timeout_last()
    assert "!CAPS_WORD" in t.take()


# ---- layer lock ------------------------------------------------------------
def test_layer_lock_keeps_nav_until_tapped_again() -> None:
    t = Harness()
    t.d("c")
    t.tap("2")
    t.u("c")  # hold Space thumb, tap lock, let go
    t.tap("o")  # still Nav: left
    assert t.take() == "+left -left"
    t.tap("2")  # lock key again: unlocked
    t.tap("o")  # base: n
    assert t.take() == "+n -n"


def test_layer_lock_on_num_and_cleared_by_escape() -> None:
    t = Harness()
    t.d(".")
    t.tap("=")
    t.u(".")  # hold Backspace thumb, tap lock (right pinky)
    t.tap("w")  # still Num: 4
    assert t.take() == "+4 -4"
    t.key(ESC, True, 0)
    t.key(ESC, False, 10)  # off
    t.key(ESC, True, 20)
    t.key(ESC, False, 30)  # on: lock is gone
    t.tap("w")
    assert t.take() == "!OFF !ON +r -r"


# ---- chordal hold ----------------------------------------------------------
def test_same_hand_roll_is_two_taps_at_once() -> None:
    t = Harness()
    t.d("e")
    t.d("4")  # s/CTL then f, same hand
    assert t.take() == "+s -s +f", "decided on the second key's press"
    t.u("4")
    t.u("e")
    assert t.take() == "-f"


def test_other_hand_key_still_gets_the_modifier() -> None:
    t = Harness()
    t.d("e")
    t.tap("8")
    t.u("e")  # Ctrl + j
    assert t.take() == "+CTL +j -j -CTL"


def test_same_hand_shortcut_works_after_holding() -> None:
    t = Harness()
    t.d("e")
    t.timeout_last()  # held past the tapping term
    t.tap("caps")
    t.u("e")  # Ctrl + z, one hand
    assert t.take() == "+CTL +z -z -CTL"


def test_layer_thumbs_are_not_affected() -> None:
    t = Harness()
    t.d("c")
    t.tap("e")
    t.u("c")  # Nav thumb (left) + left-hand Ctrl key
    assert not t.take().startswith("+spc"), "the Nav thumb was not turned into a tap"


# ---- quick tap -------------------------------------------------------------
def test_backspace_thumb_repeats_after_a_tap() -> None:
    t = Harness()
    t.at(0).tap(".")  # tap: backspace
    t.at(QUICK_TAP_MS // 2).d(".")  # again quickly and hold
    t.d(".")
    t.d(".")  # auto-repeat
    t.u(".")
    assert t.take() == "+bksp -bksp +bksp +bksp +bksp -bksp"


def test_after_a_pause_the_thumb_is_a_layer_again() -> None:
    t = Harness()
    t.at(0).tap(".")
    t.at(QUICK_TAP_MS * 2).d(".")
    t.tap("w")
    t.u(".")  # Num layer: 4
    assert t.take() == "+bksp -bksp +4 -4"


def test_home_row_letter_repeats_after_a_tap() -> None:
    t = Harness()
    t.at(1000).tap("r")  # t (Shift key)
    t.at(1000 + QUICK_TAP_MS // 2).d("r")
    t.d("r")
    assert t.take() == "+t -t +t +t", "held t repeats instead of becoming Shift"


# ---- modifier state -----------------------------------------------------------------


def test_held_shift_survives_a_shifted_symbol() -> None:
    t = Harness()
    t.d("r")
    t.timeout_last()  # t held: Shift
    t.d(",")
    t.tap("3")  # Sym layer: & (Shift+7); Shift is already down, so no Shift events
    t.u(",")
    t.tap("2")  # q, with Shift still held
    assert t.take() == "+SFT +7 -7 +q -q"


def test_releasing_one_of_two_ctrl_keys_keeps_ctrl() -> None:
    t = Harness()
    t.d("e")  # left Ctrl (s)
    t.d("p")  # right Ctrl (e), other hand
    t.timeout_last()
    t.timeout_last()  # both held: Ctrl goes down once
    t.u("p")  # one released: Ctrl stays down
    t.tap("8")  # Ctrl + j
    t.u("e")  # the last one released: Ctrl up
    assert t.take() == "+CTL +j -j -CTL"


# ---- chords ---------------------------------------------------------------------------


def test_held_undo_repeats_like_any_key() -> None:
    t = Harness()
    t.d("c")
    t.timeout_last()  # Nav layer held
    t.d("=")  # Undo: Ctrl + z, with z held
    t.d("=")  # auto-repeat: another Ctrl + z
    t.u("=")  # z up
    t.u("c")
    assert t.take() == "+CTL +z -CTL +CTL +z -CTL -z"


# ---- push-to-talk ----------------------------------------------------------------------


def test_talk_key_signals_start_and_stop() -> None:
    t = Harness()
    assert t.key(TALK, True, 0) == Verdict.SWALLOW
    assert t.key(TALK, True, 30) == Verdict.SWALLOW  # auto-repeat: still talking
    assert t.key(TALK, False, 900) == Verdict.SWALLOW
    assert t.take() == "!TALK_START !TALK_STOP"


def test_talk_key_is_a_plain_key_when_switched_off() -> None:
    t = Harness()
    t.key(ESC, True, 0)
    t.key(ESC, False, 10)
    t.take()
    assert t.key(TALK, True, 20) == Verdict.PASS
    assert t.key(TALK, False, 30) == Verdict.PASS
    assert t.take() == ""


def test_switching_off_while_talking_stops_talking() -> None:
    t = Harness()
    t.key(TALK, True, 0)
    t.key(ESC, True, 10)
    t.key(ESC, False, 20)
    assert t.key(TALK, False, 30) == Verdict.PASS  # off now: the release is a plain key
    assert t.take() == "!TALK_START !TALK_STOP !OFF"


def test_quitting_while_talking_stops_talking() -> None:
    t = Harness()
    t.key(TALK, True, 0)
    t.key(ESC, True, 10)
    assert t.key(ESC, False, 10 + QUIT_HOLD_MS) == Verdict.QUIT
    assert t.take() == "!TALK_START !TALK_STOP"


def test_talk_key_leaves_an_undecided_home_row_mod_alone() -> None:
    t = Harness()
    t.d("r")  # t or Shift: undecided
    t.key(TALK, True, 0)
    t.u("r")  # still a tap
    t.key(TALK, False, 0)
    assert t.take() == "!TALK_START +t -t !TALK_STOP"
