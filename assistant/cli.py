"""`ai`: ask from the terminal.

    ai what does EADDRINUSE mean
    git diff | ai write a commit message      (piped text is context)
    ai -c find files over 100MB here          (suggests a PowerShell command; runs it if you say so)
    ai -m gpt-6-astra explain this regex      (another model)

Runs anywhere Python does; running the suggested command needs Windows (PowerShell).
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, TextIO

import usage
from usage import Detail

from . import prompts
from .llm import Failure, Missing, ask, settings_from, stream, system_message, user_message
from .text import one_line_command

USAGE: Final = "usage: ai [-c] [-m MODEL] QUESTION...\n  -c  suggest a PowerShell command, and run it if you confirm\n  -m  use another model\n"
MAX_PIPED: Final = 100_000
"""Most piped characters sent along."""


def main(
    argv: Sequence[str],
    env: Mapping[str, str],
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    *,
    piped: bool,
    confirm: Callable[[str], bool],
    run: Callable[[str], int],
    record: Callable[[str, Mapping[str, Detail]], None],
) -> int:
    """The command, with everything it touches passed in. Returns the exit code."""
    command_mode, model, words = False, None, list(argv)
    while words and words[0] in ("-c", "--command", "-m", "--model", "-h", "--help"):
        flag = words.pop(0)
        if flag in ("-c", "--command"):
            command_mode = True
        elif flag in ("-m", "--model") and words:
            model = words.pop(0)
        else:
            words = []
    if not words:
        stderr.write(USAGE)
        return 2
    settings = settings_from(env)
    if isinstance(settings, Missing):
        stderr.write(f"ai: set {', '.join(settings.names)} first (see the toolkit's README)\n")
        return 2
    question = " ".join(words)
    context = stdin.read(MAX_PIPED) if piped else ""
    content = f"{question}\n\n{context}" if context else question

    if command_mode:
        where = f"The current folder is {Path.cwd()}.\n\n"
        answer = ask(settings, prompts.COMMAND, where + content, model=model)
        if isinstance(answer, Failure):
            stderr.write(f"ai: {answer.message}\n")
            return 1
        line = one_line_command(answer)
        stdout.write(f"{line}\n")
        stdout.flush()
        record("ai_command", {"ok": True})
        return run(line) if confirm("Run it? [y/N] ") else 1

    def write(piece: str) -> None:
        stdout.write(piece)
        stdout.flush()

    result = stream(settings, [system_message(prompts.ASK), user_message(content)], write, model=model)
    record("ai", {"ok": not isinstance(result, Failure)})
    if isinstance(result, Failure):
        stderr.write(f"ai: {result.message}\n")
        return 1
    stdout.write("\n")
    return 0


def ask_console(question: str) -> bool:
    """Asks on the console itself, even when stdin is a pipe."""
    console = "CONIN$" if sys.platform == "win32" else "/dev/tty"
    sys.stderr.write(question)
    sys.stderr.flush()
    try:
        with open(console, encoding="utf-8") as terminal:
            return terminal.readline().strip().lower() in ("y", "yes", "j", "ja")
    except OSError:
        return False


def run_powershell(command: str) -> int:
    """Runs the command in PowerShell, in this console, in the current folder."""
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], check=False).returncode


def _record(feature: str, details: Mapping[str, Detail]) -> None:
    with contextlib.suppress(OSError):
        usage.record("assistant", feature, details)


def entry() -> None:
    # Under pythonw these may be missing; typeshed types them as possibly anything.
    stdin: TextIO = sys.stdin
    stdout: TextIO = sys.stdout
    stderr: TextIO = sys.stderr
    for stream_ in (stdout, stderr):
        if isinstance(stream_, io.TextIOWrapper):
            stream_.reconfigure(encoding="utf-8", errors="replace")
    code = main(sys.argv[1:], os.environ, stdin, stdout, stderr, piped=not stdin.isatty(), confirm=ask_console, run=run_powershell, record=_record)
    sys.exit(code)
