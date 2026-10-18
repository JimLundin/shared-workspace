"""End to end on Windows: the real assistant (which starts the real layout), real
key presses and mouse drags, real windows; a fake AI endpoint plays the model.

It takes over the keyboard and mouse for half a minute, so it only runs when
asked: `TOOLKIT_E2E=1 pytest tests/test_windows_assistant_e2e.py`. The microphone
isn't tested (test machines rarely have one).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from assistant.llm import Json

from .fake_endpoint import FakeEndpoint, Reply, fake_endpoint, sse

if sys.platform != "win32" or os.environ.get("TOOLKIT_E2E") != "1":
    raise unittest.SkipTest("end-to-end tests run on Windows, with TOOLKIT_E2E=1")

import tkinter as tk

from protocol import LAYOUT_WINDOW, MAGIC, WM_QUIT_LAYOUT
from winapi import clipboard, inputs, screen, system, windows
from winapi.shapes import Rect

ROOT = Path(__file__).resolve().parent.parent
TAB, DELETE, SPACE = 0x2F, 0x35, 0x2E  # the Tools, Apps and Nav thumbs (physical V, - and C)
FIX, ASK, SCREEN_TEXT, AGENT_TYPED, SNAP_LEFT = 0x0A, 0x19, 0x1A, 0x26, 0x3A  # physical 9, P, \u00c5, L, Caps Lock


def dumps(value: Json) -> bytes:
    return json.dumps(value).encode()


def answered(text: str, calls: list[Json] | None = None) -> Reply:
    message: dict[str, Json] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = calls
    return Reply(dumps({"choices": [{"message": message}]}))


def wait_for(condition: Callable[[], bool], pump: Callable[[], None], seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pump()
        if condition():
            return True
        time.sleep(0.05)
    return False


def lit(area: Rect) -> bool:
    """Something is drawn there (the test screen is otherwise black)."""
    return any(screen.capture(area, area.width, area.height))


def chord(thumb: int, key: int) -> None:
    """Hold a layer thumb past the tapping term, tap a key on that layer."""
    inputs.send_scancodes([(thumb, True)], extra=0)
    time.sleep(0.3)
    inputs.send_scancodes([(key, True), (key, False)], extra=0)
    time.sleep(0.05)
    inputs.send_scancodes([(thumb, False)], extra=0)


def type_ascii(text: str) -> None:
    """Plain letters and spaces as virtual keys, tagged so the layout passes them through."""
    for c in text:
        vk = 0x20 if c == " " else ord(c.upper())
        inputs.send_keys([inputs.KeyStroke(vk, True), inputs.KeyStroke(vk, False)], MAGIC)
    inputs.send_keys([inputs.KeyStroke(0x0D, True), inputs.KeyStroke(0x0D, False)], MAGIC)


@contextmanager
def toolkit(endpoint: FakeEndpoint, home: Path) -> Generator[None, None, None]:
    env = {**os.environ, "OPENAI_BASE_URL": endpoint.url, "OPENAI_API_KEY": "k", "TOOLKIT_CHAT_MODEL": "chat", "LOCALAPPDATA": str(home)}
    process = subprocess.Popen([sys.executable, "-m", "assistant"], cwd=ROOT, env=env)
    try:
        assert wait_for(lambda: windows.find(LAYOUT_WINDOW) is not None, lambda: None, 20), "the layout didn't start"
        time.sleep(1)
        yield
    finally:
        if (hwnd := windows.find(LAYOUT_WINDOW)) is not None:
            system.post_message(hwnd, WM_QUIT_LAYOUT, 0, 0)
        wait_for(lambda: windows.find(LAYOUT_WINDOW) is None, lambda: None, 5)
        process.terminate()
        process.wait()


@contextmanager
def text_box(text: str) -> Generator[tuple[tk.Tk, tk.Entry], None, None]:
    root = tk.Tk()
    root.geometry("500x80+300+300")
    entry = tk.Entry(root, width=40)
    entry.pack()
    entry.insert(0, text)
    root.update()
    windows.focus(int(root.wm_frame(), 16))
    entry.focus_set()
    root.update()
    try:
        yield root, entry
    finally:
        root.destroy()


def logs(home: Path) -> str:
    folder = home / "toolkit"
    return "\n".join(f"--- {p.name}\n{p.read_text(encoding='utf-8', errors='replace')[-3000:]}" for p in sorted(folder.glob("*")) if p.is_file())


def test_the_toolkit_end_to_end() -> None:
    home = Path(tempfile.mkdtemp())
    try:
        run_everything(home)
    except AssertionError:
        print(logs(home))
        raise


def run_everything(home: Path) -> None:
    keeper = tk.Tk()  # this process's own windows keep answering, like any real program
    keeper.withdraw()
    with fake_endpoint() as endpoint, toolkit(endpoint, home):
        in_a_text_box(endpoint)
        screen_text(endpoint, keeper.update)
        ask(endpoint, keeper.update)
        agent(endpoint, home, keeper.update)
    keeper.destroy()
    used = (home / "toolkit" / "usage.jsonl").read_text(encoding="utf-8")
    for feature in ('"fix"', '"snap_left"', '"screen_text"', '"ask"', '"agent_typed"'):
        assert feature in used, feature


def in_a_text_box(endpoint: FakeEndpoint) -> None:
    with text_box("helo wrld") as (root, entry):
        # Rewrite: the selection is copied, sent, and replaced by the answer.
        endpoint.replies = [answered("Hello, world.")]
        entry.select_range(0, "end")
        root.update()
        chord(TAB, FIX)
        assert wait_for(lambda: entry.get() == "Hello, world.", root.update), entry.get()
        assert b"helo wrld" in endpoint.last.body

        # The layer indicator: shown while Nav is held, without taking the keyboard.
        frame = int(root.wm_frame(), 16)
        work = next(m.work for m in screen.monitors() if m.primary)
        middle = work.left + work.width // 2
        bottom = Rect(middle - 40, work.bottom - 60, middle + 40, work.bottom - 30)
        inputs.send_scancodes([(SPACE, True)], extra=0)
        assert wait_for(lambda: lit(bottom), root.update), "no indicator"
        assert windows.foreground() == frame, "the indicator took the focus"
        inputs.send_scancodes([(SPACE, False)], extra=0)
        assert wait_for(lambda: not lit(bottom), root.update), "the indicator stayed"

        # Window keys: snap the window to the left half of the screen.
        chord(DELETE, SNAP_LEFT)
        work = screen.monitor_of(frame).work
        assert wait_for(lambda: abs(root.winfo_x() - work.left) < 20 and root.winfo_width() > work.width // 2 - 40, root.update), root.geometry()


def screen_text(endpoint: FakeEndpoint, pump: Callable[[], None]) -> None:
    """Drag over an area; its text ends up on the clipboard."""
    endpoint.replies = [answered("Text on screen")]
    chord(TAB, SCREEN_TEXT)
    wait_for(lambda: False, pump, 1.5)
    area = screen.virtual_screen()
    inputs.move_mouse(100, 100, area, MAGIC)
    inputs.mouse_button("left", True, MAGIC)
    for step in range(1, 11):
        inputs.move_mouse(100 + step * 30, 100 + step * 20, area, MAGIC)
        time.sleep(0.03)
    inputs.mouse_button("left", False, MAGIC)
    hwnd = windows.create_hidden("e2e clipboard reader")
    assert wait_for(lambda: clipboard.get_text(hwnd) == "Text on screen", pump), clipboard.get_text(hwnd)
    assert b"data:image/png;base64," in endpoint.last.body


def ask(endpoint: FakeEndpoint, pump: Callable[[], None]) -> None:
    """A box for the question, then an answer window, streamed."""
    endpoint.replies = [sse('{"choices": [{"delta": {"content": "Yes"}}]}')]
    chord(TAB, ASK)
    wait_for(lambda: False, pump, 1.5)
    type_ascii("is it friday")
    assert wait_for(lambda: any(w.title == "is it friday" for w in windows.windows()), pump), [w.title for w in windows.windows()]
    assert wait_for(lambda: b"is it friday" in endpoint.last.body, pump)


def agent(endpoint: FakeEndpoint, home: Path, pump: Callable[[], None]) -> None:
    """A typed task, a tool call, a final answer, all in the journal."""
    call: Json = {"id": "c1", "type": "function", "function": {"name": "list_directory", "arguments": dumps({"path": "C:\\"}).decode()}}
    endpoint.replies = [answered("Looking.", [call]), answered("C: has folders.")]
    chord(TAB, AGENT_TYPED)
    wait_for(lambda: False, pump, 1.5)
    type_ascii("look at c")
    journal = home / "toolkit" / "agent.jsonl"
    assert wait_for(lambda: journal.exists() and "list_directory" in journal.read_text(encoding="utf-8"), pump), "no journal"
    assert any(w.title.startswith("Agent: look at c") for w in windows.windows())
