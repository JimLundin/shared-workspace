"""Copied source can use the installed SDK bundle and native Tk panel."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import runtime
from keeper import copy_source
from runtime import activate

ROOT = Path(__file__).resolve().parent.parent


def test_old_file_association_hands_off_before_loading_dependencies(tmp_path: Path) -> None:
    interpreter = tmp_path / ".python" / "pythonw.exe"
    interpreter.parent.mkdir()
    interpreter.touch()
    arguments = [str(tmp_path / "old" / "pythonw.exe"), str(tmp_path / "toolkit.pyw")]
    environment = dict(os.environ, PYTHONHOME="old Python", PYTHONPATH="old dependencies")
    with (
        patch("runtime.__file__", str(tmp_path / "runtime.py")),
        patch("runtime.sys.platform", "win32"),
        patch("runtime.sys.executable", arguments[0]),
        patch("runtime.sys.orig_argv", arguments),
        patch("runtime.os.environ", environment),
        patch("runtime.subprocess.Popen") as start,
    ):
        try:
            runtime.bootstrap()
        except SystemExit as error:
            assert error.code == 0
        else:
            raise AssertionError("The old interpreter must exit after the handoff.")
        positional = cast("tuple[list[str]]", start.call_args.args)
        options = cast("dict[str, object]", start.call_args.kwargs)
        assert positional[0] == [str(interpreter.resolve()), arguments[1]]
        child_environment = options["env"]
        assert isinstance(child_environment, dict)
        assert "PYTHONHOME" not in child_environment and "PYTHONPATH" not in child_environment
        process = cast(Mock, start.return_value)
        cast(Mock, process.wait).assert_not_called()


def test_bundled_interpreter_does_not_launch_itself_again(tmp_path: Path) -> None:
    interpreter = tmp_path / ".python" / "python.exe"
    interpreter.parent.mkdir()
    interpreter.touch()
    with (
        patch("runtime.__file__", str(tmp_path / "runtime.py")),
        patch("runtime.sys.platform", "win32"),
        patch("runtime.sys.executable", str(interpreter)),
        patch("runtime.subprocess.Popen") as start,
    ):
        runtime.bootstrap()
        start.assert_not_called()


def test_dependency_abi_is_checked_before_native_modules_are_loaded(tmp_path: Path) -> None:
    dependencies = tmp_path / ".runtime"
    dependencies.mkdir()
    (dependencies / "manifest.json").write_text('{"python": "3.99", "platform": "win_amd64"}')
    with patch("runtime.__file__", str(tmp_path / "runtime.py")), patch("runtime.site.addsitedir") as load:
        try:
            runtime.activate()
        except RuntimeError as error:
            assert "Python 3.99" in str(error) and "StartToolkit.cmd" in str(error)
        else:
            raise AssertionError("A different Python ABI must not load the bundled native modules.")
        load.assert_not_called()


def test_copied_source_starts_the_sdk_and_native_panel_with_the_inherited_bundle(tmp_path: Path) -> None:
    inherited = os.environ.get("TOOLKIT_BUNDLED_RUNTIME")
    if not (ROOT / ".runtime").is_dir() and not inherited:
        raise unittest.SkipTest("Windows bundle integration")
    activate()
    source = tmp_path / "source"
    copy_source(ROOT, source)
    assert not (source / ".runtime").exists()
    assert not (source / ".python").exists()
    script = (
        "from runtime import activate; activate(); import assistant.sdk; "
        "from tests.test_windows_agent_panel import test_native_inline_questions_approvals_and_stop_share_one_composer as check; "
        "check(); print('Copied SDK and native panel passed')"
    )
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=source, env=dict(os.environ, PYTHONUTF8="1"),
        capture_output=True, timeout=75, check=False,
    )
    assert checked.returncode == 0, (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert b"Copied SDK and native panel passed" in checked.stdout
