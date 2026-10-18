"""End to end on Windows: the toolkit changing itself. On a copy of the toolkit, the
keeper installs a good change and the new code runs; undoes a change that crashes
on start; and refuses a change that fails its checks.

Takes a few minutes and uses the keyboard: `TOOLKIT_E2E=1 pytest tests/test_windows_self_change_e2e.py`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

if sys.platform != "win32" or os.environ.get("TOOLKIT_E2E") != "1":
    raise unittest.SkipTest("end-to-end tests run on Windows, with TOOLKIT_E2E=1")

from assistant.workspace import Workspace
from keeper import ask, copy_source
from protocol import MAGIC
from winapi import inputs, screen, windows

from .fake_endpoint import fake_endpoint
from .test_windows_assistant_e2e import ROOT, both_ready, logs, toolkit, wait_for


def keeper_log(home: Path) -> str:
    path = home / "toolkit" / "keeper.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def change(live: Path, home: Path, summary: str, edit: str, old: str, new: str) -> None:  # noqa: PLR0917
    """What the agent does: edit the workspace, then ask the keeper to install it."""
    workspace = Workspace(live, home / "toolkit" / "workspace")
    workspace.reset()
    workspace.replace(edit, old, new)
    ask("install", summary, folder=home / "toolkit" / "keeper" / "requests")
    assert wait_for(lambda: windows.find("Toolkit update review") is not None, lambda: None, 30), keeper_log(home)
    hwnd = windows.find("Toolkit update review")
    assert hwnd is not None
    rect = windows.outer_rect(hwnd)
    # Dedicated desktop test: click the keeper-owned approval button.
    inputs.click(rect.right - 90, rect.bottom - 25, "left", 1, screen.virtual_screen(), MAGIC)


def test_the_toolkit_changes_itself_and_undoes_what_fails() -> None:
    home, live = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "toolkit"
    copy_source(ROOT, live)
    try:
        with fake_endpoint() as endpoint, toolkit(endpoint, home, live):
            config = live / "config.py"

            # A good change: installed, and the programs restart with it.
            change(live, home, "a new snippet", "config.py", '",,thx": "Thanks!",', '",,thx": "Thanks!",\n    ",,e2e": "end to end",')
            assert wait_for(lambda: '",,e2e"' in config.read_text(encoding="utf-8") and both_ready(home), lambda: None, 240), keeper_log(home)
            assert len(list((home / "toolkit" / "versions").iterdir())) == 1  # the replaced version, kept
            assert wait_for(lambda: "on trial: a new snippet" in keeper_log(home), lambda: None, 60), keeper_log(home)

            # A change that passes its checks but crashes on start: undone by itself.
            main = live / "assistant" / "__main__.py"
            change(live, home, "a broken start", "assistant/__main__.py", "def main() -> None:\n", "def main() -> None:\n    sys.exit(3)\n")
            assert wait_for(lambda: "Update undone: it didn't start." in keeper_log(home), lambda: None, 300), keeper_log(home)
            assert "sys.exit(3)" not in main.read_text(encoding="utf-8")
            assert wait_for(lambda: both_ready(home), lambda: None, 60), keeper_log(home)
            assert '",,e2e"' in config.read_text(encoding="utf-8")  # back to the good change, not further

            # A change that fails its checks: never installed.
            change(live, home, "a syntax error", "config.py", "APPS: Final", "APPS Final")
            assert wait_for(lambda: "Not installed: the change failed its checks." in keeper_log(home), lambda: None, 300), keeper_log(home)
            assert "APPS: Final" in config.read_text(encoding="utf-8")
    except AssertionError:
        print(logs(home))
        raise
