"""Start with Toolkit's private Python and activate its matching dependencies."""

import json
import os
import site
import struct
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import assert_never, cast


@dataclass(frozen=True, slots=True)
class PythonBundle:
    folder: Path
    files: frozenset[str]


class Continue(Enum):
    CURRENT = auto()


class LaunchMode(Enum):
    CONSOLE = auto()
    WINDOWED = auto()


@dataclass(frozen=True, slots=True)
class Relaunch:
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    mode: LaunchMode


@dataclass(frozen=True, slots=True)
class InvalidBundle:
    message: str


type BootstrapPlan = Continue | Relaunch | InvalidBundle


def bootstrap_plan(
    platform: str, executable: Path, arguments: tuple[str, ...], environment: Mapping[str, str], bundle: PythonBundle | None,
) -> BootstrapPlan:
    """Choose a handoff from observations; paths are normalized by the caller."""
    if platform != "win32" or bundle is None:
        return Continue.CURRENT
    mode = LaunchMode.WINDOWED if executable.stem.lower().startswith("pythonw") else LaunchMode.CONSOLE
    filename = "pythonw.exe" if mode is LaunchMode.WINDOWED else "python.exe"
    target = bundle.folder / filename
    if target == executable:
        return Continue.CURRENT
    if filename not in bundle.files:
        return InvalidBundle("Toolkit's bundled Python is incomplete. Extract the complete Windows bundle again.")
    clean = tuple((name, value) for name, value in environment.items() if name not in ("PYTHONHOME", "PYTHONPATH"))
    return Relaunch((str(target), *arguments[1:]), clean, mode)


def execute_bootstrap(plan: BootstrapPlan) -> None:
    match plan:
        case Continue.CURRENT:
            return
        case InvalidBundle(message):
            raise RuntimeError(message)
        case Relaunch(command, environment, mode):
            match mode:
                case LaunchMode.WINDOWED:
                    subprocess.Popen(command, env=dict(environment), creationflags=0x0800_0000)
                    raise SystemExit(0)
                case LaunchMode.CONSOLE:
                    process = subprocess.Popen(command, env=dict(environment))
                    raise SystemExit(process.wait())
                case _:
                    assert_never(mode)
        case _:
            assert_never(plan)


def bootstrap() -> None:
    """An existing .pyw association can hand off to the bundled interpreter."""
    folder = (Path(__file__).resolve().parent / ".python").resolve()
    if sys.platform != "win32" or not folder.is_dir():
        return
    bundle = PythonBundle(folder, frozenset(name for name in ("python.exe", "pythonw.exe") if (folder / name).is_file()))
    execute_bootstrap(bootstrap_plan(sys.platform, Path(sys.executable).resolve(), tuple(sys.orig_argv), os.environ, bundle))


def bundled() -> Path | None:
    """Source copies reuse the installed bundle; an adjacent bundle takes precedence."""
    folder = Path(__file__).resolve().parent / ".runtime"
    if folder.is_dir():
        return folder
    inherited = os.environ.get("TOOLKIT_BUNDLED_RUNTIME")
    return Path(inherited) if inherited and Path(inherited).is_dir() else None


def validate_dependencies(metadata: object, platform: str, version: tuple[int, int], pointer_bytes: int) -> None:
    required = cast("dict[str, object]", metadata).get("python") if isinstance(metadata, dict) else None
    if not isinstance(required, str) or len(parts := required.split(".")) != 2 or not all(part.isdecimal() for part in parts):
        raise RuntimeError("The bundled dependency manifest has no valid Python version. Extract the complete bundle again.")
    if platform != "win32" or version != (int(parts[0]), int(parts[1])) or pointer_bytes != 8:
        raise RuntimeError(f"This bundle requires Windows x64 Python {required}. Run StartToolkit.cmd or .python\\python.exe.")


def activate(folder: Path | None = None) -> None:
    if folder is None:
        folder = bundled()
    if folder is not None:
        metadata = cast(object, json.loads((folder / "manifest.json").read_text(encoding="utf-8")))
        validate_dependencies(metadata, sys.platform, (sys.version_info.major, sys.version_info.minor), struct.calcsize("P"))
        source = sys.path[0]  # Keep selfcheck's candidate tree ahead of dependencies too.
        sys.path.insert(0, str(folder))
        # Wheels such as pywin32 declare their import and DLL directories in
        # .pth files. Activate those hooks from the checked bundle too.
        site.addsitedir(str(folder))
        bundle_paths = [entry for entry in sys.path if Path(entry).is_relative_to(folder)]
        preferred = [source]
        for entry in bundle_paths:
            if entry not in preferred:
                preferred.append(entry)
        sys.path[:] = preferred + [entry for entry in sys.path if entry not in preferred]
        os.environ["TOOLKIT_BUNDLED_RUNTIME"] = str(folder)
        previous = os.environ.get("PYTHONPATH", "").split(os.pathsep)
        os.environ["PYTHONPATH"] = os.pathsep.join([str(folder), *(entry for entry in previous if entry and entry != str(folder))])
        interpreter = folder.parent / ".python"
        if (interpreter / "python.exe").is_file():
            # Child commands and the keeper use this same Python without changing
            # the user's global installation, PATH or file associations.
            os.environ.pop("PYTHONHOME", None)
            paths = os.environ.get("PATH", "").split(os.pathsep)
            os.environ["PATH"] = os.pathsep.join([str(interpreter), *(entry for entry in paths if entry and entry != str(interpreter))])
