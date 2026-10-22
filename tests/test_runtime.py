"""Interpreter handoff decisions and real activation, without process-global patches."""

import json
import os
import sys
from pathlib import Path
from typing import cast
from unittest import TestCase

from runtime import (
    Continue,
    InvalidBundle,
    LaunchMode,
    PythonBundle,
    Relaunch,
    activate,
    bootstrap_plan,
    execute_bootstrap,
    validate_dependencies,
)


def test_old_file_association_hands_off_before_loading_dependencies(tmp_path: Path) -> None:
    folder = tmp_path / ".python"
    executable = tmp_path / "old" / "pythonw.exe"
    arguments: tuple[str, ...] = (str(executable), "-X", "utf8", str(tmp_path / "toolkit.pyw"), "an argument with spaces")
    environment = {"PYTHONHOME": "old Python", "PYTHONPATH": "old dependencies", "PATH": "system tools", "TOOLKIT_SETTING": "preserved"}
    plan = bootstrap_plan("win32", executable, arguments, environment, PythonBundle(folder, frozenset({"pythonw.exe"})))
    assert isinstance(plan, Relaunch)
    assert plan.command == (str(folder / "pythonw.exe"), *arguments[1:]) and plan.mode is LaunchMode.WINDOWED
    assert dict(plan.environment) == {"PATH": "system tools", "TOOLKIT_SETTING": "preserved"}
    assert environment["PYTHONHOME"] == "old Python", "Planning must not edit the caller's environment."


def test_current_interpreter_and_unbundled_sources_continue_without_a_handoff(tmp_path: Path) -> None:
    executable = tmp_path / ".python" / "python.exe"
    bundle = PythonBundle(executable.parent, frozenset({"python.exe"}))
    assert bootstrap_plan("win32", executable, (), {}, bundle) is Continue.CURRENT
    assert bootstrap_plan("win32", tmp_path / "old.exe", (), {}, None) is Continue.CURRENT
    assert bootstrap_plan("linux", tmp_path / "python", (), {}, bundle) is Continue.CURRENT
    execute_bootstrap(Continue.CURRENT)


def test_incomplete_bundles_report_the_missing_interpreter(tmp_path: Path) -> None:
    for current, files in (("python.exe", frozenset({"pythonw.exe"})), ("pythonw.exe", frozenset({"python.exe"}))):
        plan = bootstrap_plan("win32", tmp_path / current, (), {}, PythonBundle(tmp_path / ".python", files))
        assert isinstance(plan, InvalidBundle)
        with TestCase().assertRaisesRegex(RuntimeError, "incomplete"):
            execute_bootstrap(plan)


def test_console_handoff_preserves_the_childs_exit_code_and_environment(tmp_path: Path) -> None:
    result = tmp_path / "environment.json"
    code = (
        "import json,os,sys; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(json.dumps({'setting':os.environ['TOOLKIT_BOOTSTRAP_TEST'],'home':os.environ.get('PYTHONHOME')})); "
        "sys.exit(7)"
    )
    environment = {name: value for name, value in os.environ.items() if name not in ("PYTHONHOME", "PYTHONPATH")}
    environment["TOOLKIT_BOOTSTRAP_TEST"] = "preserved"
    plan = Relaunch((sys.executable, "-c", code, str(result)), tuple(environment.items()), LaunchMode.CONSOLE)
    with TestCase().assertRaises(SystemExit) as exited:
        execute_bootstrap(plan)
    assert exited.exception.code == 7
    assert cast(object, json.loads(result.read_text())) == {"setting": "preserved", "home": None}


def test_dependency_abi_is_checked_before_native_modules_are_loaded(tmp_path: Path) -> None:
    dependencies = tmp_path / ".runtime"
    dependencies.mkdir()
    (dependencies / "manifest.json").write_text('{"python": "3.99", "platform": "win_amd64"}')
    search_path, environment = tuple(sys.path), dict(os.environ)
    with TestCase().assertRaisesRegex(RuntimeError, "Python 3.99.*StartToolkit.cmd"):
        activate(dependencies)
    assert tuple(sys.path) == search_path and dict(os.environ) == environment


def test_manifest_requires_the_declared_python_and_windows_abi() -> None:
    invalid: tuple[object, ...] = (None, [1], dict[str, str](), {"python": "3"}, {"python": "3.x"}, {"python": 3.15})
    for metadata in invalid:
        with TestCase().assertRaisesRegex(RuntimeError, "no valid Python version"):
            validate_dependencies(metadata, "win32", (3, 15), 8)
    for platform, version, size in (("linux", (3, 15), 8), ("win32", (3, 12), 8), ("win32", (3, 15), 4)):
        with TestCase().assertRaisesRegex(RuntimeError, "Windows x64 Python 3.15"):
            validate_dependencies({"python": "3.15"}, platform, version, size)
    validate_dependencies({"python": "3.15"}, "win32", (3, 15), 8)
