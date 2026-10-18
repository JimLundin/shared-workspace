"""The assistant program: a tray icon; a hidden window the layout posts its tool
and app keys to; a worker thread for slow jobs; the agent; and the layout itself,
which it starts, and restarts if it crashes (not if you quit it on purpose).
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from enum import Enum, auto
from pathlib import Path
from typing import Final

import keeper
import usage
from config import APPS
from miryoku.layout import Layer
from protocol import (
    ASSISTANT_WINDOW,
    MAGIC,
    WM_INDICATOR,
    WM_LAYOUT_STATE,
    WM_REQUEST,
    LayoutState,
    Tool,
    decode,
    decode_indicator,
)
from usage import DIRECTORY, Detail
from winapi import audio, clipboard, inputs, screen, shell, system, windows
from winapi.shapes import Rect, Window

from .agent import Agent
from .desktop import WinDesktop
from .features import Assistant
from .geometry import Shot
from .icons import State, dot
from .keymap import indicator_text
from .llm import MAX_SECONDS, RATE, Json, Missing, Settings
from .report import write_report
from .ui import AgentWindow, Ui
from .workspace import Workspace

ROOT: Final = Path(__file__).resolve().parent.parent
LOCK_NAME: Final = "Local\\toolkit-assistant"
LOG: Final = DIRECTORY / "assistant.log"
JOURNAL: Final = DIRECTORY / "agent.jsonl"
"""Everything the agent did: one JSON object per action."""
WM_TRAY: Final = system.WM_APP + 20
_CREATE_NO_WINDOW: Final = 0x0800_0000
_EXTENDED_VKS: Final = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B})


def log(text: str) -> None:
    """The assistant's own log: problems and crashes (there's no console under pythonw)."""
    with contextlib.suppress(OSError):
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as file:
            file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


def pythonw() -> str:
    """The windowless Python, so the layout doesn't open a console."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


# ---- the tray icon ---------------------------------------------------------------------


class Tray:
    """The notification-area icon. Safe to use from any thread."""

    __slots__ = ("_icon", "_icons", "_lock")

    def __init__(self, window: int) -> None:
        self._icons = {state: shell.Icon(32, dot(32, state.value)) for state in State}
        self._lock = threading.Lock()
        self._icon = shell.TrayIcon(window, WM_TRAY, self._icons[State.READY], "Toolkit")

    def show(self, state: State) -> None:
        with self._lock:
            self._icon.update(self._icons[state], f"Toolkit: {state.name.lower().replace('_', ' ')}")

    def notify(self, title: str, text: str, *, warning: bool = False) -> None:
        with self._lock:
            self._icon.notify(title, text, shell.NIIF_WARNING if warning else shell.NIIF_INFO)

    def remove(self) -> None:
        with self._lock:
            self._icon.remove()
            for icon in self._icons.values():
                icon.destroy()


# ---- the agent's machine ---------------------------------------------------------------


class WinMachine:
    """What the agent's tools act on, done with Windows calls."""

    __slots__ = ("desktop", "ui")

    def __init__(self, desktop: WinDesktop, ui: Ui) -> None:
        self.desktop = desktop
        self.ui = ui

    def run_powershell(self, command: str, timeout_s: float) -> tuple[int, str]:
        script = f"[Console]::OutputEncoding = [Text.Encoding]::UTF8; {command}"
        try:
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=timeout_s,
                creationflags=_CREATE_NO_WINDOW,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return -1, f"Timed out after {timeout_s:.0f} seconds."
        output = (done.stdout + done.stderr).decode("utf-8", errors="replace")
        return done.returncode, output

    def screenshot(self) -> tuple[bytes, Shot]:
        """The monitor the front window is on."""
        return self.desktop.capture(screen.monitor_of(windows.foreground()).area, 1280)

    def click(self, x: int, y: int, button: str, count: int) -> None:
        inputs.click(x, y, button, count, screen.virtual_screen(), MAGIC)

    def type_text(self, text: str) -> None:
        inputs.type_text(text, MAGIC)

    def press_keys(self, strokes: list[tuple[int, bool]]) -> None:
        inputs.send_keys([inputs.KeyStroke(vk, down, vk in _EXTENDED_VKS) for vk, down in strokes], MAGIC)

    def scroll(self, x: int, y: int, clicks: int) -> None:
        inputs.move_mouse(x, y, screen.virtual_screen(), MAGIC)
        inputs.scroll(clicks, MAGIC)

    def windows(self) -> list[Window]:
        return windows.windows()

    def focus(self, handle: int) -> bool:
        return windows.focus(handle)

    def get_clipboard(self) -> str | None:
        return clipboard.get_text(self.desktop.owner)

    def set_clipboard(self, text: str) -> None:
        clipboard.set_text(self.desktop.owner, text)

    def open(self, target: str) -> None:
        os.startfile(target)

    def ask_user(self, question: str) -> str | None:
        return self.ui.prompt(question)

    def confirm(self, title: str, detail: str) -> bool:
        return system.ask_yes_no(f"{title}\n\n{detail}", "Toolkit agent")

    def review(self, summary: str, diff: str) -> bool:
        return True  # the protected keeper reviews its frozen candidate before executing it

    def install(self, summary: str) -> None:
        keeper.ask("install", summary)


def write_journal(entry: Mapping[str, Json]) -> None:
    with contextlib.suppress(OSError):
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


class AgentRunner:
    """One agent task at a time, on a thread of its own, in a window of its own."""

    __slots__ = ("machine", "on_done", "on_problem", "settings", "stop_event", "thread", "ui")

    def __init__(self, settings: Settings, machine: WinMachine, ui: Ui, *, on_done: Callable[[], None], on_problem: Callable[[str], None]) -> None:
        self.settings = settings
        self.machine = machine
        self.ui = ui
        self.on_done = on_done
        self.on_problem = on_problem
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, task: str) -> None:
        if self.running():
            self.on_problem("The agent is already working on a task. Stop it first.")
            return
        self.stop_event = threading.Event()
        window = self.ui.agent(task, self.stop_event.set)
        self.thread = threading.Thread(target=self.run, args=(task, window, self.stop_event), name="agent", daemon=True)
        self.thread.start()

    def run(self, task: str, window: AgentWindow, stop: threading.Event) -> None:
        started = time.monotonic()
        finished = False
        try:
            finished = Agent(self.settings, self.machine, window, stop, journal=write_journal, workspace=Workspace()).run(task)
        except Exception:
            log(traceback.format_exc())
            window.problem("Something went wrong; it's in the assistant's log.")
        finally:
            with contextlib.suppress(OSError):
                usage.record("assistant", "agent", {"ok": finished, "seconds": round(time.monotonic() - started, 1)})
            self.on_done()

    def stop(self) -> None:
        self.stop_event.set()


# ---- the program -----------------------------------------------------------------------


class Exit(Enum):
    QUIT = auto()
    ALREADY_RUNNING = auto()


def indicator_area() -> Rect:
    """Where the layer indicator goes: the bottom of the main monitor."""
    monitors = screen.monitors()
    return next((m.work for m in monitors if m.primary), monitors[0].work)


class _Menu(Enum):
    KEYMAP = 9
    STATUS = 1
    RESTART_LAYOUT = 2
    ASK = 3
    AGENT = 4
    STOP_AGENT = 5
    USAGE = 6
    LOGS = 7
    QUIT = 8
    UNDO = 10


class AssistantApp:
    __slots__ = ("agent", "assistant", "desktop", "jobs", "layout", "mic_event", "tray", "ui", "window", "worker")

    def __init__(self, settings: Settings | Missing) -> None:
        self.ui = Ui()
        self.jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self.window = shell.AppWindow(ASSISTANT_WINDOW, self.on_message)
        self.tray = Tray(self.window.handle)
        self.mic_event = system.create_event()
        mic = audio.Microphone(RATE, MAX_SECONDS, self.mic_event)
        self.desktop = WinDesktop(self.window.handle, mic, self.ui, on_state=self.show, on_notify=self.tray.notify, on_problem=self.problem)
        missing = settings if isinstance(settings, Missing) else None
        usable = settings if isinstance(settings, Settings) else Settings("", "", "", "", "", "", "")
        self.agent = AgentRunner(usable, WinMachine(self.desktop, self.ui), self.ui, on_done=self.agent_done, on_problem=self.problem)
        self.assistant = Assistant(
            self.desktop,
            usable,
            record=self.record,
            submit=self.jobs.put,
            run_agent=self.agent.start,
            stop_agent=self.agent.stop,
            missing=missing,
        )
        self.layout: LayoutState | None = None
        """What the layout last said; None until it does."""
        self.worker = threading.Thread(target=self.work, name="worker", daemon=True)
        if missing is not None:
            self.tray.notify("Toolkit", f"The AI tools need {', '.join(missing.names)}; the layout works without them.", warning=True)

    # ---- running -----------------------------------------------------------------------

    def run(self) -> None:
        self.worker.start()
        keeper.ready()  # tells the keeper this version came up
        system.run_message_loop()

    def quit(self) -> None:
        self.agent.stop()
        self.jobs.put(None)
        self.tray.remove()
        self.window.destroy()
        system.close_event(self.mic_event)
        system.post_quit()

    def work(self) -> None:
        """The worker thread: slow jobs, one at a time."""
        while (job := self.jobs.get()) is not None:
            try:
                job()
            except Exception:
                log(traceback.format_exc())
                self.problem("Something went wrong; it's in the assistant's log.")

    def record(self, feature: str, details: Mapping[str, Detail]) -> None:
        with contextlib.suppress(OSError):
            usage.record("assistant", feature, details)

    # ---- what the user sees ------------------------------------------------------------

    def show(self, state: State) -> None:
        if state in (State.READY, State.LAYOUT_OFF) and self.agent.running():
            state = State.AGENT
        self.tray.show(state)

    def agent_done(self) -> None:
        if self.assistant.listening is None:
            self.assistant.idle()

    def problem(self, message: str) -> None:
        log(message)
        system.beep(system.MB_ICONEXCLAMATION)
        self.tray.notify("Toolkit", message, warning=True)

    # ---- messages ----------------------------------------------------------------------

    def on_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int | None:
        try:
            if message == WM_REQUEST:
                if (request := decode(wparam, APPS)) is not None:
                    self.assistant.handle(request, bool(lparam))
                return 0
            if message == WM_INDICATOR:
                index, locked, caps_word = decode_indicator(wparam)
                layer = next((layer for layer in Layer if layer.value == index), None)
                self.ui.indicate(indicator_text(layer, locked=locked, caps_word=caps_word), indicator_area())
                return 0
            if message == WM_LAYOUT_STATE:
                with contextlib.suppress(ValueError):
                    self.layout = LayoutState(wparam)
                if self.layout is not LayoutState.QUIT:
                    self.ui.flash("Layout on" if self.layout is LayoutState.ON else "Layout off", indicator_area())
                self.assistant.layout_on = self.layout is LayoutState.ON
                if self.assistant.listening is None:
                    self.assistant.idle()
                return 0
            if message == system.WM_CLOSE:  # the keeper asks: it's restarting or installing
                self.quit()
                return 0
            if message == WM_TRAY and lparam in (system.WM_LBUTTONUP, system.WM_RBUTTONUP):
                self.menu()
                return 0
        except Exception:
            log(traceback.format_exc())
        return None

    def menu(self) -> None:
        status = {
            None: "not running",
            LayoutState.ON: "on",
            LayoutState.OFF: "off (tap \u00a7 to switch on)",
            LayoutState.QUIT: "not running",
        }[self.layout]
        running = self.layout in (LayoutState.ON, LayoutState.OFF)
        items = [
            shell.MenuItem(_Menu.STATUS.value, f"Layout: {status}", enabled=False),
            shell.MenuItem(_Menu.RESTART_LAYOUT.value, "Restart layout" if running else "Start layout"),
            shell.MenuItem(_Menu.KEYMAP.value, "Key map"),
            shell.MenuItem(0),
            shell.MenuItem(_Menu.ASK.value, "Ask\u2026"),
            shell.MenuItem(_Menu.AGENT.value, "Agent task\u2026"),
            shell.MenuItem(_Menu.STOP_AGENT.value, "Stop agent", enabled=self.agent.running()),
            shell.MenuItem(0),
            shell.MenuItem(_Menu.UNDO.value, "Undo the last update"),
            shell.MenuItem(_Menu.USAGE.value, "Usage report"),
            shell.MenuItem(_Menu.LOGS.value, "Open logs folder"),
            shell.MenuItem(0),
            shell.MenuItem(_Menu.QUIT.value, "Quit toolkit"),
        ]
        match shell.show_menu(self.window.handle, items):
            case _Menu.RESTART_LAYOUT.value:
                keeper.ask("restart_layout")
            case _Menu.KEYMAP.value:
                self.ui.show_keymap()
            case _Menu.ASK.value:
                self.assistant.handle(Tool.ASK, True)
            case _Menu.AGENT.value:
                self.assistant.handle(Tool.AGENT_TYPED, True)
            case _Menu.STOP_AGENT.value:
                self.agent.stop()
            case _Menu.UNDO.value:
                keeper.ask("undo")
            case _Menu.USAGE.value:
                os.startfile(write_report())
            case _Menu.LOGS.value:
                DIRECTORY.mkdir(parents=True, exist_ok=True)
                os.startfile(DIRECTORY)
            case _Menu.QUIT.value:
                self.quit()
            case _:
                pass


def run(settings: Settings | Missing) -> Exit:
    """Blocks until quit from the tray."""
    screen.use_physical_pixels()  # before any window exists
    lock = system.create_lock(LOCK_NAME)
    if lock is None:
        return Exit.ALREADY_RUNNING
    try:
        AssistantApp(settings).run()
        return Exit.QUIT
    finally:
        system.release_lock(lock)


def show_error(message: str) -> None:
    system.message_box(message, "Toolkit", system.MB_ICONHAND)
