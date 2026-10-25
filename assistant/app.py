"""The assistant owns the tray, conversation views, microphone, and meetings.

Durable work executes in the supervised automation process. The keyboard sends
its tool requests here without depending on either work storage or model calls.
"""

import contextlib
import os
import queue
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from enum import Enum, auto
from pathlib import Path
from typing import Final, assert_never

import keeper
import usage
from automation.bridge import BridgeClient
from automation.context import ContextStore
from automation.ipc import MeetingCommand, MeetingFailed, MeetingSucceeded, StartMeeting
from automation.routines import RoutineStore
from automation.store import TaskStore
from config import APPS
from miryoku.layout import Layer, Scancode
from protocol import (
    ASSISTANT_WINDOW,
    LAYOUT_WINDOW,
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
from winapi import audio, recording, screen, shell, system, windows
from winapi.shapes import Rect, Window

from .activity import ActivityTracker, ContextSnapshot, Observation
from .canvas_requests import CanvasRequests
from .desktop import WinDesktop
from .features import Assistant
from .icons import State, dot
from .keymap import indicator_text
from .llm import MAX_SECONDS, RATE, Missing, Settings
from .machine import WinMachine as WinMachine  # noqa: PLC0414 (compatibility re-export)
from .meeting_events import TranscriptEvents
from .meeting_followup_store import FollowupStore
from .meeting_requests import MeetingRequests
from .meeting_store import MeetingArchive
from .meeting_workflow import MeetingWorkflow
from .meetings import Meetings
from .overlay import OverlayState, load_enabled, save_enabled
from .recording_state import RecordingAction, RecordingState
from .report import write_report
from .ui import Ui
from .work_agent import WorkAgent
from .work_window import WorkSnapshot

ROOT: Final = Path(__file__).resolve().parent.parent
LOCK_NAME: Final = "Local\\toolkit-assistant"
LOG: Final = DIRECTORY / "assistant.log"
WM_TRAY: Final = system.WM_APP + 20


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


# ---- the request context ---------------------------------------------------------------


def foreground_context() -> Observation:
    """Keep Toolkit's own windows from replacing the app a request refers to."""
    handle = windows.foreground()
    if not handle:
        return "unavailable"
    if windows.process_id(handle) == os.getpid():
        return "toolkit"
    return Window(handle, windows.title(handle), windows.exe(handle), windows.is_minimized(handle))


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
    WORK = 14
    CANVAS = 15
    LAUNCHER = 16
    FOLLOWUPS = 17


class AssistantApp:
    __slots__ = (
        "_recording_phase", "activity", "agent", "assistant", "desktop", "followups", "jobs", "layout", "meetings", "mic_event", "overlay",
        "settings", "tray", "ui", "window", "worker",
    )

    def __init__(self, settings: Settings | Missing, ui: Ui) -> None:
        self._recording_phase = "idle"
        self.ui = ui
        self.ui.configure_controls(
            on_agent_waiting=lambda waiting: self.show(State.NEEDS_INPUT if waiting else State.AGENT), on_recording=self.recording_action,
        )
        self.overlay = OverlayState(enabled=load_enabled())
        self.jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self.window = shell.AppWindow(ASSISTANT_WINDOW, self.on_message)
        self.tray = Tray(self.window.handle)
        self.mic_event = system.create_event()
        mic = audio.Microphone(RATE, MAX_SECONDS, self.mic_event)
        self.desktop = WinDesktop(self.window.handle, mic, self.ui, on_state=self.show, on_notify=self.tray.notify, on_problem=self.problem)
        missing = settings if isinstance(settings, Missing) else None
        usable = settings if isinstance(settings, Settings) else Settings("", "", "", "", "", "", "")
        self.settings = usable
        self.activity: ActivityTracker
        def context() -> ContextSnapshot:
            # The tracker and runner refer to each other; observe after composition.
            return self.activity.snapshot()

        work_folder = DIRECTORY / "work"
        tasks = TaskStore(work_folder / "work.sqlite3")
        routines = RoutineStore(tasks.path)
        saved_context = ContextStore(tasks.path)
        self.agent = WorkAgent(
            tasks, routines, saved_context, BridgeClient(work_folder),
            lambda runner: self.ui.agent(
                None, runner.stop, on_problem=self.problem, on_reset=runner.new_conversation, on_submit=runner.start,
                on_work=self.show_work, reveal=False,
            ),
            on_snapshot=self.work_changed, on_done=self.agent_done, report_error=log,
            on_start=lambda: self.show(State.AGENT), context=context, meeting=self.meeting_command,
            browser_app=self.ui.browser_app,
            canvas_requests=CanvasRequests(work_folder / "canvas-requests"),
        )
        archive = MeetingArchive(DIRECTORY / "meetings")
        followup_folder = work_folder / "meeting-followups"
        self.followups = MeetingWorkflow(
            archive, MeetingRequests(followup_folder), FollowupStore(followup_folder / "packages"),
            submit_identified=self.agent.submit_identified, read_work=self.agent.snapshot,
            retain_work=lambda ids: self.agent.watch(ids, owner="meetings"),
            publish=self.ui.meetings_changed, report_error=log,
        )
        self.ui.configure_meetings(self.followups, self.open_meeting_file)
        events = TranscriptEvents(archive, routines, on_error=log, on_change=self.refresh_work)
        self.meetings = Meetings(
            usable, lambda source: recording.MeetingAudio(computer_audio=source == "both"), archive=archive,
            on_update=self.recording_changed, on_recovery=events.recover, on_complete=events.completed,
        )
        self.activity = ActivityTracker(
            foreground_context, self.agent.running, enabled=os.environ.get("TOOLKIT_AGENT_CONTEXT", "recent").lower() != "off",
        )
        self.assistant = Assistant(
            self.desktop,
            usable,
            record=self.record,
            submit=self.jobs.put,
            run_agent=self.canvas_voice,
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
        self.agent.start_worker()
        self.followups.start()
        self.meetings.start_worker()
        if os.environ.get("TOOLKIT_DESKTOP", "on").lower() != "off":
            self.ui.desktop(self.agent.submit_canvas, self.agent.command, self.agent.snapshot,
                            watch=self.agent.watch,
                            show_agent=self.agent.show, show_work=self.show_work, stop=self.agent.stop,
                            overlay=self.toggle_overlay, on_problem=self.problem)
        if (layout_window := windows.find(LAYOUT_WINDOW)) is not None:
            system.post_message(layout_window, WM_QUERY_LAYOUT_STATE, 0, 0)
        keeper.ready()  # tells the keeper this version came up
        if self.settings.api_key:
            threading.Thread(target=self.agent.prepare, name="agent panel warmup", daemon=True).start()
        system.run_message_loop()

    def quit(self) -> None:
        try:
            self.ui.close_desktop()
        except OSError as error:
            log(str(error))
        try:
            self.meetings.close()
        except OSError as error:
            log(str(error))
        self.followups.close()
        self.followups.wait_closed()
        self.agent.close()
        if not self.agent.wait_closed(timeout=5):
            log("The work frontend did not finish closing before assistant shutdown.")
        self.activity.close()
        self.ui.close_agent()
        self.ui.close_work()
        self.ui.close()
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

    def refresh_work(self) -> None:
        self.agent.refresh()
        self.followups.refresh()

    def work_changed(self, snapshot: WorkSnapshot) -> None:
        self.ui.work(self.agent.command, on_problem=self.problem, reveal=False).publish(snapshot)

    def show_work(self) -> None:
        self.ui.work(self.agent.command, on_problem=self.problem)
        self.agent.refresh()

    def show_meetings(self) -> None:
        self.show_work()
        self.ui.show_meetings()
        self.followups.refresh()

    def open_meeting_file(self, path: Path) -> None:
        def open_file() -> None:
            try:
                os.startfile(path)
            except OSError:
                self.problem("Windows could not open this file. You can still review it in Meetings.")
        self.jobs.put(open_file)

    def meeting_command(self, command: StartMeeting | MeetingCommand) -> MeetingSucceeded | MeetingFailed:
        """Execute the worker's validated request with the existing capture owner."""
        try:
            match command:
                case StartMeeting(source, title):
                    self.meetings.start(source, title)
                case MeetingCommand.STOP:
                    self.meetings.stop()
                case MeetingCommand.RETRY:
                    self.meetings.retry()
                case MeetingCommand.LIST:
                    return MeetingSucceeded(self.meetings.recent())
                case MeetingCommand.STATUS:
                    pass
                case _:
                    assert_never(command)
            return MeetingSucceeded(self.meetings.describe())
        except (OSError, ValueError) as error:
            return MeetingFailed(str(error))

    def agent_done(self) -> None:
        if self.assistant.listening is None:
            self.assistant.idle()

    def canvas_voice(self, text: str) -> None:
        if not self.ui.submit_canvas_voice(text, self.agent.voice):
            self.agent.voice(text)

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
            shell.MenuItem(_Menu.WORK.value, "Work\u2026"),
            shell.MenuItem(_Menu.CANVAS.value, "Workspace canvas\tCtrl+Alt+D"),
            shell.MenuItem(_Menu.LAUNCHER.value, "Desktop launcher\tCtrl+Alt+Space"),
            shell.MenuItem(_Menu.STOP_AGENT.value, "Stop agent\tHold V + \u00d6", enabled=self.agent.running()),
            shell.MenuItem(_Menu.MEETING.value, "Stop recording" if self.meetings.snapshot().capturing else "Record a meeting"),
            shell.MenuItem(_Menu.TRANSCRIPTS.value, "Transcripts"),
            shell.MenuItem(_Menu.FOLLOWUPS.value, "Meeting follow-up\u2026"),
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
            case _Menu.WORK.value:
                self.show_work()
            case _Menu.CANVAS.value:
                self.ui.show_desktop()
            case _Menu.LAUNCHER.value:
                self.ui.show_desktop(launcher=True)
            case _Menu.STOP_AGENT.value:
                self.agent.stop()
            case _Menu.MEETING.value:
                self.recording_action("stop" if self.meetings.snapshot().capturing else "start")
            case _Menu.TRANSCRIPTS.value:
                self.recording_action("folder")
            case _Menu.FOLLOWUPS.value:
                self.show_meetings()
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
        ui = Ui()
        errors: queue.Queue[BaseException] = queue.Queue()

        def control() -> None:
            try:
                AssistantApp(settings, ui).run()
            except BaseException as error:
                errors.put(error)
            finally:
                try:
                    ui.close()
                except BaseException as error:
                    errors.put(error)

        controller = threading.Thread(target=control, name="assistant control", daemon=True)
        controller.start()
        try:
            ui.run()
        finally:
            if controller.is_alive() and (window := windows.find(ASSISTANT_WINDOW)) is not None:
                system.post_message(window, system.WM_CLOSE, 0, 0)
            controller.join(timeout=10)
        if controller.is_alive():
            raise OSError("The assistant control thread did not finish closing.")
        if not errors.empty():
            raise errors.get_nowait()
        return Exit.QUIT
    finally:
        system.release_lock(lock)


def show_error(message: str) -> None:
    system.message_box(message, "Toolkit", system.MB_ICONHAND)
