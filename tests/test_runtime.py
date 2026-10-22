"""Interpreter handoff and dependency ABI validation, without native startup."""

import os
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import runtime


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
