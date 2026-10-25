"""The `Desktop` the assistant's features use, done with Windows calls and Qt."""

import os
import subprocess
import time
from collections.abc import Callable
from typing import Final, Literal

from config import TERMINALS
from protocol import MAGIC, Tool
from winapi import audio, clipboard, inputs, screen, windows
from winapi.shapes import Rect, Window

from .geometry import Shot, fit, half, to_monitor
from .icons import State
from .png import encode_png
from .textio import paste_text
from .ui import AnswerWindow, Ui

_VK_CONTROL, _VK_C, _VK_V = 0xA2, 0x43, 0x56
_CLIPBOARD_WAIT_S: Final = 0.6
"""How long a program gets to put the selection on the clipboard after Ctrl+C."""
_PASTE_WAIT_S: Final = 0.4
"""How long a program gets to read the clipboard after Ctrl+V, before it's restored."""
_CREATE_NO_WINDOW: Final = 0x0800_0000


def _shortcut(vk: int) -> None:
    """Ctrl + a key, tagged so the layout lets it through."""
    keys = [inputs.KeyStroke(_VK_CONTROL, True), inputs.KeyStroke(vk, True), inputs.KeyStroke(vk, False), inputs.KeyStroke(_VK_CONTROL, False)]
    inputs.send_keys(keys, MAGIC)


class WinDesktop:
    __slots__ = ("mic", "on_notify", "on_problem", "on_state", "owner", "ui")

    def __init__(
        self,
        owner: int,
        mic: audio.Microphone,
        ui: Ui,
        *,
        on_state: Callable[[State], None],
        on_notify: Callable[[str, str], None],
        on_problem: Callable[[str], None],
    ) -> None:
        self.owner = owner
        """The assistant's window: it owns what we put on the clipboard."""
        self.mic = mic
        self.ui = ui
        self.on_state = on_state
        self.on_notify = on_notify
        self.on_problem = on_problem

    # ---- the microphone ----------------------------------------------------------------

    def start_listening(self) -> str | None:
        if error := self.mic.start():
            return f"Can't open the microphone (Windows error {error}). Is microphone access on for desktop apps?"
        return None

    def stop_listening(self) -> bytes:
        return self.mic.stop()

    # ---- text in other programs --------------------------------------------------------

    def in_terminal(self) -> bool:
        return windows.exe(windows.foreground()) in TERMINALS

    def copy_selection(self) -> str | None:
        # In a terminal, Ctrl+C with nothing selected stops the running program: never send it there.
        if self.in_terminal():
            return None
        saved = clipboard.get_text(self.owner)
        before = clipboard.sequence()
        _shortcut(_VK_C)
        deadline = time.monotonic() + _CLIPBOARD_WAIT_S
        while clipboard.sequence() == before and time.monotonic() < deadline:
            time.sleep(0.02)
        if clipboard.sequence() == before:
            return None  # nothing was selected
        text = clipboard.get_text(self.owner)
        if saved is not None:
            clipboard.set_text(self.owner, saved)
        return text

    def paste(self, text: str) -> None:
        paste_text(self.owner, text, clipboard, lambda: _shortcut(_VK_V), lambda: time.sleep(_PASTE_WAIT_S))

    def type_text(self, text: str) -> None:
        inputs.type_text(text, MAGIC)

    def set_clipboard(self, text: str) -> None:
        clipboard.set_text(self.owner, text)

    # ---- the screen --------------------------------------------------------------------

    def choose_region(self) -> Rect | None:
        return self.ui.choose_region(screen.virtual_screen())

    def capture(self, area: Rect, longest: int) -> tuple[bytes, Shot]:
        width, height = fit(area.width, area.height, longest)
        return encode_png(width, height, screen.capture(area, width, height)), Shot(area, width, height)

    # ---- the assistant's own windows ---------------------------------------------------

    def prompt(self, title: str) -> str | None:
        return self.ui.prompt(title)

    def answer(self, title: str, follow_up: Callable[[str], None]) -> AnswerWindow:
        return self.ui.answer(title, follow_up)

    def show_state(self, state: State) -> None:
        self.on_state(state)

    def notify(self, title: str, text: str) -> None:
        self.on_notify(title, text)

    def problem(self, message: str) -> None:
        self.on_problem(message)

    # ---- other programs' windows -------------------------------------------------------

    def windows(self) -> list[Window]:
        return windows.windows()

    def foreground(self) -> int:
        return windows.foreground()

    def focus(self, handle: int) -> bool:
        return windows.focus(handle)

    def launch(self, command: str) -> None:
        try:
            os.startfile(command)  # as the Run dialog would: program names, paths, URLs, msteams:...
        except OSError:
            subprocess.Popen(["cmd", "/c", "start", "", command], creationflags=_CREATE_NO_WINDOW)

    def arrange(self, tool: Tool) -> None:
        hwnd = windows.foreground()
        if not hwnd:
            return
        match tool:
            case Tool.SNAP_LEFT | Tool.SNAP_RIGHT:
                if windows.is_maximized(hwnd):
                    windows.show(hwnd, windows.SW_RESTORE)
                side: Literal["left", "right"] = "left" if tool is Tool.SNAP_LEFT else "right"
                windows.move(hwnd, half(screen.monitor_of(hwnd).work, side, windows.outer_rect(hwnd), windows.visible_rect(hwnd)))
            case Tool.MAXIMIZE:
                windows.show(hwnd, windows.SW_RESTORE if windows.is_maximized(hwnd) else windows.SW_MAXIMIZE)
            case Tool.MINIMIZE:
                windows.show(hwnd, windows.SW_MINIMIZE)
            case Tool.NEXT_MONITOR:
                self.next_monitor(hwnd)
            case _:
                raise AssertionError(f"{tool} doesn't arrange windows")

    def next_monitor(self, hwnd: int) -> None:
        monitors = screen.monitors()
        if len(monitors) < 2:
            return
        current = screen.monitor_of(hwnd)
        index = next((i for i, m in enumerate(monitors) if m.area == current.area), 0)
        target = monitors[(index + 1) % len(monitors)]
        maximized = windows.is_maximized(hwnd)
        if maximized:
            windows.show(hwnd, windows.SW_RESTORE)
        windows.move(hwnd, to_monitor(windows.outer_rect(hwnd), current.work, target.work))
        if maximized:
            windows.show(hwnd, windows.SW_MAXIMIZE)
