"""The assistant program: a tray icon; a hidden window the layout posts its tool
and app keys to; a worker thread for slow jobs; the agent; and the layout itself,
which it starts, and restarts if it crashes (not if you quit it on purpose).
"""

import contextlib
import importlib
import json
import os
import queue
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Mapping
from enum import Enum, auto
from pathlib import Path
from typing import Final

import keeper
import usage
from config import APPS
from miryoku.layout import Layer, Scancode
from protocol import (
    ASSISTANT_WINDOW,
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
    decode,
    decode_indicator,
)
from usage import DIRECTORY, Detail
from winapi import audio, clipboard, inputs, recording, screen, shell, system, windows
from winapi.shapes import Rect, Window

from . import commands, uia
from .activity import ActivityTracker, ContextSnapshot, Observation
from .agent import Agent
from .browser import Browser, Product
from .desktop import WinDesktop
from .features import Assistant
from .geometry import Shot, half
from .icons import State, dot
from .keymap import indicator_text
from .llm import MAX_SECONDS, RATE, Json, Missing, Settings
from .meetings import Meetings
from .overlay import OverlayState, load_enabled, save_enabled
from .recording_state import RecordingAction, RecordingState
from .report import write_report
from .teams import Teams
from .ui import AgentWindow, Ui
from .workspace import Workspace

ROOT: Final = Path(__file__).resolve().parent.parent
LOCK_NAME: Final = "Local\\toolkit-assistant"
LOG: Final = DIRECTORY / "assistant.log"
JOURNAL: Final = DIRECTORY / "agent.jsonl"
"""Everything the agent did: one JSON object per action."""
WM_TRAY: Final = system.WM_APP + 20
_EXTENDED_VKS: Final = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5D, 0x90})


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


def warm_agent() -> None:
    """Load the SDK after startup, off the UI and input threads, before the first voice request."""
    try:
        importlib.import_module("assistant.sdk")
    except Exception:
        log(traceback.format_exc())


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


def foreground_context() -> Observation:
    """Keep Toolkit's own windows from replacing the app a request refers to."""
    handle = windows.foreground()
    if not handle:
        return "unavailable"
    if windows.process_id(handle) == os.getpid():
        return "toolkit"
    return Window(handle, windows.title(handle), windows.exe(handle), windows.is_minimized(handle))


def rectangle_data(area: Rect) -> dict[str, Json]:
    return {"x": area.left, "y": area.top, "width": area.width, "height": area.height}


class WinMachine:
    """What the agent's tools act on, done with Windows calls."""

    __slots__ = ("background_browser", "desktop", "meetings", "teams_client", "ui")

    def __init__(self, desktop: WinDesktop, ui: Ui) -> None:
        self.desktop = desktop
        self.ui = ui
        self.background_browser = Browser(DIRECTORY / "browser-profile", source=Product.EDGE)
        self.teams_client = Teams(DIRECTORY / "teams")
        self.meetings: Meetings | None = None

    def meeting(self, operation: str, source: str = "both", title: str = "Meeting") -> str:
        if self.meetings is None:
            raise OSError("Meeting recording is unavailable.")
        if operation == "start":
            if source not in ("both", "microphone"):
                raise OSError("Meeting source must be both or microphone.")
            self.meetings.start("both" if source == "both" else "microphone", title)
        elif operation == "stop":
            self.meetings.stop()
        elif operation == "retry":
            self.meetings.retry()
        elif operation == "list":
            return self.meetings.recent()
        elif operation != "status":
            raise OSError("Unknown meeting action.")
        return self.meetings.describe()

    def browse(self, operation: str, arguments: dict[str, Json], cancel: threading.Event) -> tuple[str, bytes | None]:
        result = self.background_browser.request(operation, arguments, cancel)
        return result.text, result.image

    def teams(self, operation: str, arguments: dict[str, Json], cancel: threading.Event) -> str:
        return self.teams_client.request(operation, arguments, cancel)

    def close_background(self) -> None:
        try:
            self.background_browser.close()
        finally:
            self.teams_client.cancel_sign_in()

    def run_powershell(
        self, command: str, timeout_s: float, *, cwd: str | None = None, cancel: threading.Event | None = None,
    ) -> tuple[int, str]:
        return commands.powershell(command, cwd=cwd, timeout_s=timeout_s, cancel=cancel)

    def screenshot(self, *, area: Rect | None = None, handle: int = 0, monitor: int = -1, longest: int = 1280) -> tuple[bytes, Shot]:
        if not windows.foreground():
            raise OSError("The interactive desktop is unavailable or locked.")
        if area is None:
            if handle:
                if not windows.exists(handle) or windows.is_minimized(handle):
                    raise OSError("The requested window is closed or minimized; inspect or restore it first.")
                area = windows.visible_rect(handle)
            elif monitor == -2:
                area = screen.virtual_screen()
            elif monitor >= 0:
                monitors = screen.monitors()
                if monitor >= len(monitors):
                    raise OSError("That monitor is unavailable; call desktop_state for current monitor indexes.")
                area = monitors[monitor].area
            else:
                area = screen.monitor_of(windows.foreground()).area
        desktop = screen.virtual_screen()
        area = Rect(max(area.left, desktop.left), max(area.top, desktop.top), min(area.right, desktop.right), min(area.bottom, desktop.bottom))
        if area.width <= 0 or area.height <= 0:
            raise OSError("The requested area is outside the desktop.")
        return self.desktop.capture(area, longest)

    def desktop_state(self) -> str:
        x, y = inputs.cursor_position()
        data: dict[str, Json] = {
            "coordinates": "physical screen pixels", "foreground": windows.foreground(), "cursor": {"x": x, "y": y},
            "monitors": [{"index": index, "bounds": rectangle_data(monitor.area), "work": rectangle_data(monitor.work),
                          "primary": monitor.primary} for index, monitor in enumerate(screen.monitors())],
            "windows": [{"handle": window.handle, "title": window.title, "app": window.exe, "minimized": window.minimized,
                         "bounds": rectangle_data(windows.outer_rect(window.handle))} for window in windows.windows()],
        }
        return json.dumps(data, ensure_ascii=False)

    def window_action(self, handle: int, action: str, bounds: Rect | None = None) -> str:
        if not windows.exists(handle):
            raise OSError("The window is no longer available; list windows again.")
        if action == "close":
            if not system.post_message(handle, system.WM_CLOSE, 0, 0):
                raise OSError("Windows rejected the close request.")
            return "Close requested. Inspect the window for any unsaved-work dialog."
        if action in ("minimize", "maximize", "restore"):
            windows.show(handle, {"minimize": windows.SW_MINIMIZE, "maximize": windows.SW_MAXIMIZE, "restore": windows.SW_RESTORE}[action])
        else:
            if windows.is_maximized(handle) or windows.is_minimized(handle):
                windows.show(handle, windows.SW_RESTORE)
            if action in ("snap_left", "snap_right"):
                bounds = half(screen.monitor_of(handle).work, "left" if action == "snap_left" else "right",
                              windows.outer_rect(handle), windows.visible_rect(handle))
            if bounds is None:
                raise OSError("Moving a window requires bounds.")
            windows.move(handle, bounds)
        state: dict[str, Json] = {"handle": handle, "bounds": rectangle_data(windows.outer_rect(handle)),
                                  "minimized": windows.is_minimized(handle), "maximized": windows.is_maximized(handle)}
        return json.dumps(state)

    def inspect_ui(self, request: dict[str, Json], cancel: threading.Event) -> tuple[int, str]:
        handle = request.get("handle") or windows.foreground()
        if not isinstance(handle, int) or not windows.exists(handle):
            return 1, "No accessible window is available; list windows again or unlock the desktop."
        return commands.powershell(uia.script({**request, "handle": handle}), timeout_s=20, cancel=cancel)

    def move_mouse(self, x: int, y: int) -> None:
        inputs.move_mouse(x, y, screen.virtual_screen(), MAGIC)

    def mouse_button(self, button: str, down: bool) -> None:
        inputs.mouse_button(button, down, MAGIC)

    def click(self, x: int, y: int, button: str, count: int) -> None:
        inputs.click(x, y, button, count, screen.virtual_screen(), MAGIC)

    def type_text(self, text: str) -> None:
        inputs.type_text(text, MAGIC)

    def paste_text(self, text: str) -> None:
        self.desktop.paste(text)

    def press_keys(self, strokes: list[tuple[int, bool]]) -> None:
        inputs.send_keys([inputs.KeyStroke(vk, down, vk in _EXTENDED_VKS) for vk, down in strokes], MAGIC)

    def scroll(self, x: int, y: int, clicks: int, *, horizontal: bool = False) -> None:
        inputs.move_mouse(x, y, screen.virtual_screen(), MAGIC)
        inputs.scroll(clicks, MAGIC, horizontal=horizontal)

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

    def ask_user(self, question: str, *, cancel: threading.Event | None = None) -> str | None:
        return self.ui.agent_input(question, question=True, cancel=cancel)

    def confirm(self, title: str, detail: str, *, cancel: threading.Event | None = None) -> bool:
        return self.ui.agent_input(title, detail, cancel=cancel) == "approve"

    def review(self, summary: str, diff: str) -> bool:
        return True  # the keeper owns the optional review of its frozen candidate

    def install(self, summary: str) -> None:
        keeper.ask("install", summary)


def write_journal(entry: Mapping[str, Json]) -> None:
    with contextlib.suppress(OSError):
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


class AgentRunner:
    """One conversation; requests, including queued follow-ups, run one at a time."""

    __slots__ = (
        "context", "finished", "history", "lock", "machine", "on_done", "on_problem", "on_start", "pending", "settings", "stop_event", "thread",
        "ui", "window", "workspace",
    )

    def __init__(
        self, settings: Settings, machine: WinMachine, ui: Ui, *, on_done: Callable[[], None], on_problem: Callable[[str], None],
        on_start: Callable[[], None] | None = None,
        context: Callable[[], ContextSnapshot] | None = None,
    ) -> None:
        self.settings = settings
        self.machine = machine
        self.ui = ui
        self.on_done = on_done
        self.on_problem = on_problem
        self.on_start = on_start
        self.context = context
        self.stop_event = threading.Event()
        self.finished = threading.Event()
        self.finished.set()
        self.thread: threading.Thread | None = None
        self.history: list[Json] = []
        self.workspace = Workspace()
        self.lock = threading.Lock()
        self.pending: deque[tuple[str, ContextSnapshot | None]] = deque()
        self.window: AgentWindow | None = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self.finished.is_set()

    def show(self) -> None:
        """Open conversation history without starting a request or resetting context."""
        self._panel(None, focus=True)

    def voice(self, text: str) -> None:
        if self.window is None or not self.window.reply_by_voice(text):
            self.start(text)

    def prepare(self) -> None:
        self._panel(None, reveal=False)

    def _panel(self, task: str | None, *, focus: bool = False, queued: bool = False, reveal: bool = True) -> AgentWindow:
        window = self.ui.agent(
            task, self.stop, on_problem=self.on_problem, on_reset=self.new_conversation, on_submit=self.start,
            focus=focus, reveal=reveal and not queued, queued=queued,
        )
        with self.lock:
            if self.window is None:
                # A second request can arrive while the very first window is
                # being built. Show those pending requests once it is available.
                for pending, _context in self.pending:
                    window.queued(pending)
            self.window = window
        return window

    def start(self, task: str) -> None:
        if not (task := task.strip()):
            return
        context = self.context() if self.context is not None else None
        with self.lock:
            if not self.finished.is_set():
                self.pending.append((task, context))
                if self.window is not None:
                    self.window.queued(task)
                return
            self.finished.clear()
            self.stop_event = threading.Event()
        try:
            window = self._panel(task)
        except Exception:
            with self.lock:
                self.pending.clear()
                self.finished.set()
            raise
        self.thread = threading.Thread(target=self.run, args=(task, window, self.stop_event, context), name="agent", daemon=True)
        if self.on_start is not None:
            self.on_start()
        self.thread.start()

    def run(self, task: str, window: AgentWindow, stop: threading.Event, context: ContextSnapshot | None = None) -> None:
        try:
            while True:
                self.run_task(task, window, stop, context)
                with self.lock:
                    if not self.pending:
                        self.finished.set()
                        window.finish()
                        break
                    task, context = self.pending.popleft()
                    stop = self.stop_event = threading.Event()
                window = self._panel(task, queued=True)
        except Exception:
            log(traceback.format_exc())
            with self.lock:
                self.pending.clear()
                self.finished.set()
                window.problem("Couldn't continue the agent conversation; see the assistant's log.")
                window.finish()
        self.on_done()

    def run_task(self, task: str, window: AgentWindow, stop: threading.Event, context: ContextSnapshot | None = None) -> None:
        started = time.monotonic()
        finished = False
        agent = Agent(self.settings, self.machine, window, stop, journal=write_journal, workspace=self.workspace, history=self.history,
                      context=context, context_reader=self.context)
        try:
            finished = agent.run(task)
        except Exception:
            log(traceback.format_exc())
            window.problem("Something went wrong; it's in the assistant's log.")
        finally:
            if stop.is_set():
                self.machine.close_background()
            with contextlib.suppress(OSError):
                usage.record("assistant", "agent", {"ok": finished, "seconds": round(time.monotonic() - started, 1), **agent.timings})

    def stop(self) -> None:
        with self.lock:
            self.pending.clear()
            self.stop_event.set()
            if self.window is not None:
                self.window.stopping()

    def new_conversation(self) -> bool:
        """Reset only when idle; closing or stopping the window keeps the conversation."""
        with self.lock:
            if not self.finished.is_set():
                return False
            self.history.clear()
            self.workspace = Workspace()
            if self.window is not None:
                self.window.reset()
            return True


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
    TOGGLE_LAYOUT = 1
    RESTART_LAYOUT = 2
    ASK = 3
    AGENT = 4
    STOP_AGENT = 5
    USAGE = 6
    LOGS = 7
    QUIT = 8
    UNDO = 10
    OVERLAY = 11
    MEETING = 12
    TRANSCRIPTS = 13


class AssistantApp:
    __slots__ = (
        "_recording_phase", "activity", "agent", "assistant", "desktop", "jobs", "layout", "meetings", "mic_event", "overlay",
        "tray", "ui", "window", "worker",
    )

    def __init__(self, settings: Settings | Missing) -> None:
        self._recording_phase = "idle"
        self.ui = Ui(on_agent_waiting=lambda waiting: self.show(State.NEEDS_INPUT if waiting else State.AGENT), on_recording=self.recording_action)
        self.overlay = OverlayState(enabled=load_enabled())
        self.jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self.window = shell.AppWindow(ASSISTANT_WINDOW, self.on_message)
        self.tray = Tray(self.window.handle)
        self.mic_event = system.create_event()
        mic = audio.Microphone(RATE, MAX_SECONDS, self.mic_event)
        self.desktop = WinDesktop(self.window.handle, mic, self.ui, on_state=self.show, on_notify=self.tray.notify, on_problem=self.problem)
        missing = settings if isinstance(settings, Missing) else None
        usable = settings if isinstance(settings, Settings) else Settings("", "", "", "", "", "", "")
        self.agent = AgentRunner(
            usable, WinMachine(self.desktop, self.ui), self.ui, on_done=self.agent_done, on_problem=self.problem,
            on_start=lambda: self.show(State.AGENT),
        )
        self.meetings = Meetings(
            usable, lambda source: recording.MeetingAudio(computer_audio=source == "both"), on_update=self.recording_changed,
        )
        self.agent.machine.meetings = self.meetings
        self.activity = ActivityTracker(
            foreground_context, self.agent.running, enabled=os.environ.get("TOOLKIT_AGENT_CONTEXT", "recent").lower() != "off",
        )
        self.agent.context = self.activity.snapshot
        self.assistant = Assistant(
            self.desktop,
            usable,
            record=self.record,
            submit=self.jobs.put,
            run_agent=self.agent.voice,
            show_agent=self.agent.show,
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
        self.activity.start()
        self.worker.start()
        self.meetings.start_worker()
        if (layout_window := windows.find(LAYOUT_WINDOW)) is not None:
            system.post_message(layout_window, WM_QUERY_LAYOUT_STATE, 0, 0)
        keeper.ready()  # tells the keeper this version came up
        if self.agent.settings.api_key:
            threading.Thread(target=warm_agent, name="agent warmup", daemon=True).start()
            threading.Thread(target=self.agent.prepare, name="agent panel warmup", daemon=True).start()
        system.run_message_loop()

    def quit(self) -> None:
        try:
            self.meetings.close()
        except OSError as error:
            log(str(error))
        self.agent.stop()
        self.agent.machine.close_background()
        self.activity.close()
        self.ui.close_agent()
        self.jobs.put(None)
        self.desktop.mic.close()
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
        if self.meetings.snapshot().capturing:
            state = State.RECORDING
        elif state in (State.READY, State.LAYOUT_OFF) and self.agent.running():
            state = State.NEEDS_INPUT if self.agent.window is not None and self.agent.window.waiting else State.AGENT
        self.tray.show(state)

    def recording_changed(self, state: RecordingState) -> None:
        self.agent.prepare()
        if self.agent.window is not None:
            self.agent.window.recording(state)
        prior, self._recording_phase = self._recording_phase, state.phase
        failed_capture = prior in ("starting", "recording", "stopping") and state.phase == "finishing" and bool(state.detail)
        if (prior != state.phase and state.phase in ("paused", "interrupted")) or failed_capture:
            self.tray.notify("Meeting transcript", state.detail or "Recording was interrupted. Open Transcripts for the saved part.", warning=True)
        self.show(State.READY if self.assistant.layout_on else State.LAYOUT_OFF)

    def recording_action(self, action: RecordingAction) -> None:
        try:
            match action:
                case "start" | "start_microphone":
                    self.meetings.start("both" if action == "start" else "microphone")
                    self.agent.prepare()
                    if self.agent.window is not None:
                        self.agent.window.show()
                case "stop":
                    self.meetings.stop()
                case "retry":
                    self.meetings.retry()
                case "open" | "folder":
                    self.meetings.root.mkdir(parents=True, exist_ok=True)
                    transcript = self.meetings.snapshot().path
                    path = Path(transcript) if action == "open" and transcript else self.meetings.root
                    os.startfile(path if path.exists() else self.meetings.root)
        except (OSError, ValueError) as error:
            self.problem(str(error))

    def agent_done(self) -> None:
        if self.assistant.listening is None:
            self.assistant.idle()

    def problem(self, message: str) -> None:
        log(message)
        system.beep(system.MB_ICONEXCLAMATION)
        self.tray.notify("Toolkit", message, warning=True)

    def show_overlay(self, work: Rect) -> None:
        self.ui.show_keyboard_overlay(self.overlay.layer, self.overlay.title, work, visible=self.overlay.visible, pressed=self.overlay.pressed)

    def toggle_overlay(self) -> None:
        self.overlay.enabled = not self.overlay.enabled
        self.show_overlay(indicator_area())
        try:
            save_enabled(self.overlay.enabled)
        except OSError:
            self.problem("The overlay changed, but its setting couldn't be saved.")

    # ---- messages ----------------------------------------------------------------------

    def on_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int | None:
        try:
            if message == WM_KEY_STATE:
                self.overlay.key(Scancode(wparam), bool(lparam))
                self.ui.show_pressed_keys(self.overlay.pressed, reset=wparam == 0)
                return 0
            if message == WM_REQUEST:
                if (request := decode(wparam, APPS)) is not None:
                    self.assistant.handle(request, bool(lparam))
                return 0
            if message == WM_INDICATOR:
                index, locked, caps_word = decode_indicator(wparam)
                layer = next((layer for layer in Layer if layer.value == index), None)
                work = indicator_area()
                self.ui.indicate(indicator_text(layer, locked=locked, caps_word=caps_word), work)
                self.overlay.indicate(layer, locked=locked, caps_word=caps_word)
                self.show_overlay(work)
                return 0
            if message == WM_LAYOUT_STATE:
                with contextlib.suppress(ValueError):
                    self.layout = LayoutState(wparam)
                work = indicator_area()
                if self.layout is not LayoutState.QUIT:
                    self.ui.flash("Layout on" if self.layout is LayoutState.ON else "Layout off", work)
                self.assistant.layout_on = self.layout is LayoutState.ON
                self.overlay.set_layout(self.assistant.layout_on)
                self.ui.show_pressed_keys(self.overlay.pressed, reset=True)
                self.show_overlay(work)
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
            LayoutState.OFF: "off",
            LayoutState.QUIT: "not running",
        }[self.layout]
        running = self.layout in (LayoutState.ON, LayoutState.OFF)
        items = [
            shell.MenuItem(
                _Menu.TOGGLE_LAYOUT.value,
                f"Layout: {status}" + ("\tTap \u00a7" if running else ""),
                enabled=running,
                checked=self.layout is LayoutState.ON,
            ),
            shell.MenuItem(_Menu.RESTART_LAYOUT.value, "Restart layout" if running else "Start layout"),
            shell.MenuItem(_Menu.OVERLAY.value, "Keyboard overlay", checked=self.overlay.enabled),
            shell.MenuItem(_Menu.KEYMAP.value, "Key map"),
            shell.MenuItem(0),
            shell.MenuItem(_Menu.ASK.value, "Ask\u2026\tHold V + P"),
            shell.MenuItem(_Menu.AGENT.value, "Agent session\tHold V + L"),
            shell.MenuItem(_Menu.STOP_AGENT.value, "Stop agent\tHold V + \u00d6", enabled=self.agent.running()),
            shell.MenuItem(_Menu.MEETING.value, "Stop recording" if self.meetings.snapshot().capturing else "Record a meeting"),
            shell.MenuItem(_Menu.TRANSCRIPTS.value, "Transcripts"),
            shell.MenuItem(0),
            shell.MenuItem(_Menu.UNDO.value, "Undo the last update"),
            shell.MenuItem(_Menu.USAGE.value, "Usage report"),
            shell.MenuItem(_Menu.LOGS.value, "Open logs folder"),
            shell.MenuItem(0),
            shell.MenuItem(_Menu.QUIT.value, "Quit toolkit"),
        ]
        match shell.show_menu(self.window.handle, items):
            case _Menu.TOGGLE_LAYOUT.value:
                layout_window = windows.find(LAYOUT_WINDOW)
                if layout_window is None or not system.post_message(layout_window, WM_TOGGLE_LAYOUT, 0, 0):
                    self.problem("Couldn't reach the layout. Try Start layout or Restart layout.")
            case _Menu.RESTART_LAYOUT.value:
                keeper.ask("restart_layout")
            case _Menu.OVERLAY.value:
                self.toggle_overlay()
            case _Menu.KEYMAP.value:
                self.ui.show_keymap()
            case _Menu.ASK.value:
                self.assistant.handle(Tool.ASK, True)
            case _Menu.AGENT.value:
                self.assistant.handle(Tool.AGENT_TYPED, True)
            case _Menu.STOP_AGENT.value:
                self.agent.stop()
            case _Menu.MEETING.value:
                self.recording_action("stop" if self.meetings.snapshot().capturing else "start")
            case _Menu.TRANSCRIPTS.value:
                self.recording_action("folder")
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
