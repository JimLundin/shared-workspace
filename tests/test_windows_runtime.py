"""Copied source uses the inherited SDK and native Tk from the installed bundle."""

import os
import socket
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

if sys.platform != "win32":
    raise unittest.SkipTest("Windows bundle integration")

from keeper import copy_source
from runtime import LaunchMode, Relaunch, activate, execute_bootstrap

ROOT = Path(__file__).resolve().parent.parent


def test_windowed_handoff_returns_while_the_child_is_still_running() -> None:
    code = (
        "import socket,sys; "
        "channel=socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=5); channel.recv(1); channel.close()"
    )
    with socket.create_server(("127.0.0.1", 0)) as server, ThreadPoolExecutor(max_workers=1) as worker:
        server.settimeout(5)
        port = cast(tuple[str, int], server.getsockname())[1]
        plan = Relaunch((sys.executable, "-c", code, str(port)), tuple(os.environ.items()), LaunchMode.WINDOWED)
        pending = worker.submit(execute_bootstrap, plan)
        channel, _peer = server.accept()
        with channel:
            channel.settimeout(5)
            try:
                with unittest.TestCase().assertRaises(SystemExit) as exited:
                    pending.result(timeout=2)
                assert exited.exception.code == 0
            finally:
                channel.sendall(b"x")
            assert channel.recv(1) == b"", "The child must exit after its work is released."


def test_copied_source_imports_the_sdk_and_starts_tk_with_the_inherited_bundle(tmp_path: Path) -> None:
    if not (ROOT / ".runtime").is_dir() and not os.environ.get("TOOLKIT_BUNDLED_RUNTIME"):
        raise unittest.SkipTest("Windows bundle integration")
    activate()
    source = tmp_path / "source"
    copy_source(ROOT, source)
    assert not (source / ".runtime").exists() and not (source / ".python").exists()
    script = (
        "from runtime import activate; activate(); import assistant.sdk, assistant.agent_panel, tkinter; "
        "root=tkinter.Tk(); root.withdraw(); root.update(); root.destroy(); print('Copied SDK and Tk passed')"
    )
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=source, env=dict(os.environ, PYTHONUTF8="1"),
        capture_output=True, timeout=75, check=False,
    )
    assert checked.returncode == 0, (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert b"Copied SDK and Tk passed" in checked.stdout
