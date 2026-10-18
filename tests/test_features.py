"""What each tool and app key does, against a fake desktop and a fake endpoint."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import cast

from assistant.features import Assistant, pick_window
from assistant.geometry import Shot
from assistant.icons import State
from assistant.llm import RATE, Json, Settings
from protocol import App, Tool
from usage import Detail
from winapi.shapes import Rect, Window

from .fake_endpoint import FakeEndpoint, Reply, fake_endpoint, sse

SPEECH = bytes(2 * RATE)  # a second of silence: long enough to send


def dumps(value: Json) -> str:
    return json.dumps(value)


def said(text: str) -> Reply:
    return Reply(dumps({"text": text}).encode())


def answered(text: str) -> Reply:
    return Reply(dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode())


def streamed(*pieces: str) -> Reply:
    return sse(*(dumps({"choices": [{"delta": {"content": p}}]}) for p in pieces))


class FakeAnswer:
    __slots__ = ("desktop",)

    def __init__(self, desktop: FakeDesktop) -> None:
        self.desktop = desktop

    def write(self, text: str) -> None:
        self.desktop.log.append(f"write {text}")

    def finish(self) -> None:
        self.desktop.log.append("finish")

    def fail(self, message: str) -> None:
        self.desktop.log.append(f"fail {message}")


class FakeDesktop:
    __slots__ = ("follow_up", "log", "prompts", "region", "selection", "speech", "windows_open")

    def __init__(self) -> None:
        self.log: list[str] = []
        self.speech = SPEECH
        self.selection: str | None = None
        self.prompts: list[str | None] = []
        self.region: Rect | None = Rect(0, 0, 100, 50)
        self.windows_open: list[Window] = []
        self.follow_up: Callable[[str], None] | None = None

    def start_listening(self) -> str | None:
        self.log.append("listen")
        return None

    def stop_listening(self) -> bytes:
        self.log.append("stop listening")
        return self.speech

    def copy_selection(self) -> str | None:
        return self.selection

    def paste(self, text: str) -> None:
        self.log.append(f"paste {text}")

    def type_text(self, text: str) -> None:
        self.log.append(f"type {text!r}")

    def set_clipboard(self, text: str) -> None:
        self.log.append(f"clipboard {text}")

    def choose_region(self) -> Rect | None:
        return self.region

    def capture(self, area: Rect, longest: int) -> tuple[bytes, Shot]:
        return b"\x89PNG", Shot(area, area.width, area.height)

    def prompt(self, title: str) -> str | None:
        self.log.append(f"prompt {title}")
        return self.prompts.pop(0) if self.prompts else None

    def answer(self, title: str, follow_up: Callable[[str], None]) -> FakeAnswer:
        self.log.append(f"answer window {title}")
        self.follow_up = follow_up
        return FakeAnswer(self)

    def show_state(self, state: State) -> None:
        self.log.append(f"state {state.name}")

    def notify(self, title: str, text: str) -> None:
        self.log.append(f"notify {title}: {text}")

    def problem(self, message: str) -> None:
        self.log.append(f"problem {message}")

    def windows(self) -> list[Window]:
        return self.windows_open

    def foreground(self) -> int:
        return self.windows_open[0].handle if self.windows_open else 0

    def focus(self, handle: int) -> bool:
        self.log.append(f"focus {handle}")
        return True

    def launch(self, command: str) -> None:
        self.log.append(f"launch {command}")

    def arrange(self, tool: Tool) -> None:
        self.log.append(f"arrange {tool.name.lower()}")


class Rig:
    """An assistant wired to fakes: what it did is in `desktop.log`, `used` and `agent`."""

    __slots__ = ("agent", "assistant", "desktop", "endpoint", "used")

    def __init__(self, endpoint: FakeEndpoint) -> None:
        self.endpoint = endpoint
        self.desktop = FakeDesktop()
        self.used: list[tuple[str, Mapping[str, Detail]]] = []
        self.agent: list[str] = []
        settings = Settings(endpoint.url, "k", "transcribe", "chat", "agent", "", "")
        self.assistant = Assistant(
            self.desktop,
            settings,
            record=lambda feature, details: self.used.append((feature, details)),
            submit=lambda job: job(),
            run_agent=self.agent.append,
            stop_agent=lambda: self.agent.append("STOP"),
        )

    def press(self, request: Tool | App) -> None:
        self.assistant.handle(request, True)
        self.assistant.handle(request, False)

    def hold(self, tool: Tool) -> None:
        """Down, speak, up."""
        self.assistant.handle(tool, True)
        self.assistant.handle(tool, False)

    def take(self) -> list[str]:
        log, self.desktop.log = self.desktop.log, []
        return [entry for entry in log if not entry.startswith("state ")]

    def sent(self, index: int = -1) -> dict[str, object]:
        data: object = json.loads(self.endpoint.received[index].body)
        assert isinstance(data, dict)
        return {str(k): v for k, v in cast("dict[object, object]", data).items()}

    def ok(self, feature: str) -> bool:
        details = dict(self.used)[feature]
        return details.get("ok") is True


# ---- dictation -------------------------------------------------------------------------


def test_dictate_types_what_was_said() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [said("hej på dig\n")]
        rig = Rig(endpoint)
        rig.hold(Tool.DICTATE)
        assert rig.take() == ["listen", "stop listening", "type 'hej på dig '"]
        assert rig.ok("dictate")


def test_dictate_clean_types_the_tidied_text() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [said("um so like the the meeting"), answered("So, the meeting.")]
        rig = Rig(endpoint)
        rig.hold(Tool.DICTATE_CLEAN)
        assert rig.take()[-1] == "type 'So, the meeting. '"
        messages = cast("list[dict[str, object]]", rig.sent()["messages"])
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[1]["content"] == "um so like the the meeting"


def test_dictate_command_types_one_line_and_never_enter() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [said("list big files"), answered("```powershell\nGet-ChildItem |\n Sort-Object Length\n```")]
        rig = Rig(endpoint)
        rig.hold(Tool.DICTATE_COMMAND)
        assert rig.take()[-1] == "type 'Get-ChildItem | Sort-Object Length'"


def test_a_tap_of_a_talk_key_sends_nothing() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint)
        rig.desktop.speech = bytes(100)
        rig.hold(Tool.DICTATE)
        assert rig.take() == ["listen", "stop listening"]
        assert endpoint.received == []


def test_a_second_talk_key_while_listening_is_ignored() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [said("one")]
        rig = Rig(endpoint)
        rig.assistant.handle(Tool.DICTATE, True)
        rig.assistant.handle(Tool.DICTATE_CLEAN, True)
        rig.assistant.handle(Tool.DICTATE_CLEAN, False)
        rig.assistant.handle(Tool.DICTATE, False)
        assert rig.take() == ["listen", "stop listening", "type 'one '"]


def test_endpoint_failures_are_reported_and_logged() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [Reply(b'{"error": {"message": "Invalid session key"}}', status=401)]
        rig = Rig(endpoint)
        rig.hold(Tool.DICTATE)
        assert rig.take()[-1] == "problem HTTP 401: Invalid session key"
        assert dict(rig.used)["dictate"]["ok"] is False


def test_the_tray_shows_listening_then_working_then_ready() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [said("hi")]
        rig = Rig(endpoint)
        rig.hold(Tool.DICTATE)
        states = [entry for entry in rig.desktop.log if entry.startswith("state ")]
        assert states == ["state LISTENING", "state WORKING", "state READY"]


# ---- rewriting -------------------------------------------------------------------------


def test_rewrite_keys_replace_the_selection() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [answered('"Hello, world."')]
        rig = Rig(endpoint)
        rig.desktop.selection = "helo wrld"
        rig.press(Tool.FIX)
        assert rig.take() == ["paste Hello, world."]
        messages = rig.sent()["messages"]
        assert isinstance(messages, list)
        assert cast("list[dict[str, object]]", messages)[1] == {"role": "user", "content": "helo wrld"}
        assert "spelling" in str(cast("list[dict[str, object]]", messages)[0]["content"])


def test_rewrite_without_a_selection_says_so() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint)
        rig.press(Tool.TRANSLATE)
        assert rig.take() == ["problem Select some text first."]
        assert endpoint.received == []


def test_rewrite_by_voice_follows_the_spoken_instruction() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [said("make it sound less annoyed"), answered("Could you take a look?")]
        rig = Rig(endpoint)
        rig.desktop.selection = "Fix this NOW."
        rig.hold(Tool.REWRITE_VOICE)
        assert rig.take()[-1] == "paste Could you take a look?"
        assert "make it sound less annoyed" in json.dumps(rig.sent())


# ---- asking ----------------------------------------------------------------------------


def test_ask_streams_the_answer_and_takes_follow_ups() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [streamed("Ja", "!"), streamed("Nej.")]
        rig = Rig(endpoint)
        rig.desktop.prompts = ["Is it Friday?"]
        rig.press(Tool.ASK)
        assert rig.take() == ["prompt Ask", "answer window Is it Friday?", "write Ja", "write !", "finish"]
        follow_up = rig.desktop.follow_up
        assert follow_up is not None
        follow_up("Tomorrow?")
        assert rig.take() == ["write Nej.", "finish"]
        roles = [m["role"] for m in cast("list[dict[str, object]]", rig.sent()["messages"])]
        assert roles == ["system", "user", "assistant", "user"]


def test_ask_includes_the_selection() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [streamed("ok")]
        rig = Rig(endpoint)
        rig.desktop.prompts = ["What does this mean?"]
        rig.desktop.selection = "ERROR 0x80070005"
        rig.press(Tool.ASK)
        assert "ERROR 0x80070005" in json.dumps(rig.sent())


def test_cancelled_prompts_do_nothing() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint)
        rig.press(Tool.ASK)
        assert rig.take() == ["prompt Ask"]
        assert dict(rig.used)["ask"]["cancelled"] is True


# ---- the screen ------------------------------------------------------------------------


def test_screen_text_copies_the_text_in_the_area() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [answered("Line one\nLine two")]
        rig = Rig(endpoint)
        rig.press(Tool.SCREEN_TEXT)
        assert rig.take() == ["clipboard Line one\nLine two", "notify Text copied: Line one Line two"]
        assert "data:image/png;base64," in json.dumps(rig.sent())


def test_screen_ask_answers_about_the_area() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [streamed("It's a chart.")]
        rig = Rig(endpoint)
        rig.desktop.prompts = [""]  # Enter on an empty box: the default question
        rig.press(Tool.SCREEN_ASK)
        assert rig.take()[-2:] == ["write It's a chart.", "finish"]
        assert "data:image/png;base64," in json.dumps(rig.sent())


def test_screen_tools_do_nothing_when_no_area_is_chosen() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint)
        rig.desktop.region = None
        rig.press(Tool.SCREEN_TEXT)
        assert rig.take() == []


# ---- the agent -------------------------------------------------------------------------


def test_agent_tasks_by_voice_and_typed() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [said("tidy my downloads")]
        rig = Rig(endpoint)
        rig.hold(Tool.AGENT_VOICE)
        rig.desktop.prompts = ["summarise today's mail"]
        rig.press(Tool.AGENT_TYPED)
        rig.press(Tool.STOP)
        assert rig.agent == ["tidy my downloads", "summarise today's mail", "STOP"]


# ---- apps and windows ------------------------------------------------------------------

TERMINAL = App("terminal", "windowsterminal.exe", "wt.exe")


def window(handle: int, exe: str = "windowsterminal.exe", title: str = "PowerShell") -> Window:
    return Window(handle, title, exe, minimized=False)


def test_app_key_focuses_the_app() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint)
        rig.desktop.windows_open = [window(1, "msedge.exe"), window(2)]
        rig.press(TERMINAL)
        assert rig.take() == ["focus 2"]
        assert rig.ok("app:terminal")


def test_app_key_starts_the_app_if_it_isnt_running() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint)
        rig.press(TERMINAL)
        assert rig.take() == ["launch wt.exe"]


def test_pressing_again_goes_through_the_apps_windows() -> None:
    windows = [window(1), window(2, "msedge.exe"), window(3), window(4)]  # front to back
    assert pick_window(windows, TERMINAL, foreground=1) == 4  # the one furthest back: repeated presses visit all
    assert pick_window(windows, TERMINAL, foreground=2) == 1
    assert pick_window([window(1)], TERMINAL, foreground=1) == 1


def test_app_title_narrows_the_windows() -> None:
    teams = App("teams", "msedge.exe", "msedge.exe", title="Teams")
    windows = [window(1, "msedge.exe", "News"), window(2, "msedge.exe", "Chat | Microsoft Teams")]
    assert pick_window(windows, teams, foreground=0) == 2


def test_window_keys_arrange_the_window() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint)
        for tool in (Tool.SNAP_LEFT, Tool.MAXIMIZE, Tool.NEXT_MONITOR):
            rig.press(tool)
        assert rig.take() == ["arrange snap_left", "arrange maximize", "arrange next_monitor"]
