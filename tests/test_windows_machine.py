"""The worker's machine is constructed without a microphone or assistant UI."""

import subprocess
import sys
import threading
import unittest
from pathlib import Path

if sys.platform != "win32":
    raise unittest.SkipTest("Windows machine ownership")

from assistant.agent import Machine
from assistant.browser import BrowserResult
from assistant.geometry import Shot
from assistant.llm import Json
from assistant.machine import WinMachine
from assistant.recording_state import AudioSource
from winapi.shapes import Rect


class Desktop:
    __slots__ = ("owner", "pasted")

    def __init__(self) -> None:
        self.owner = 0
        self.pasted: list[str] = []

    def capture(self, area: Rect, longest: int) -> tuple[bytes, Shot]:
        return b"capture", Shot(area, area.width, area.height)

    def paste(self, text: str) -> None:
        self.pasted.append(text)


class Human:
    __slots__ = ("answers", "requests")

    def __init__(self, answers: tuple[str | None, ...] = ("The project notes",)) -> None:
        self.answers = iter(answers)
        self.requests: list[tuple[str, str, bool, threading.Event | None]] = []

    def agent_input(
        self, title: str, detail: str = "", *, question: bool = False, cancel: threading.Event | None = None,
    ) -> str | None:
        self.requests.append((title, detail, question, cancel))
        return next(self.answers)


class Recording:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def start(self, source: AudioSource = "both", title: str = "Meeting") -> None:
        self.calls.append(("start", source, title))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def retry(self) -> None:
        self.calls.append(("retry",))

    def recent(self) -> str:
        self.calls.append(("list",))
        return "Saved meetings"

    def describe(self) -> str:
        return "Recording owner status"


class Browser:
    __slots__ = ("calls", "failure")

    def __init__(self, failure: OSError | None = None) -> None:
        self.calls: list[str] = []
        self.failure = failure

    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        self.calls.append(operation)
        return BrowserResult("Browser observation", b"browser image")

    def close(self) -> None:
        self.calls.append("close")
        if self.failure is not None:
            raise self.failure


class Teams:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> str:
        self.calls.append(operation)
        return "Teams observation"

    def cancel_sign_in(self) -> None:
        self.calls.append("cancel sign-in")


def test_machine_uses_explicit_human_and_meeting_owners() -> None:
    desktop, human, recording = Desktop(), Human(("The project notes", "approve", None)), Recording()
    machine: Machine = WinMachine(desktop, human, browser=Browser(), teams=Teams(), meetings=recording)
    cancel = threading.Event()
    assert machine.ask_user("Which notes?", cancel=cancel) == "The project notes"
    assert human.requests == [("Which notes?", "", True, cancel)]
    assert machine.confirm("Update notes", "The proposed edit", cancel=cancel)
    assert human.requests[-1] == ("Update notes", "The proposed edit", False, cancel)
    assert not machine.confirm("Update notes", "The proposed edit", cancel=cancel)
    machine.paste_text("A prepared note")
    assert desktop.pasted == ["A prepared note"]
    assert machine.meeting("start", "microphone", "Planning") == "Recording owner status"
    assert machine.meeting("stop") == "Recording owner status"
    assert machine.meeting("retry") == "Recording owner status"
    assert machine.meeting("list") == "Saved meetings"
    assert recording.calls == [("start", "microphone", "Planning"), ("stop",), ("retry",), ("list",)]
    try:
        machine.meeting("start", "invalid", "Planning")
    except OSError as error:
        assert "both or microphone" in str(error)
    else:
        raise AssertionError("Invalid capture sources must not reach the recording owner.")
    assert len(recording.calls) == 4


def test_machine_keeps_browser_and_teams_cleanup_with_their_owners() -> None:
    browser, teams = Browser(OSError("Browser disconnect failed")), Teams()
    machine = WinMachine(Desktop(), Human(), browser=browser, teams=teams)
    cancel = threading.Event()
    assert machine.browse("browser_tabs", {}, cancel) == ("Browser observation", b"browser image")
    assert machine.teams("teams_status", {}, cancel) == "Teams observation"
    try:
        machine.close_background()
    except OSError as error:
        assert str(error) == "Browser disconnect failed"
    else:
        raise AssertionError("A failed disconnect must remain visible.")
    assert browser.calls == ["browser_tabs", "close"]
    assert teams.calls == ["teams_status", "cancel sign-in"]
    try:
        machine.meeting("status")
    except OSError as error:
        assert str(error) == "Meeting recording is unavailable."
    else:
        raise AssertionError("A missing recording owner must be reported.")


def test_machine_import_and_construction_do_not_load_ui_or_audio() -> None:
    program = """
import os
import sys
from runtime import activate
activate()
from assistant.machine import NativeDesktop, WinMachine
from winapi import windows

class Human:
    def agent_input(self, title, detail="", *, question=False, cancel=None):
        return None

owner = windows.create_hidden("toolkit machine construction fixture")
try:
    machine = WinMachine(NativeDesktop(owner), Human())
    assert windows.process_id(owner) == os.getpid()
    assert machine.ask_user("Which notes?") is None
    machine.close_background()
    for name in ("assistant.app", "assistant.desktop", "assistant.ui", "winapi.audio",
                 "winapi.recording", "tkinter", "PySide6"):
        assert name not in sys.modules, name
finally:
    windows.destroy(owner)
assert not windows.exists(owner)
print("Constructed without UI or audio.")
"""
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=Path(__file__).resolve().parent.parent,
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Constructed without UI or audio."
