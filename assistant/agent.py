"""The agent: a model working on a task with tools, step by step, until it's done,
stopped, or out of steps.

What it may do without asking is decided here, not by the model:
- Reading (files, folders, the clipboard, windows, screenshots) is free.
- Writing a file, opening something, or running a command asks every time, unless
  the command is on the allowed list in config (and can't chain another on).
- The mouse and keyboard need permission once per task.
Every action goes to the agent's journal (a log you can read back).
"""

from __future__ import annotations

import json
import os
import shlex
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, cast

from config import AGENT_MAX_STEPS, ALLOWED_COMMANDS
from winapi.shapes import Window

from .geometry import Shot
from .llm import (
    Failure,
    Image,
    Json,
    Message,
    Settings,
    Text,
    ToolCall,
    ToolSpec,
    assistant_message,
    chat,
    system_message,
    tool_result,
    user_message,
)
from .publishing import DEFAULT_REMOTE, PublishError, publish_source
from .workspace import SourceError, Workspace

MAX_CHARS: Final = 20_000
"""Most text one tool result gives the model (a file, a command's output)."""
MAX_FILES: Final = 200
MAX_SOURCE_CHARS: Final = 60_000
"""Most of one source file the model reads at once."""
KEPT_SCREENSHOTS: Final = 2
"""Screenshots kept in the conversation; older ones are replaced by a note, to save space."""


class Machine(Protocol):
    """What the agent's tools act on: the computer, and the user."""

    def run_powershell(self, command: str, timeout_s: float) -> tuple[int, str]:
        """Exit code, and stdout and stderr together."""
        ...

    def screenshot(self) -> tuple[bytes, Shot]: ...
    def click(self, x: int, y: int, button: str, count: int) -> None:
        """At a point on the screen."""

    def type_text(self, text: str) -> None: ...
    def press_keys(self, strokes: list[tuple[int, bool]]) -> None:
        """Virtual keys going down (True) and up."""

    def scroll(self, x: int, y: int, clicks: int) -> None: ...
    def windows(self) -> list[Window]: ...
    def focus(self, handle: int) -> bool: ...
    def get_clipboard(self) -> str | None: ...
    def set_clipboard(self, text: str) -> None: ...
    def open(self, target: str) -> None:
        """A file, folder or web address, as double-clicking it would."""

    def ask_user(self, question: str) -> str | None: ...
    def confirm(self, title: str, detail: str) -> bool:
        """Asks the user yes or no."""
        ...

    def review(self, summary: str, diff: str) -> bool:
        """Shows the user a change to the toolkit; True if they approve it."""
        ...

    def install(self, summary: str) -> None:
        """Asks the keeper to install the workspace."""


class Log(Protocol):
    """Where the user watches the agent work."""

    def step(self, text: str) -> None: ...
    def say(self, text: str) -> None: ...
    def problem(self, text: str) -> None: ...
    def done(self, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Output:
    text: str
    image: bytes | None = None
    ok: bool = True


class ArgumentError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Args:
    tool: str
    values: dict[str, object]

    def text(self, name: str, default: str | None = None) -> str:
        value = self.values.get(name, default)
        if not isinstance(value, str):
            raise ArgumentError(f"{self.tool} needs {name} (text).")
        return value

    def number(self, name: str, default: int | None = None) -> int:
        value = self.values.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ArgumentError(f"{self.tool} needs {name} (a number).")
        return round(value)

    def flag(self, name: str) -> bool:
        return self.values.get(name) is True


DECLINED: Final = "The user declined: don't run this. Ask them, or try something else."
_UNSAFE: Final = frozenset(";|&<>`$(){}\n\r")


def allowed(command: str) -> bool:
    """A command on the allowed list, with nothing that could run another after it."""
    command = command.strip()
    if any(c in _UNSAFE for c in command) or "--output" in command:
        return False
    lower = command.lower()
    if not any(lower == p.lower() or lower.startswith(p.lower() + " ") for p in ALLOWED_COMMANDS):
        return False
    if lower.startswith("git "):
        try:
            words = shlex.split(lower)
        except ValueError:
            return False
        safe = {
            "status": {"--short", "--branch", "--porcelain", "-s", "-b"},
            "log": {"--oneline", "--stat", "--name-only", "--no-decorate"},
            "diff": {"--stat", "--name-only", "--name-status", "--cached", "--staged", "--no-ext-diff", "--no-textconv"},
        }
        return words[1] in safe and all(w in safe[words[1]] or (w.startswith("-") and w[1:].isdigit()) for w in words[2:])
    return True


_MODIFIERS: Final = {"ctrl": 0xA2, "control": 0xA2, "shift": 0xA0, "alt": 0xA4, "win": 0x5B, "windows": 0x5B}
_KEYS: Final = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    ",": 0xBC, ".": 0xBE, "-": 0xBD, "=": 0xBB, "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD,
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
}  # fmt: skip


def key_strokes(combo: str) -> list[tuple[int, bool]] | None:
    """`ctrl+shift+s` as virtual keys down and up; None if a name isn't known."""
    *mods, key = [part.strip() for part in combo.lower().split("+")]
    if key not in _KEYS or any(m not in _MODIFIERS for m in mods):
        return None
    held = [_MODIFIERS[m] for m in mods]
    return [*((m, True) for m in held), (_KEYS[key], True), (_KEYS[key], False), *((m, False) for m in reversed(held))]


def _schema(properties: dict[str, Json], required: list[str]) -> dict[str, Json]:
    return {"type": "object", "properties": properties, "required": cast("Json", required)}


_TEXT: Final[Json] = {"type": "string"}
_NUMBER: Final[Json] = {"type": "integer"}

SPECS: Final = (
    ToolSpec("list_directory", "List a folder: subfolders first, then files with sizes.", _schema({"path": _TEXT}, ["path"])),
    ToolSpec("read_file", f"Read a text file (up to {MAX_CHARS} characters).", _schema({"path": _TEXT}, ["path"])),
    ToolSpec(
        "find_files", "Find files under a folder by name pattern, e.g. *.xlsx.", _schema({"folder": _TEXT, "pattern": _TEXT}, ["folder", "pattern"])
    ),
    ToolSpec("write_file", "Write a text file (the user is asked first).", _schema({"path": _TEXT, "content": _TEXT}, ["path", "content"])),
    ToolSpec(
        "run_powershell", "Run a PowerShell command and get its output (the user may be asked first).", _schema({"command": _TEXT}, ["command"])
    ),
    ToolSpec(
        "open", "Open a file, folder or web address as double-clicking would (the user is asked first).", _schema({"target": _TEXT}, ["target"])
    ),
    ToolSpec("get_clipboard", "The text on the clipboard.", _schema({}, [])),
    ToolSpec("set_clipboard", "Put text on the clipboard.", _schema({"text": _TEXT}, ["text"])),
    ToolSpec("list_windows", "The open windows, front to back, with their handles.", _schema({}, [])),
    ToolSpec("focus_window", "Bring a window to the front by its handle.", _schema({"handle": _NUMBER}, ["handle"])),
    ToolSpec("screenshot", "See the screen. Coordinates for click and scroll are in the latest screenshot.", _schema({}, [])),
    ToolSpec(
        "click",
        "Click at a point in the latest screenshot.",
        _schema(
            {"x": _NUMBER, "y": _NUMBER, "button": {"type": "string", "enum": ["left", "right", "middle"]}, "double": {"type": "boolean"}}, ["x", "y"]
        ),
    ),
    ToolSpec("type_text", "Type text where the keyboard focus is.", _schema({"text": _TEXT}, ["text"])),
    ToolSpec("press_keys", "Press a key or combination, e.g. enter, ctrl+s, alt+tab.", _schema({"keys": _TEXT}, ["keys"])),
    ToolSpec(
        "scroll",
        "Scroll at a point in the latest screenshot; positive is up.",
        _schema({"x": _NUMBER, "y": _NUMBER, "clicks": _NUMBER}, ["x", "y", "clicks"]),
    ),
    ToolSpec("wait", "Wait a few seconds (at most 10) for something to load.", _schema({"seconds": _NUMBER}, ["seconds"])),
    ToolSpec("ask_user", "Ask the user a question and get their answer.", _schema({"question": _TEXT}, ["question"])),
)


SOURCE_SPECS: Final = (
    ToolSpec("list_source", "List the toolkit's own source files (your workspace copy).", _schema({}, [])),
    ToolSpec("read_source", "Read one of the toolkit's source files. Start with README.md.", _schema({"path": _TEXT}, ["path"])),
    ToolSpec(
        "write_source",
        "Write a whole source file in your workspace (new or replaced).",
        _schema({"path": _TEXT, "content": _TEXT}, ["path", "content"]),
    ),
    ToolSpec(
        "edit_source",
        "Replace text that appears exactly once in a source file in your workspace.",
        _schema({"path": _TEXT, "old": _TEXT, "new": _TEXT}, ["path", "old", "new"]),
    ),
    ToolSpec("delete_source", "Delete a source file in your workspace.", _schema({"path": _TEXT}, ["path"])),
    ToolSpec("show_changes", "Your changes so far, as a diff against the running toolkit.", _schema({}, [])),
    ToolSpec("check_changes", "Check syntax without executing code. Runtime tests follow keeper approval.", _schema({}, [])),
    ToolSpec(
        "apply_changes",
        "Submit changes to the keeper: it reviews a frozen copy, runs tests after approval, and installs if they pass. "
        "If they fail to start or crash, they're undone automatically.",
        _schema({"summary": _TEXT}, ["summary"]),
    ),
    ToolSpec(
        "publish_changes", "Review, test, commit and push source changes to a new Git branch; main and running source stay unchanged.",
        _schema({"summary": _TEXT}, ["summary"]),
    ),
    ToolSpec("discard_changes", "Throw away your changes: back to the running version.", _schema({}, [])),
)

SELF_CHANGE: Final = (
    "You can also change the toolkit itself, the program you are part of. You work on a copy (your workspace): "
    "read README.md first, then the files you need. Follow the code's style: strict types with no Any, __slots__ on "
    "classes, small functions, docstrings that say why; add or update tests for what you change. User settings (apps, "
    "snippets, rewrite prompts) live in config.py. Keep each change small. When done, run check_changes; fix anything "
    "it reports; then apply_changes with a one-line summary. The user approves the diff; the keeper installs it and "
    "runs runtime checks after approval, restarts the toolkit if they pass, and undoes failures. Approved code is not sandboxed. "
    "keeper.py, selfcheck.py and toolkit.pyw are protected. "
    "To save a change on GitHub, call publish_changes before apply_changes ends the task. "
    "Publishing reviews and tests a frozen copy, then pushes a new branch; it does not install or merge into main."
)


def _folders_first(path: Path) -> tuple[bool, str]:
    return not path.is_dir(), path.name.lower()


def instructions() -> str:
    return (
        "You are an agent working on the user's Windows work laptop, on their behalf. "
        f"It is {datetime.now():%A %Y-%m-%d %H:%M}. The user is {os.environ.get('USERNAME', 'unknown')}; "
        f"their home folder is {Path.home()}.\n"
        "Work in small steps and check results. Prefer files and PowerShell; use the screenshot, click and type "
        "tools only for programs that offer no other way. Before a group of actions, say in one short sentence what "
        "you're about to do. Don't send messages or email, or delete anything, unless the task plainly asks for it. "
        "If something is unclear, ask the user. When finished, reply with a short summary of what you did and found."
    )


class Agent:
    __slots__ = (
        "after_step",
        "check_failed",
        "checked",
        "control",
        "installing",
        "journal",
        "log",
        "machine",
        "max_steps",
        "messages",
        "settings",
        "shot",
        "stop",
        "workspace",
    )

    def __init__(
        self,
        settings: Settings,
        machine: Machine,
        log: Log,
        stop: threading.Event,
        *,
        max_steps: int = AGENT_MAX_STEPS,
        journal: Callable[[dict[str, Json]], None],
        after_step: Callable[[], None] | None = None,
        workspace: Workspace | None = None,
    ) -> None:
        self.settings = settings
        self.machine = machine
        self.log = log
        self.stop = stop
        self.max_steps = max_steps
        self.journal = journal
        self.after_step = after_step
        self.shot: Shot | None = None
        """The latest screenshot: clicks are in its coordinates."""
        self.control: bool | None = None
        """Whether the user lets the agent use the mouse and keyboard; None: not asked yet."""
        self.messages: list[Message] = []
        self.workspace = workspace
        """The toolkit's source, to change; None: no source tools."""
        self.checked: str | None = None
        """The diff that last passed its checks."""
        self.check_failed = False
        self.installing: str | None = None
        """Set once the keeper has been asked to install: the task is over."""

    def run(self, task: str) -> bool:
        """Works on the task; True if the model said it was done."""
        if self.workspace is not None:
            self.workspace.reset()  # every task starts from the running version
        specs = (*SPECS, *SOURCE_SPECS) if self.workspace is not None else SPECS
        prompt = f"{instructions()}\n\n{SELF_CHANGE}" if self.workspace is not None else instructions()
        self.messages = [system_message(prompt), user_message(task)]
        for _ in range(self.max_steps):
            if self.stop.is_set():
                self.log.done("Stopped.")
                return False
            reply = chat(self.settings, self.messages, model=self.settings.agent_model, tools=specs)
            if isinstance(reply, Failure):
                self.log.problem(reply.message)
                return False
            if not reply.tool_calls:
                self.log.done(reply.text or "Done.")
                return True
            if reply.text:
                self.log.say(reply.text)
            self.messages.append(assistant_message(reply.text, reply.tool_calls))
            image: bytes | None = None
            for call in reply.tool_calls:  # every call needs a result, even once stopped
                output = Output("Stopped by the user.", ok=False) if self.stop.is_set() else self.call(call)
                self.messages.append(tool_result(call.id, output.text))
                if self.installing is not None:
                    break
                image = output.image or image
            if image is not None:
                self.messages.append(user_message([Text("The screenshot you took:"), Image(image)]))
                self.forget_old_screenshots()
            if self.installing is not None:
                self.log.done(f"Installing: {self.installing}. The toolkit restarts in a moment.")
                return True
            if self.after_step is not None:
                self.after_step()
        self.log.done(f"Stopped after {self.max_steps} steps, not finished.")
        return False

    def forget_old_screenshots(self) -> None:
        with_images = [i for i, m in enumerate(self.messages) if isinstance(m.content, tuple) and any(isinstance(p, Image) for p in m.content)]
        for i in with_images[:-KEPT_SCREENSHOTS]:
            self.messages[i] = user_message("[an earlier screenshot, removed to save space]")

    def call(self, call: ToolCall) -> Output:
        try:
            raw: object = json.loads(call.arguments or "{}")
        except ValueError:
            raw = None
        if not isinstance(raw, dict):
            output = Output(f"Error: the arguments for {call.name} aren't a JSON object.", ok=False)
        elif (tool := _TOOLS.get(call.name)) is None:
            output = Output(f"Error: there's no tool called {call.name}.", ok=False)
        else:
            args = Args(call.name, {str(k): v for k, v in cast("dict[object, object]", raw).items()})
            try:
                output = tool(self, args)
            except (ArgumentError, SourceError, PublishError) as error:
                output = Output(f"Error: {error}", ok=False)
            except OSError as error:
                output = Output(f"Error: {error}", ok=False)
        self.log.step(f"{call.name} {call.arguments[:160]}" + ("" if output.ok else f"  ({output.text[:120]})"))
        self.journal(
            {
                "t": datetime.now().isoformat(timespec="seconds"),
                "tool": call.name,
                "args": call.arguments[:500],
                "ok": output.ok,
                "result": output.text[:300],
            }
        )
        return output

    # ---- permission --------------------------------------------------------------------

    def may_control(self) -> bool:
        if self.control is None:
            self.control = self.machine.confirm(
                "Let the agent use the mouse and keyboard?", "Until this task ends. Stop it any time from its window."
            )
        return self.control

    def on_screen(self, args: Args) -> tuple[int, int] | Output:
        if self.shot is None:
            return Output("Error: take a screenshot first; coordinates are in the latest screenshot.", ok=False)
        if not self.may_control():
            return Output("The user declined to let you use the mouse and keyboard for this task.", ok=False)
        return self.shot.to_screen(args.number("x"), args.number("y"))

    # ---- the tools ---------------------------------------------------------------------

    def list_directory(self, args: Args) -> Output:
        folder = Path(args.text("path")).expanduser()
        entries = sorted(folder.iterdir(), key=_folders_first)
        lines = [f"{p.name}/" if p.is_dir() else f"{p.name}  ({p.stat().st_size:,} bytes)" for p in entries[:500]]
        return Output("\n".join(lines) or "The folder is empty.")

    def read_file(self, args: Args) -> Output:
        text = Path(args.text("path")).expanduser().read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_CHARS:
            return Output(f"{text[:MAX_CHARS]}\n[cut: showing {MAX_CHARS} of {len(text)} characters]")
        return Output(text)

    def find_files(self, args: Args) -> Output:
        folder, pattern = Path(args.text("folder")).expanduser(), args.text("pattern")
        found: list[str] = []
        for path in folder.rglob(pattern):
            if path.is_file():
                found.append(str(path))
                if len(found) >= MAX_FILES:
                    found.append(f"[stopped at {MAX_FILES} files]")
                    break
        return Output("\n".join(sorted(found)) or "No files match.")

    def write_file(self, args: Args) -> Output:
        path, content = Path(args.text("path")).expanduser(), args.text("content")
        if not self.machine.confirm("Write a file?", f"{path}\n\n{content[:600]}"):
            return Output(DECLINED, ok=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return Output(f"Wrote {len(content)} characters to {path}.")

    def run_powershell(self, args: Args) -> Output:
        command = args.text("command")
        if not allowed(command) and not self.machine.confirm("Run this command?", command):
            return Output(DECLINED, ok=False)
        code, output = self.machine.run_powershell(command, timeout_s=120)
        return Output(f"Exit code {code}.\n{output[:MAX_CHARS]}", ok=code == 0)

    def open(self, args: Args) -> Output:
        target = args.text("target")
        if not self.machine.confirm("Open this?", target):
            return Output(DECLINED, ok=False)
        self.machine.open(target)
        return Output("Opened.")

    def get_clipboard(self, _: Args) -> Output:
        text = self.machine.get_clipboard()
        return Output(text[:MAX_CHARS] if text is not None else "The clipboard has no text.")

    def set_clipboard(self, args: Args) -> Output:
        self.machine.set_clipboard(args.text("text"))
        return Output("Copied.")

    def list_windows(self, _: Args) -> Output:
        return Output("\n".join(f"{w.handle}: {w.title} ({w.exe}{', minimized' if w.minimized else ''})" for w in self.machine.windows()))

    def focus_window(self, args: Args) -> Output:
        ok = self.machine.focus(args.number("handle"))
        return Output("Done." if ok else "Windows wouldn't bring it to the front.", ok=ok)

    def screenshot(self, _: Args) -> Output:
        png, self.shot = self.machine.screenshot()
        return Output(f"Screenshot taken: {self.shot.width}x{self.shot.height} pixels.", image=png)

    def click(self, args: Args) -> Output:
        point = self.on_screen(args)
        if isinstance(point, Output):
            return point
        button = args.text("button", "left")
        if button not in ("left", "right", "middle"):
            raise ArgumentError("click's button is left, right or middle.")
        self.machine.click(*point, button, 2 if args.flag("double") else 1)
        return Output("Clicked.")

    def type_text(self, args: Args) -> Output:
        text = args.text("text")
        if not self.may_control():
            return Output("The user declined to let you use the mouse and keyboard for this task.", ok=False)
        self.machine.type_text(text)
        return Output("Typed.")

    def press_keys(self, args: Args) -> Output:
        strokes = key_strokes(args.text("keys"))
        if strokes is None:
            raise ArgumentError("press_keys knows modifiers (ctrl, shift, alt, win) and keys like a, 1, enter, tab, esc, f5, left.")
        if not self.may_control():
            return Output("The user declined to let you use the mouse and keyboard for this task.", ok=False)
        self.machine.press_keys(strokes)
        return Output("Pressed.")

    def scroll(self, args: Args) -> Output:
        point = self.on_screen(args)
        if isinstance(point, Output):
            return point
        self.machine.scroll(*point, args.number("clicks"))
        return Output("Scrolled.")

    def wait(self, args: Args) -> Output:
        time.sleep(min(max(args.number("seconds"), 0), 10))
        return Output("Waited.")

    # ---- the toolkit's own source ------------------------------------------------------

    def source(self) -> Workspace:
        if self.workspace is None:
            raise ArgumentError("changing the toolkit isn't available here.")
        return self.workspace

    def list_source(self, _: Args) -> Output:
        return Output("\n".join(self.source().files()))

    def read_source(self, args: Args) -> Output:
        text = self.source().read(args.text("path"))
        if len(text) > MAX_SOURCE_CHARS:
            return Output(f"{text[:MAX_SOURCE_CHARS]}\n[cut: showing {MAX_SOURCE_CHARS} of {len(text)} characters]")
        return Output(text)

    def write_source(self, args: Args) -> Output:
        self.source().write(args.text("path"), args.text("content"))
        return Output("Written.")

    def edit_source(self, args: Args) -> Output:
        self.source().replace(args.text("path"), args.text("old"), args.text("new"))
        return Output("Edited.")

    def delete_source(self, args: Args) -> Output:
        self.source().delete(args.text("path"))
        return Output("Deleted.")

    def show_changes(self, _: Args) -> Output:
        changes = self.source().changes()
        if not changes:
            return Output("No changes yet.")
        return Output("\n".join(changes) + "\n\n" + self.source().diff()[:MAX_SOURCE_CHARS])

    def check_changes(self, _: Args) -> Output:
        passed, report = self.source().validate()
        self.checked = self.source().diff() if passed else None
        self.check_failed = not passed
        return Output("Syntax checks pass. Runtime checks run after approval.") if passed else Output(report[-6000:], ok=False)

    def apply_changes(self, args: Args) -> Output:
        summary, diff = args.text("summary"), self.source().diff()
        if not diff:
            raise ArgumentError("there are no changes to install.")
        if self.checked is None:
            raise ArgumentError(
                "the last check failed; fix the changes and check again."
                if self.check_failed
                else "run check_changes first; only checked changes can be installed."
            )
        if diff != self.checked:
            raise ArgumentError("the changes have changed since they were checked; run check_changes again.")
        if not self.machine.review(summary, diff):
            return Output("The user didn't approve these changes. Ask what they'd like instead.", ok=False)
        if self.stop.is_set():
            return Output("Stopped by the user.", ok=False)
        self.machine.install(summary)
        self.installing = summary
        return Output("Asked the keeper to install it.")

    def publish_changes(self, args: Args) -> Output:
        source = self.source()
        passed, report = source.validate()
        if not passed:
            return Output(report[-6000:], ok=False)
        result = publish_source(
            source.live, source.root, args.text("summary"), self.machine.confirm, self.stop.is_set,
            remote=os.environ.get("TOOLKIT_GIT_REMOTE", DEFAULT_REMOTE),
            base=os.environ.get("TOOLKIT_GIT_BASE", "main"),
        )
        return Output(result)

    def discard_changes(self, _: Args) -> Output:
        self.source().reset()
        self.checked, self.check_failed = None, False
        return Output("Discarded: back to the running version.")

    def ask_user(self, args: Args) -> Output:
        answer = self.machine.ask_user(args.text("question"))
        return Output(answer if answer else "The user didn't answer.", ok=bool(answer))


_TOOLS: Final[dict[str, Callable[[Agent, Args], Output]]] = {
    "list_directory": Agent.list_directory,
    "read_file": Agent.read_file,
    "find_files": Agent.find_files,
    "write_file": Agent.write_file,
    "run_powershell": Agent.run_powershell,
    "open": Agent.open,
    "get_clipboard": Agent.get_clipboard,
    "set_clipboard": Agent.set_clipboard,
    "list_windows": Agent.list_windows,
    "focus_window": Agent.focus_window,
    "screenshot": Agent.screenshot,
    "click": Agent.click,
    "type_text": Agent.type_text,
    "press_keys": Agent.press_keys,
    "scroll": Agent.scroll,
    "wait": Agent.wait,
    "ask_user": Agent.ask_user,
    "list_source": Agent.list_source,
    "read_source": Agent.read_source,
    "write_source": Agent.write_source,
    "edit_source": Agent.edit_source,
    "delete_source": Agent.delete_source,
    "show_changes": Agent.show_changes,
    "check_changes": Agent.check_changes,
    "apply_changes": Agent.apply_changes,
    "discard_changes": Agent.discard_changes,
    "publish_changes": Agent.publish_changes,
}
if set(_TOOLS) != {spec.name for spec in (*SPECS, *SOURCE_SPECS)}:
    raise ImportError("every tool the model is offered needs a method, and the other way round")
