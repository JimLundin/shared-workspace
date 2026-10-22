"""Run commands without blocking Stop or keeping unbounded output in memory."""

import base64
import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path

OUTPUT_LIMIT = 20_000


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Stop a command and its descendants, including a program launched by a shell."""
    if sys.platform == "win32":
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10,
                creationflags=0x0800_0000, check=False,
            )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def run(
    arguments: Sequence[str], *, input_text: str = "", cwd: str | None = None,
    timeout_s: float = 120, cancel: threading.Event | None = None,
) -> tuple[int, str]:
    if cancel is not None and cancel.is_set():
        return -1, "Stopped by the user."
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
        source.write(input_text.encode("utf-8"))
        source.seek(0)
        process = subprocess.Popen(
            arguments, stdin=source, stdout=output, stderr=subprocess.STDOUT,
            cwd=str(Path(cwd).expanduser()) if cwd else None,
            creationflags=0x0800_0000 if os.name == "nt" else 0, start_new_session=os.name != "nt",
        )
        deadline = time.monotonic() + timeout_s
        interrupted = ""
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    interrupted = "Stopped by the user."
                    break
                if time.monotonic() >= deadline:
                    interrupted = f"Timed out after {timeout_s:g} seconds; inspect the result before retrying."
                    break
                if cancel is not None:
                    cancel.wait(.05)
                else:
                    time.sleep(.05)
        finally:
            if process.poll() is None:
                _terminate(process)
        size = output.tell()
        output.seek(0)
        first = output.read(OUTPUT_LIMIT * 4)
        text = first.decode("utf-8", errors="replace")
        if size > len(first) or len(text) > OUTPUT_LIMIT:
            output.seek(max(0, size - OUTPUT_LIMIT * 4))
            ending = output.read().decode("utf-8", errors="replace")
            text = text[:OUTPUT_LIMIT // 2] + "\n[output truncated]\n" + ending[-OUTPUT_LIMIT // 2:]
        return (-1, interrupted + "\n" + text) if interrupted else (process.wait(), text)


def powershell(
    command: str, *, cwd: str | None = None, timeout_s: float = 120, cancel: threading.Event | None = None,
) -> tuple[int, str]:
    # The script travels through stdin, so quotes, Unicode and large scripts do
    # not depend on Windows command-line escaping or its length limit.
    wrapper = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$global:LASTEXITCODE = 0
try {
    & ([ScriptBlock]::Create([Console]::In.ReadToEnd())) 3>&1 4>&1 5>&1 6>&1 |
        Out-String -Stream -Width 240 | ForEach-Object { [Console]::WriteLine($_) }
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""
    encoded = base64.b64encode(wrapper.encode("utf-16-le")).decode("ascii")
    return run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-EncodedCommand", encoded],
        input_text=command, cwd=cwd, timeout_s=timeout_s, cancel=cancel,
    )
