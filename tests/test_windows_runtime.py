"""Copied source uses the inherited SDK and native Tk from the installed bundle."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

if sys.platform != "win32":
    raise unittest.SkipTest("Windows bundle integration")

from keeper import copy_source
from runtime import activate

ROOT = Path(__file__).resolve().parent.parent


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
