"""The pinned archive excludes Tcl/Tk while retaining Python and Qt dependencies."""

import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.bundle import WEB_ENGINE_PROCESS, extract, release


def test_web_renderer_child_is_bundled_without_unrelated_wheel_executables(tmp_path: Path) -> None:
    archive = tmp_path / "webengine.whl"
    members = (WEB_ENGINE_PROCESS, "PySide6/Qt6WebEngineCore.dll", "PySide6/designer.exe", "other/QtWebEngineProcess.exe")
    with zipfile.ZipFile(archive, "w") as output:
        for name in members:
            output.writestr(name, name.encode())
    folder = tmp_path / "runtime"
    extract(archive, folder, dependency=True)
    assert (folder / WEB_ENGINE_PROCESS).is_file()
    assert (folder / members[1]).is_file()
    assert not (folder / members[2]).exists()
    assert not (folder / members[3]).exists()


def test_runtime_exclusions_remove_modules_and_binaries_without_dropping_other_python_files(tmp_path: Path) -> None:
    archive = tmp_path / "python.zip"
    removed = ("Lib/tkinter/__init__.py", "Lib/idlelib/idle.py", "DLLs/_tkinter.pyd", "DLLs/tcl90.dll", "DLLs/tcl9tk90.dll")
    kept = ("pythonw.exe", "Lib/asyncio/__init__.py", "DLLs/_ssl.pyd")
    with zipfile.ZipFile(archive, "w") as output:
        for name in (*removed, *kept):
            output.writestr(name, name.encode())
    folder = tmp_path / "runtime"
    extract(archive, folder, exclude=release().exclude)
    assert all(not (folder / name).exists() for name in removed)
    assert all((folder / name).read_text() == name for name in kept)


def test_pin_rejects_non_relative_exclusions_at_the_input_boundary(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements"
    requirements.mkdir()
    pin = {"version": "3.15.1", "url": "https://example.com/python.zip", "sha256": "a" * 64}
    for invalid in ("../outside", "/absolute", "C:/drive", "Lib\\tkinter", "Lib/../python.exe", "."):
        data: dict[str, str | list[str]] = {**pin, "exclude": [invalid]}
        (requirements / "python-win64.json").write_text(json.dumps(data))
        with patch("scripts.bundle.ROOT", tmp_path), unittest.TestCase().assertRaises(ValueError):
            release()
