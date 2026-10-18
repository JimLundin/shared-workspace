"""The `ai` terminal command, against a fake endpoint."""

from __future__ import annotations

import io
import json

from assistant.cli import main
from assistant.llm import Json

from .fake_endpoint import FakeEndpoint, Reply, fake_endpoint, sse


def dumps(value: Json) -> str:
    return json.dumps(value)


def streamed(*pieces: str) -> Reply:
    return sse(*(dumps({"choices": [{"delta": {"content": p}}]}) for p in pieces))


def answered(text: str) -> Reply:
    return Reply(dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode())


class Terminal:
    """What the command reads and writes, and what it would run."""

    __slots__ = ("asked", "err", "input", "out", "ran", "yes")

    def __init__(self, piped: str | None = None, *, yes: bool = False) -> None:
        self.input = io.StringIO(piped or "")
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.yes = yes
        self.asked: list[str] = []
        self.ran: list[str] = []

    def confirm(self, question: str) -> bool:
        self.asked.append(question)
        return self.yes

    def run(self, command: str) -> int:
        self.ran.append(command)
        return 3

    def call(self, endpoint: FakeEndpoint | None, *argv: str, piped: bool = False) -> int:
        env = {"OPENAI_BASE_URL": endpoint.url, "OPENAI_API_KEY": "k"} if endpoint else {}
        return main(list(argv), env, self.input, self.out, self.err, piped=piped, confirm=self.confirm, run=self.run, record=lambda *_: None)


def sent(endpoint: FakeEndpoint) -> str:
    return endpoint.last.body.decode()


def test_ai_streams_the_answer() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [streamed("A mutex ", "is a lock.")]
        t = Terminal()
        assert t.call(endpoint, "what", "is", "a", "mutex") == 0
        assert t.out.getvalue() == "A mutex is a lock.\n"
        assert "what is a mutex" in sent(endpoint)


def test_piped_input_is_context() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [streamed("Fix the typo.")]
        t = Terminal("diff --git a/x b/x\n+helo\n")
        t.call(endpoint, "write", "a", "commit", "message", piped=True)
        body = sent(endpoint)
        assert "write a commit message" in body
        assert "+helo" in body


def test_command_mode_shows_the_command_and_runs_it_only_if_confirmed() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [answered("```powershell\nGet-ChildItem -Recurse | Where-Object Length -gt 100MB\n```")]
        no = Terminal()
        assert no.call(endpoint, "-c", "files", "over", "100MB") == 1
        assert no.out.getvalue() == "Get-ChildItem -Recurse | Where-Object Length -gt 100MB\n"
        assert no.ran == []
        yes = Terminal(yes=True)
        assert yes.call(endpoint, "-c", "files", "over", "100MB") == 3  # the command's exit code
        assert yes.ran == ["Get-ChildItem -Recurse | Where-Object Length -gt 100MB"]
        assert yes.asked == ["Run it? [y/N] "]


def test_model_can_be_chosen() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [streamed("ok")]
        Terminal().call(endpoint, "-m", "big-model", "hi")
        assert '"model": "big-model"' in sent(endpoint)


def test_problems_go_to_stderr_with_a_failing_exit_code() -> None:
    t = Terminal()
    assert t.call(None, "hi") == 2
    assert "OPENAI_BASE_URL" in t.err.getvalue()
    with fake_endpoint() as endpoint:
        endpoint.replies = [Reply(b'{"error": {"message": "quota exceeded"}}', status=429)]
        t = Terminal()
        assert t.call(endpoint, "hi") == 1
        assert t.err.getvalue() == "ai: HTTP 429: quota exceeded\n"


def test_help_without_a_question() -> None:
    t = Terminal()
    assert t.call(None) == 2
    assert "usage: ai" in t.err.getvalue()
