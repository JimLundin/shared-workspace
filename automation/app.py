"""The supervised Windows work process owns execution, never UI or audio.

The profile lease outlives the controller and all of its native cleanup. The
hidden window is created and destroyed on this process's main thread; WM_CLOSE
parks active work and drains its effects before returning to the keeper.
"""

import contextlib
import importlib
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

import keeper
import usage
from assistant.activity import ContextSnapshot, Observation, observed
from assistant.agent_execution import AgentExecution
from assistant.agent_session import Begin, Done, Finished, LiveMessage, Problem, Progress, Said
from assistant.browser import Browser, Product
from assistant.browser_surface_control import AppBrowserControl, CompositeBrowser
from assistant.canvas_execution import CanvasWork
from assistant.canvas_requests import CanvasRequests
from assistant.llm import Missing, Settings
from assistant.machine import NativeDesktop, WinMachine
from assistant.meeting_execution import MeetingWork
from assistant.meeting_followup_store import FollowupStore
from assistant.meeting_requests import MeetingRequests
from assistant.outlook import OutlookDrafts
from assistant.recording_state import AudioSource
from protocol import AUTOMATION_WINDOW
from usage import DIRECTORY
from winapi import screen, system, windows
from winapi.shapes import Window

from . import ipc
from .bridge import BridgeServer, ProfileLease
from .context import ContextStore, select_context
from .controller import ControllerSnapshot, Fault, Idle, Stopping, WorkController, WorkExecution, Working
from .execution import ApprovalReview, Log, Outcome
from .routines import RoutineStore
from .store import TaskStore
from .task import CanvasSource, Claim, RunRef, owns


def report(message: str, *, folder: Path = DIRECTORY / "work") -> None:
    """Operational diagnostics only; SDK and IPC payloads never enter this log."""
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "worker.log").open("a", encoding="utf-8") as file:
            file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


class DurableInput:
    """Unmediated synchronous input is unavailable; durable execution owns it."""

    __slots__ = ()

    def agent_input(
        self, title: str, detail: str = "", *, question: bool = False, cancel: threading.Event | None = None,
    ) -> str | None:
        raise OSError("This work requires a durable question or approval in the work overview.")


@dataclass(frozen=True, slots=True)
class MeetingRun:
    run: RunRef
    stop: threading.Event


class RemoteMeetings:
    """The execution thread delegates capture to its assistant-owned meeting service."""

    __slots__ = ("bridge", "current")

    def __init__(self, bridge: BridgeServer) -> None:
        self.bridge = bridge
        self.current: MeetingRun | None = None

    def request(self, operation: ipc.MeetingOperation) -> str:
        if self.current is None:
            raise OSError("No work run owns this meeting request.")
        return self.bridge.meeting(self.current.run, operation, self.current.stop)

    def start(self, source: AudioSource = "both", title: str = "Meeting") -> None:
        self.request(ipc.StartMeeting(source, title))

    def stop(self) -> None:
        self.request(ipc.MeetingCommand.STOP)

    def retry(self) -> None:
        self.request(ipc.MeetingCommand.RETRY)

    def recent(self) -> str:
        return self.request(ipc.MeetingCommand.LIST)

    def describe(self) -> str:
        return self.request(ipc.MeetingCommand.STATUS)


class DesktopContext:
    """Fresh foreground observations exclude both Toolkit process owners."""

    __slots__ = ("bridge", "enabled")

    def __init__(self, bridge: BridgeServer, *, enabled: bool) -> None:
        self.bridge, self.enabled = bridge, enabled

    def snapshot(self) -> ContextSnapshot:
        now = time.time()
        if not self.enabled:
            return ContextSnapshot(now, "disabled")
        observation: Observation = "unavailable"
        try:
            handle = windows.foreground()
            if handle:
                observation = ("toolkit" if windows.process_id(handle) in (os.getpid(), self.bridge.assistant_pid)
                               else Window(handle, windows.title(handle), windows.exe(handle), windows.is_minimized(handle)))
        except OSError:
            pass
        return observed(self.bridge.context(), observation, now, agent_active=True)


class VolatileLog:
    __slots__ = ("bridge", "run")

    def __init__(self, bridge: BridgeServer, run: RunRef) -> None:
        self.bridge, self.run = bridge, run

    def publish(self, event: ipc.PresentationEvent) -> None:
        try:
            self.bridge.publish(ipc.LogEvent(self.run, event))
        except ValueError:
            self.bridge.publish(ipc.Resync("A live update exceeded the transport bound; reload the durable work result."))

    def step(self, message: str, /) -> None:
        self.publish(Progress(message))

    def say(self, message: str, /) -> None:
        self.publish(Said(message, time.time()))

    def updates(self) -> Callable[[LiveMessage], None]:
        return self.publish

    def problem(self, message: str, /) -> None:
        self.publish(Problem(message, time.time()))

    def done(self, message: str, /) -> None:
        self.publish(Done(message, time.time()))


class NativeExecution:
    __slots__ = ("apps", "bridge", "contexts", "execution", "meetings")

    def __init__(
        self, execution: AgentExecution, bridge: BridgeServer, meetings: RemoteMeetings, contexts: ContextStore, apps: AppBrowserControl,
    ) -> None:
        self.execution, self.bridge, self.meetings, self.contexts = execution, bridge, meetings, contexts
        self.apps = apps

    def run_work(self, claim: Claim, store: TaskStore, log: Log, stop: threading.Event) -> Outcome:
        self.meetings.current = MeetingRun(claim.run, stop)

        def review(preview: ApprovalReview) -> bool:
            try:
                return self.bridge.publish(ipc.ReviewEvent(claim.run, preview))
            except ValueError:
                return False

        try:
            scoped = isinstance(claim.task.source, CanvasSource)
            context = None if scoped else self.bridge.invocation(claim.task.id)
            saved = "" if scoped else select_context(self.contexts.list(), claim.task.prompt[:20_000], time.time()).describe()
            # Captured-source work never borrows a live app control binding.
            # Ordinary cleanup disconnects this run while the UI keeps cards.
            with contextlib.nullcontext() if scoped else self.apps.bind(claim.run, stop):
                return self.execution.run_work(claim, store, log, stop, context=context, context_text=saved, review=review)
        finally:
            self.meetings.current = None


def _native_execution(settings: Settings | Missing, bridge: BridgeServer, hwnd: int, contexts: ContextStore) -> tuple[NativeExecution, WinMachine]:
    meetings = RemoteMeetings(bridge)
    apps = AppBrowserControl(bridge)
    browser = CompositeBrowser(Browser(DIRECTORY / "browser-profile", source=Product.EDGE), apps)
    machine = WinMachine(NativeDesktop(hwnd), DurableInput(), meetings=meetings, browser=browser)
    context = DesktopContext(bridge, enabled=os.environ.get("TOOLKIT_AGENT_CONTEXT", "recent").lower() != "off")
    usable = settings if isinstance(settings, Settings) else Settings("", "", "", "", "", "", "")
    followups = bridge.folder / "meeting-followups"
    try:
        execution = AgentExecution(
            usable, machine, journal=lambda entry: None,
            record=lambda details: usage.record("automation", "agent", details),
            report_error=lambda detail: report("Agent execution failed; its durable task contains the recovery state.", folder=bridge.folder),
            close_background=machine.close_background, context=context.snapshot,
            meeting=MeetingWork(MeetingRequests(followups), FollowupStore(followups / "packages"), OutlookDrafts(followups / "mail")),
            canvas=CanvasWork(CanvasRequests(bridge.folder / "canvas-requests")),
        )
    except BaseException:
        machine.close_background()
        raise
    return NativeExecution(execution, bridge, meetings, contexts, apps), machine


def run(  # noqa: PLR0915 (one scope owns the lease, native window, and cleanup order)
    settings: Settings | Missing, *, folder: Path = DIRECTORY / "work", execution: WorkExecution | None = None,
    ready: Callable[[], None] = keeper.ready, title: str = AUTOMATION_WINDOW,
) -> None:
    """Own one profile until all work is parked and native resources are released.

    The optional execution and readiness adapters are also used by native
    process tests with a real journal, real pipe, and isolated clipboard HWND.
    """
    with ProfileLease(folder), contextlib.ExitStack() as cleanup:
        screen.use_physical_pixels()
        hwnd = windows.create_hidden(title)
        cleanup.callback(windows.destroy, hwnd)
        tasks = TaskStore(folder / "work.sqlite3")
        routines, contexts = RoutineStore(tasks.path), ContextStore(tasks.path)
        routines.ensure_meeting_notes(at=time.time())
        shutdown, failed = threading.Event(), threading.Event()
        controller: WorkController

        def run_owns(reference: RunRef) -> bool:
            task = tasks.get(reference.task_id)
            return task is not None and owns(task, reference)

        def wake() -> None:
            controller.wake()

        bridge = BridgeServer(folder, epoch=uuid.uuid4().hex, wake=wake, owns=run_owns)
        cleanup.callback(bridge.close)
        if execution is None:
            execution, machine = _native_execution(settings, bridge, hwnd, contexts)
            cleanup.callback(machine.close_background)
        previous: RunRef | None = None

        def problem(reason: str) -> None:
            # Caller details may include exception payloads; retain only an
            # operational marker here and expose durable task state separately.
            failed.set()
            report("The work process encountered an error and is stopping for recovery.", folder=folder)
            system.post_message(hwnd, system.WM_CLOSE, 0, 0)

        def changed(snapshot: ControllerSnapshot) -> None:
            nonlocal previous
            active: RunRef | None
            match snapshot.status:
                case Working(reference) | Stopping(reference):
                    active = reference
                case Fault():
                    active = None
                    problem("Work controller failed.")
                case Idle():
                    active = None
                case _:
                    assert_never(snapshot.status)
            if previous is not None and active != previous:
                bridge.publish(ipc.LogEvent(previous, Finished()))
            previous = active

        def log(claim: Claim) -> Log:
            result = VolatileLog(bridge, claim.run)
            result.publish(Begin(claim.task.prompt, time.time()))
            return result

        controller = WorkController(tasks, execution, owner=bridge.discovery.epoch, log=log, now=time.time,
                                    new_run_id=lambda: uuid.uuid4().hex, on_change=changed, on_error=problem)

        def stop_work() -> None:
            controller.shutdown()
            try:
                bridge.close()
            finally:
                controller.wait()

        cleanup.callback(stop_work)

        def maintenance() -> None:
            while not shutdown.is_set():
                try:
                    if bridge.wait(0):
                        problem("Work transport exited.")
                        return
                    now = time.time()
                    routines.scan_files(now)
                    if routines.dispatch(tasks, now=now):
                        controller.wake()
                except Exception:
                    problem("Routine polling failed.")
                    return
                shutdown.wait(1)

        bridge.start()
        controller.start()  # Synchronous exclusive-owner recovery precedes readiness.
        thread = threading.Thread(target=maintenance, name="standing work", daemon=True)
        thread.start()
        warmup: threading.Thread | None = None

        def warm_sdk() -> None:
            try:
                importlib.import_module("assistant.sdk")
            except Exception:
                report("The agent SDK could not be warmed; the next task will report its configuration state.", folder=folder)

        try:
            ready()
            if isinstance(settings, Settings) and settings.api_key:
                warmup = threading.Thread(target=warm_sdk, name="work SDK warmup", daemon=True)
                warmup.start()
            for message in system.messages():
                if message.hwnd == hwnd and message.message == system.WM_CLOSE:
                    break
        finally:
            shutdown.set()
            controller.shutdown()
            thread.join()
            if warmup is not None:
                warmup.join()
        if failed.is_set():
            raise OSError("The work process stopped after an operational failure.")
