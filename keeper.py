"""The keeper: the one part of the toolkit that never changes itself.

It runs the layout and the assistant, and installs changes to them: when the agent
asks, it checks the change (selfcheck.py), keeps a copy of the current version,
installs, and restarts both. A change that fails its checks isn't installed; one
that doesn't come up, crashes during its ten-minute trial, or keeps crashing later
is undone, back to the copy. During the trial a small window offers Keep and Undo,
usable with the mouse alone in case the change broke the keyboard.

So that a change can never break what undoes it, the agent can't edit this file,
selfcheck.py or toolkit.pyw (`PROTECTED`), and this file uses nothing but Python's
standard library. The other programs talk to it through files: requests in one
folder, and a "ready" file each program writes once it's up.

    pythonw keeper.py        (toolkit.pyw does this)
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Final, Protocol, cast

LIVE: Final = Path(__file__).resolve().parent
"""The toolkit's own folder: the version that runs."""
DIRECTORY: Final = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "toolkit"
STATE: Final = DIRECTORY / "keeper"
REQUESTS: Final = STATE / "requests"
WORKSPACE: Final = DIRECTORY / "workspace"
"""Where the agent edits a copy of the source; installed from here."""
VERSIONS: Final = DIRECTORY / "versions"
"""A copy of each version replaced by an install, newest last."""
KEPT_VERSIONS: Final = 20

PROTECTED: Final = frozenset({"keeper.py", "selfcheck.py", "toolkit.pyw"})
"""Files a change can't touch."""
PROGRAMS: Final = {"layout": "miryoku", "assistant": "assistant"}
"""What the keeper runs: name -> module."""

# The window titles and messages that ask each program to quit cleanly (the layout
# lets go of held keys first). Defined here, the stable side; protocol.py uses them.
LAYOUT_WINDOW: Final = "toolkit layout"
ASSISTANT_WINDOW: Final = "toolkit assistant"
WM_CLOSE: Final = 0x0010
WM_QUIT_LAYOUT: Final = 0x8003

START_S: Final = 30.0
"""How long a program gets to say it's ready."""
TRIAL_S: Final = 600.0
CHECK_S: Final = 300.0
CRASH_WINDOW_S: Final = 600.0
MAX_CRASHES: Final = 3
"""Restarts allowed within `CRASH_WINDOW_S`; one more crash and the keeper stops trying."""


# ---- requests and readiness ------------------------------------------------------------


def ask(action: str, summary: str = "", *, folder: Path = REQUESTS) -> None:
    """Asks the keeper to `install` the workspace, `undo` the last install, `keep` a
    change on trial, or `restart_layout`."""
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns():020d}"
    temporary = folder / f"{name}.tmp"
    request: dict[str, str] = {"action": action, "summary": summary}
    temporary.write_text(json.dumps(request), encoding="utf-8")
    temporary.replace(folder / f"{name}.json")  # appears whole, never half-written


def take_requests(*, folder: Path = REQUESTS) -> list[tuple[str, str]]:
    """Requests waiting, oldest first; each is taken once."""
    taken: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        with contextlib.suppress(OSError, ValueError):
            data: object = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                fields = cast("dict[str, object]", data)
                action, summary = fields.get("action"), fields.get("summary")
                if isinstance(action, str) and isinstance(summary, str):
                    taken.append((action, summary))
        with contextlib.suppress(OSError):
            path.unlink()
    return taken


def ready() -> None:
    """Called by a program the keeper runs, once it's up."""
    if path := os.environ.get("TOOLKIT_READY"):
        with contextlib.suppress(OSError):
            Path(path).write_text("ready", encoding="utf-8")


# ---- versions on disk ------------------------------------------------------------------


def source_files(root: Path) -> list[str]:
    """The toolkit's own files under `root`, as relative paths: not caches or history."""
    found: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in relative.parts) and path.suffix != ".pyc":
            found.append(relative.as_posix())
    return sorted(found)


def _mirror(source: Path, target: Path, *, protect: bool) -> None:
    """Makes `target`'s source files the same as `source`'s."""
    keep = PROTECTED if protect else frozenset[str]()
    wanted = {f for f in source_files(source) if f not in keep}
    for name in source_files(target) if target.exists() else []:
        if name not in wanted and name not in keep:
            (target / name).unlink()
    for name in wanted:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / name).read_bytes())  # small files: a plain copy works everywhere


def snapshot(live: Path, versions: Path) -> str:
    """Copies the running version; the copy's name. Only the newest are kept."""
    versions.mkdir(parents=True, exist_ok=True)
    existing = sorted(p.name for p in versions.iterdir() if p.is_dir())
    number = int(existing[-1].split("-")[0]) + 1 if existing else 1
    name = f"{number:05d}-{datetime.now():%Y%m%d-%H%M%S}"
    _mirror(live, versions / name, protect=False)
    for old in existing[: max(0, len(existing) + 1 - KEPT_VERSIONS)]:
        shutil.rmtree(versions / old, ignore_errors=True)
    return name


def restore(version: Path, live: Path) -> None:
    _mirror(version, live, protect=True)


def install(workspace: Path, live: Path) -> None:
    _mirror(workspace, live, protect=True)


# ---- the keeper's logic ----------------------------------------------------------------


class Child(Protocol):
    def poll(self) -> int | None:
        """Its exit code once it has exited."""
        ...

    def stop(self) -> None: ...


class Check(Protocol):
    def result(self) -> tuple[bool, str] | None:
        """(passed, report) once finished."""
        ...


class World(Protocol):
    """What the keeper does to the computer."""

    def start(self, name: str) -> Child: ...
    def is_ready(self, name: str) -> bool: ...
    def start_check(self) -> Check: ...
    def snapshot(self) -> str: ...
    def restore(self, version: str) -> None: ...
    def install(self) -> None: ...
    def notify(self, text: str) -> None: ...
    def show_trial(self, summary: str | None) -> None:
        """Shows the trial window for this change; None hides it."""


class Stage(Enum):
    STARTING = auto()
    RUNNING = auto()
    CHECKING = auto()
    TRIAL = auto()


class Keeper:
    """Everything the keeper decides, driven by `request` and a regular `tick`."""

    __slots__ = ("check", "children", "crashes", "deadline", "done", "fresh", "stage", "summary", "undoable", "world")

    def __init__(self, world: World) -> None:
        self.world = world
        self.children: dict[str, Child] = {}
        self.stage = Stage.STARTING
        self.deadline = 0.0
        self.check: Check | None = None
        self.summary = ""
        self.undoable: str | None = None
        """The version the last install replaced."""
        self.fresh = False
        """Starting a just-installed change (so failing to start undoes it)."""
        self.crashes: list[float] = []
        self.done = False
        """The assistant was quit: the keeper should exit."""

    def start(self, now: float) -> None:
        for name in PROGRAMS:
            self.children[name] = self.world.start(name)
        self.stage, self.deadline = Stage.STARTING, now + START_S

    def stop_all(self) -> None:
        for child in self.children.values():
            if child.poll() is None:
                child.stop()
        self.children = {}

    def request(self, action: str, summary: str, now: float) -> None:
        match action:
            case "install":
                if self.stage in (Stage.CHECKING, Stage.STARTING):
                    self.world.notify("Busy installing; try again in a moment.")
                    return
                if self.stage is Stage.TRIAL:
                    self.world.show_trial(None)
                self.check, self.summary = self.world.start_check(), summary
                self.stage, self.deadline = Stage.CHECKING, now + CHECK_S
            case "keep":
                if self.stage is Stage.TRIAL:
                    self.stage = Stage.RUNNING
                    self.world.show_trial(None)
            case "undo":
                if self.undoable is None:
                    self.world.notify("There's no update to undo.")
                else:
                    self.rollback("you undid it", now)
            case "restart_layout":
                if (layout := self.children.pop("layout", None)) is not None and layout.poll() is None:
                    layout.stop()
                self.children["layout"] = self.world.start("layout")
            case _:
                pass

    def tick(self, now: float) -> None:
        match self.stage:
            case Stage.CHECKING:
                self.checking(now)
            case Stage.STARTING:
                self.starting(now)
            case Stage.TRIAL:
                if any(child.poll() not in (None, 0) for child in self.children.values()):
                    self.rollback("it crashed during its trial", now)
                    return
                self.running(now)  # quitting on purpose is handled as usual
                if now > self.deadline and not self.done:
                    self.stage = Stage.RUNNING
                    self.world.show_trial(None)
            case Stage.RUNNING:
                self.running(now)

    def checking(self, now: float) -> None:
        result = self.check.result() if self.check is not None else (False, "no check")
        if result is None:
            if now > self.deadline:
                self.world.notify("Not installed: the checks took too long.")
                self.stage = Stage.RUNNING
            return
        passed, report = result
        if not passed:
            self.world.notify(f"Not installed: the change failed its checks.\n\n{report[-3000:]}")
            self.stage = Stage.RUNNING
            return
        self.undoable = self.world.snapshot()
        self.stop_all()
        self.world.install()
        self.start(now)
        self.fresh = True

    def starting(self, now: float) -> None:
        failed = any(child.poll() is not None for child in self.children.values()) or now > self.deadline
        if failed and self.fresh and self.undoable is not None:
            self.rollback("it didn't start", now)
        elif failed:
            self.world.notify("The toolkit didn't start properly; see the logs.")
            self.stage = Stage.RUNNING
        elif all(self.world.is_ready(name) for name in self.children):
            if self.fresh:
                self.stage, self.deadline = Stage.TRIAL, now + TRIAL_S
                self.world.show_trial(self.summary)
            else:
                self.stage = Stage.RUNNING
            self.fresh = False

    def running(self, now: float) -> None:
        for name, child in list(self.children.items()):
            code = child.poll()
            if code is None:
                continue
            del self.children[name]
            if code == 0:
                if name == "assistant":  # Quit in the tray menu: everything stops
                    self.stop_all()
                    self.done = True
                    return
                continue  # the layout quit on purpose: it stays stopped until asked
            self.crashes = [t for t in self.crashes if now - t < CRASH_WINDOW_S] + [now]
            if len(self.crashes) > MAX_CRASHES:
                if self.undoable is not None:
                    self.rollback("it kept crashing", now)
                else:
                    self.world.notify(f"The {name} keeps crashing, so it's stopped. See {name}.log.")
                return
            self.children[name] = self.world.start(name)

    def rollback(self, why: str, now: float) -> None:
        on_trial = self.stage is Stage.TRIAL
        version, self.undoable = self.undoable, None
        self.stop_all()
        if version is not None:
            self.world.restore(version)
        self.start(now)
        self.fresh = False
        self.crashes = []
        self.world.notify(f"Update undone: {why}.")
        if on_trial:
            self.world.show_trial(None)


# ---- on Windows ------------------------------------------------------------------------


class _User32(Protocol):
    def FindWindowW(self, cls: None, title: str) -> int: ...  # noqa: N802 (Win32 names)
    def PostMessageW(self, hwnd: int, message: int, wparam: int, lparam: int) -> int: ...  # noqa: N802


def _log(text: str) -> None:
    with contextlib.suppress(OSError):
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        with (DIRECTORY / "keeper.log").open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")


def _pythonw() -> str:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


class _Process:
    """A program the keeper started. Stopping asks it to quit (so the layout lets go
    of held keys) and only then ends it."""

    __slots__ = ("process", "quit_message", "title", "user32")

    def __init__(self, process: subprocess.Popen[bytes], title: str, quit_message: int, user32: _User32) -> None:
        self.process, self.title, self.quit_message, self.user32 = process, title, quit_message, user32

    def poll(self) -> int | None:
        return self.process.poll()

    def stop(self) -> None:
        if hwnd := self.user32.FindWindowW(None, self.title):
            self.user32.PostMessageW(hwnd, self.quit_message, 0, 0)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


class _Check:
    __slots__ = ("output", "process")

    def __init__(self) -> None:
        STATE.mkdir(parents=True, exist_ok=True)
        self.output = STATE / "check.txt"
        with self.output.open("wb") as out:
            self.process = subprocess.Popen(
                [_pythonw(), str(LIVE / "selfcheck.py"), str(WORKSPACE)], stdout=out, stderr=subprocess.STDOUT, creationflags=0x0800_0000
            )

    def result(self) -> tuple[bool, str] | None:
        code = self.process.poll()
        if code is None:
            return None
        return code == 0, self.output.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    if sys.platform != "win32":
        sys.exit("the keeper runs on Windows")
    import msvcrt  # noqa: PLC0415 (Windows only)
    import tkinter as tk  # noqa: PLC0415

    STATE.mkdir(parents=True, exist_ok=True)
    lock_file = (STATE / "keeper.lock").open("a+b")
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        sys.exit(0)  # already running
    user32 = cast("_User32", ctypes.WinDLL("user32"))
    root = tk.Tk()
    root.withdraw()
    trial: list[tk.Toplevel] = []
    choices: list[str] = []

    def corner(window: tk.Toplevel, width: int, height: int) -> None:
        window.geometry(f"{width}x{height}+{root.winfo_screenwidth() - width - 24}+{root.winfo_screenheight() - height - 72}")
        window.attributes("-topmost", True)  # pyright: ignore[reportUnknownMemberType] (Tk's stubs)

    class WinWorld:
        __slots__ = ()

        def start(self, name: str) -> Child:
            flag = STATE / f"ready-{name}"
            flag.unlink(missing_ok=True)
            env = {**os.environ, "TOOLKIT_READY": str(flag)}
            with (DIRECTORY / f"{name}.log").open("ab") as errors:
                process = subprocess.Popen([_pythonw(), "-m", PROGRAMS[name]], cwd=LIVE, env=env, stderr=errors, creationflags=0x0800_0000)
            _log(f"started {name}")
            if name == "layout":
                return _Process(process, LAYOUT_WINDOW, WM_QUIT_LAYOUT, user32)
            return _Process(process, ASSISTANT_WINDOW, WM_CLOSE, user32)

        def is_ready(self, name: str) -> bool:
            return (STATE / f"ready-{name}").exists()

        def start_check(self) -> Check:
            _log("checking the workspace")
            return _Check()

        def snapshot(self) -> str:
            return snapshot(LIVE, VERSIONS)

        def restore(self, version: str) -> None:
            _log(f"restoring {version}")
            restore(VERSIONS / version, LIVE)

        def install(self) -> None:
            _log("installing the workspace")
            install(WORKSPACE, LIVE)

        def notify(self, text: str) -> None:
            _log(text)
            toast = tk.Toplevel(root, padx=14, pady=10)
            toast.title("Toolkit")
            tk.Label(toast, text=text[:700], justify="left", wraplength=420).pack(anchor="w")
            tk.Button(toast, text="OK", width=8, command=toast.destroy).pack(anchor="e", pady=(8, 0))
            corner(toast, 460, 110 + 14 * min(text.count("\n"), 12))
            toast.after(20_000, toast.destroy)

        def show_trial(self, summary: str | None) -> None:
            for window in trial:
                window.destroy()
            trial.clear()
            if summary is None:
                return
            window = tk.Toplevel(root, padx=14, pady=10)
            window.title("Toolkit update")
            window.protocol("WM_DELETE_WINDOW", lambda: choices.append("keep"))
            tk.Label(window, text=f"Updated: {summary}", justify="left", wraplength=380, font=("Segoe UI Semibold", 10)).pack(anchor="w")
            tk.Label(window, text="On trial for ten minutes; it's undone if it crashes.", justify="left").pack(anchor="w")
            buttons = tk.Frame(window)
            buttons.pack(anchor="e", pady=(8, 0))
            tk.Button(buttons, text="Undo", width=8, command=lambda: choices.append("undo")).pack(side="left", padx=(0, 6))
            tk.Button(buttons, text="Keep", width=8, command=lambda: choices.append("keep")).pack(side="left")
            corner(window, 420, 120)
            trial.append(window)

    keeper = Keeper(WinWorld())
    keeper.start(time.monotonic())

    def loop() -> None:
        now = time.monotonic()
        for action, summary in [*((c, "") for c in choices), *take_requests()]:
            _log(f"request {action} {summary}")
            keeper.request(action, summary, now)
        choices.clear()
        keeper.tick(now)
        if keeper.done:
            root.destroy()
        else:
            root.after(500, loop)

    root.after(500, loop)
    root.mainloop()


if __name__ == "__main__":
    main()
