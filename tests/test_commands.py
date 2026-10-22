"""Command execution preserves data, bounds output, and responds to Stop."""

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from assistant.commands import OUTPUT_LIMIT, run


def test_command_input_and_working_directory_survive_quotes_unicode_and_long_scripts(tmp_path: Path) -> None:
    text = 'Åäö "quoted" $value `literal`\n' * 2000
    code, output = run(
        [sys.executable, "-c", "import os, sys; value=sys.stdin.buffer.read().decode('utf-8'); print(os.getcwd()); print(len(value))"],
        input_text=text, cwd=str(tmp_path),
    )
    assert code == 0
    assert output.splitlines() == [str(tmp_path), str(len(text))]


def test_command_exit_status_and_truncated_output_preserve_the_ending() -> None:
    code, output = run([sys.executable, "-c", "import sys; print('start' + 'x'*100000 + 'THE END'); sys.exit(7)"])
    assert code == 7 and output.startswith('start') and output.rstrip().endswith('THE END')
    assert "[output truncated]" in output and len(output) < OUTPUT_LIMIT + 100


def test_stopped_command_does_not_launch_and_timeout_ends_running_work(tmp_path: Path) -> None:
    cancelled = threading.Event()
    cancelled.set()
    marker = tmp_path / "never-created"
    code, output = run([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)], cancel=cancelled)
    assert code != 0 and "Stopped" in output and not marker.exists()
    before = time.monotonic()
    code, output = run([sys.executable, "-c", "import time; time.sleep(60)"], timeout_s=.1)
    assert code != 0 and "Timed out" in output
    assert time.monotonic() - before < 12


def test_stop_terminates_the_command_and_its_running_child(tmp_path: Path) -> None:
    ready, late = tmp_path / "ready", tmp_path / "late"
    child = (
        "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); "
        "time.sleep(2); pathlib.Path(sys.argv[2]).touch(); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]], "
        "creationflags=0x08000000 if sys.platform=='win32' else 0); time.sleep(60)"
    )
    cancelled = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(run, [sys.executable, "-c", parent, child, str(ready), str(late)], cancel=cancelled)
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert ready.exists(), "The fixture child never started."
        finally:
            cancelled.set()
        code, output = result.result(timeout=15)
    assert code != 0 and "Stopped" in output
    time.sleep(2.1)
    assert not late.exists(), "Stop left the command's child running."
