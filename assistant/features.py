"""What each tool and app key does. Everything that touches Windows goes through a
`Desktop`, so the behaviour can be tested without Windows (like the layout's
engine and its `Host`).

Requests arrive on the thread that receives the layout's messages, and anything
quick happens right there: the microphone starts the moment a talk key goes down,
even while an earlier dictation is still being transcribed. Anything slow is
handed to the worker thread (`submit`), which does one job at a time, in order.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from enum import Enum, auto
from typing import Final, Protocol

from config import REWRITES
from protocol import App, Request, Tool
from usage import Detail
from winapi.shapes import Rect, Window

from . import prompts
from .geometry import Shot
from .icons import State
from .llm import (
    RATE,
    Failure,
    Image,
    Message,
    Missing,
    Part,
    Settings,
    Text,
    ask,
    assistant_message,
    long_enough,
    stream,
    system_message,
    transcribe,
    user_message,
    wav,
)
from .text import one_line_command, typed_text, unwrap


class Answer(Protocol):
    """A window showing an answer as it's written, with a box for follow-up questions."""

    def write(self, text: str) -> None: ...
    def finish(self) -> None:
        """The answer is complete; follow-up questions can be asked."""

    def fail(self, message: str) -> None: ...


class Desktop(Protocol):
    """What the assistant needs from Windows and its windows."""

    def start_listening(self) -> str | None:
        """Starts recording; a problem's description, or None."""

    def stop_listening(self) -> bytes:
        """Stops recording; 16-bit mono PCM at `RATE`."""
        ...

    def copy_selection(self) -> str | None:
        """The selected text, if any. Leaves the clipboard as it was."""

    def paste(self, text: str) -> None:
        """Replaces the selection (or types at the cursor), then restores the clipboard."""

    def type_text(self, text: str) -> None: ...
    def set_clipboard(self, text: str) -> None: ...
    def choose_region(self) -> Rect | None:
        """Lets the user drag over part of the screen; None if they press Escape."""

    def capture(self, area: Rect, longest: int) -> tuple[bytes, Shot]:
        """A PNG of the area, no bigger than `longest` pixels on its longer side."""
        ...

    def prompt(self, title: str) -> str | None:
        """A text box; what was typed, or None if cancelled."""

    def answer(self, title: str, follow_up: Callable[[str], None]) -> Answer: ...
    def show_state(self, state: State) -> None: ...
    def notify(self, title: str, text: str) -> None: ...
    def problem(self, message: str) -> None:
        """Something went wrong that the user should hear about."""

    def windows(self) -> list[Window]:
        """Windows on the taskbar, front to back."""
        ...

    def foreground(self) -> int: ...
    def focus(self, handle: int) -> bool: ...
    def launch(self, command: str) -> None: ...
    def arrange(self, tool: Tool) -> None:
        """Snap, maximize, minimize or move the front window (a window tool)."""


Recorder = Callable[[str, Mapping[str, Detail]], None]
"""Writes one use of a feature to the usage log."""


class Outcome(Enum):
    DONE = auto()
    CANCELLED = auto()
    """The user backed out (Escape in a box, no area chosen)."""


HOLD_TOOLS: Final = frozenset({Tool.DICTATE, Tool.DICTATE_CLEAN, Tool.DICTATE_COMMAND, Tool.REWRITE_VOICE, Tool.AGENT_VOICE})
"""Hold the key and speak: these listen until it's released."""
WINDOW_TOOLS: Final = frozenset({Tool.SNAP_LEFT, Tool.SNAP_RIGHT, Tool.MAXIMIZE, Tool.MINIMIZE, Tool.NEXT_MONITOR})
SCREENSHOT_SIZE: Final = 2048
"""Longest side of screenshots sent to the model, in pixels."""


def pick_window(windows: Sequence[Window], app: App, foreground: int) -> int | None:
    """The app's window to bring forward: its front one; or, if one of its windows is
    already in front, its rearmost, so pressing again goes through all of them."""
    mine = [w.handle for w in windows if w.exe == app.exe.lower() and app.title.lower() in w.title.lower()]
    if not mine:
        return None
    return mine[-1] if foreground in mine else mine[0]


def submit_voice_text(text: str, send: Callable[[str], None], details: dict[str, Detail]) -> None:
    """Measure submission, without retaining content or claiming the app displayed it."""
    details["requested_characters"] = len(text)
    started = time.monotonic()
    try:
        send(text)
        details["submitted_characters"] = len(text)
    finally:
        details["submit_seconds"] = round(time.monotonic() - started, 3)


class Conversation:
    """A question and its follow-ups, answered in one window."""

    __slots__ = ("messages", "settings", "window")

    def __init__(self, settings: Settings, first: Sequence[Part], title: str, desktop: Desktop, submit: Callable[[Callable[[], None]], None]) -> None:
        self.settings = settings
        self.messages: tuple[Message, ...] = (system_message(prompts.ASK), user_message(first))
        self.window = desktop.answer(title, lambda question: submit(lambda: self.follow_up(question)))

    def run(self) -> Failure | Outcome:
        result = stream(self.settings, self.messages, self.window.write)
        if isinstance(result, Failure):
            self.window.fail(result.message)
            return result
        self.messages = (*self.messages, assistant_message(result))
        self.window.finish()
        return Outcome.DONE

    def follow_up(self, question: str) -> None:
        self.messages = (*self.messages, user_message(question))
        self.run()


class Assistant:
    """Turns requests from the layout's tool and app keys into actions."""

    __slots__ = (
        "desktop", "layout_on", "listening", "listening_started", "missing", "record", "run_agent", "settings", "show_agent", "stop_agent", "submit",
    )

    def __init__(
        self,
        desktop: Desktop,
        settings: Settings,
        *,
        record: Recorder,
        submit: Callable[[Callable[[], None]], None],
        run_agent: Callable[[str], None],
        show_agent: Callable[[], None],
        stop_agent: Callable[[], None],
        missing: Missing | None = None,
    ) -> None:
        self.desktop = desktop
        self.settings = settings
        self.record = record
        self.submit = submit
        """Runs a job on the worker thread, after whatever is already queued."""
        self.run_agent = run_agent
        self.show_agent = show_agent
        self.stop_agent = stop_agent
        self.listening: Tool | None = None
        """The talk key being held, if any."""
        self.listening_started = 0.0
        self.layout_on = True
        self.missing = missing
        """Settings the AI tools need but don't have; the other keys work without them."""

    # ---- requests ----------------------------------------------------------------------

    def handle(self, request: Request, down: bool) -> None:
        """A tool or app key went down or up. Returns quickly; slow work is submitted."""
        if self.missing is not None and isinstance(request, Tool) and request not in WINDOW_TOOLS and request is not Tool.STOP:
            if down:
                self.desktop.problem(f"Set {', '.join(self.missing.names)} to use the AI tools (see the README).")
            return
        match request:
            case App():
                if down:
                    self.timed(f"app:{request.name}", lambda: self.switch_to(request))
            case Tool.STOP:
                if down:
                    self.stop_agent()
            case Tool() if request in HOLD_TOOLS:
                self.hold(request, down)
            case Tool() if request in WINDOW_TOOLS:
                if down:
                    self.timed(request.name.lower(), lambda: self.arrange(request))
            case Tool():
                if down:
                    self.submit(lambda: self.timed(request.name.lower(), lambda: self.act(request)))

    def idle(self) -> None:
        self.desktop.show_state(State.READY if self.layout_on else State.LAYOUT_OFF)

    def timed(self, feature: str, job: Callable[[], Failure | Outcome], *, extra: Mapping[str, Detail] | None = None) -> None:
        """Runs a job, reports a failure, and logs the use (never the content)."""
        started = time.monotonic()
        outcome: Failure | Outcome = Failure("crashed")
        try:
            outcome = job()
        finally:
            details: dict[str, Detail] = {**(extra or {}), "ok": not isinstance(outcome, Failure), "seconds": round(time.monotonic() - started, 2)}
            if isinstance(outcome, Failure):
                details["error"] = outcome.message[:120]
                self.desktop.problem(outcome.message)
            elif outcome is Outcome.CANCELLED:
                details["cancelled"] = True
            self.record(feature, details)
            if self.listening is None:
                self.idle()

    def hold(self, tool: Tool, down: bool) -> None:
        if down:
            if self.listening is not None:
                return
            if (problem := self.desktop.start_listening()) is not None:
                self.desktop.problem(problem)
                self.record(tool.name.lower(), {"ok": False, "error": problem[:120]})
                return
            self.listening = tool
            self.listening_started = time.monotonic()
            self.desktop.show_state(State.LISTENING)
        elif tool is self.listening:
            held_seconds = time.monotonic() - self.listening_started
            self.listening = None
            try:
                pcm = self.desktop.stop_listening()
            except OSError as error:
                self.desktop.problem(str(error))
                self.record(tool.name.lower(), {"ok": False, "error": str(error)[:120]})
                self.idle()
                return
            if not long_enough(pcm):
                self.idle()
                return
            self.desktop.show_state(State.WORKING)
            lengths: dict[str, Detail] = {"held_seconds": round(held_seconds, 2), "audio_seconds": round(len(pcm) / (RATE * 2), 2)}
            self.submit(lambda: self.timed(tool.name.lower(), lambda: self.heard(tool, pcm, lengths), extra=lengths))

    # ---- the tools ---------------------------------------------------------------------

    def heard(self, tool: Tool, pcm: bytes, details: dict[str, Detail]) -> Failure | Outcome:
        started = time.monotonic()
        try:
            transcript = transcribe(self.settings, wav(pcm, RATE))
        finally:
            details["transcribe_seconds"] = round(time.monotonic() - started, 3)
        if isinstance(transcript, Failure):
            return transcript
        details["transcript_characters"] = len(transcript)
        match tool:
            case Tool.DICTATE:
                submit_voice_text(typed_text(transcript), self.desktop.type_text, details)
            case Tool.DICTATE_CLEAN:
                if isinstance(cleaned := ask(self.settings, prompts.CLEAN_DICTATION, transcript), Failure):
                    return cleaned
                submit_voice_text(typed_text(cleaned), self.desktop.type_text, details)
            case Tool.DICTATE_COMMAND:
                if isinstance(command := ask(self.settings, prompts.COMMAND, transcript), Failure):
                    return command
                submit_voice_text(one_line_command(command), self.desktop.type_text, details)
            case Tool.REWRITE_VOICE:
                return self.rewrite(transcript)
            case Tool.AGENT_VOICE:
                submit_voice_text(transcript, self.run_agent, details)
            case _:
                raise AssertionError(f"{tool} doesn't listen")
        return Outcome.DONE

    def arrange(self, tool: Tool) -> Outcome:
        self.desktop.arrange(tool)
        return Outcome.DONE

    def act(self, tool: Tool) -> Failure | Outcome:
        if tool in REWRITES:
            return self.rewrite(REWRITES[tool])
        match tool:
            case Tool.ASK:
                return self.ask()
            case Tool.SCREEN_TEXT:
                return self.screen_text()
            case Tool.SCREEN_ASK:
                return self.screen_ask()
            case Tool.AGENT_TYPED:
                self.show_agent()
                return Outcome.DONE
            case _:
                raise AssertionError(f"{tool} isn't a tool that acts on press")

    def rewrite(self, instruction: str) -> Failure | Outcome:
        self.desktop.show_state(State.WORKING)
        if not (text := self.desktop.copy_selection()):
            return Failure("Select some text first.")
        if isinstance(result := ask(self.settings, prompts.rewrite(instruction), text), Failure):
            return result
        self.desktop.paste(unwrap(result))
        return Outcome.DONE

    def ask(self) -> Failure | Outcome:
        selection = self.desktop.copy_selection()
        if not (question := self.desktop.prompt("Ask")):
            return Outcome.CANCELLED
        self.desktop.show_state(State.WORKING)
        content = f"{question}\n\nSelected text:\n{selection}" if selection else question
        return Conversation(self.settings, [Text(content)], question, self.desktop, self.submit).run()

    def screenshot(self) -> bytes | None:
        area = self.desktop.choose_region()
        return None if area is None else self.desktop.capture(area, SCREENSHOT_SIZE)[0]

    def screen_text(self) -> Failure | Outcome:
        if (png := self.screenshot()) is None:
            return Outcome.CANCELLED
        self.desktop.show_state(State.WORKING)
        if isinstance(result := ask(self.settings, prompts.SCREEN_TEXT, [Image(png)]), Failure):
            return result
        text = unwrap(result)
        self.desktop.set_clipboard(text)
        self.desktop.notify("Text copied", " ".join(text.split())[:80])
        return Outcome.DONE

    def screen_ask(self) -> Failure | Outcome:
        if (png := self.screenshot()) is None:
            return Outcome.CANCELLED
        if (question := self.desktop.prompt("Ask about this (Enter: explain it)")) is None:
            return Outcome.CANCELLED
        self.desktop.show_state(State.WORKING)
        question = question or prompts.SCREEN_QUESTION
        return Conversation(self.settings, [Text(question), Image(png)], question, self.desktop, self.submit).run()

    def switch_to(self, app: App) -> Failure | Outcome:
        foreground = self.desktop.foreground()
        handle = pick_window(self.desktop.windows(), app, foreground)
        if handle is None:
            self.desktop.launch(app.command)
        elif handle != foreground and not self.desktop.focus(handle):
            return Failure(f"Windows wouldn't bring {app.name} to the front.")
        return Outcome.DONE
