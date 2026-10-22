"""Start with Toolkit's private Python and activate its matching dependencies."""

import json
import os
import site
import struct
import subprocess
import sys
from pathlib import Path
from typing import cast


def bootstrap() -> None:
    """An existing .pyw association can hand off to the bundled interpreter."""
    folder = Path(__file__).resolve().parent / ".python"
    if sys.platform != "win32" or not folder.is_dir():
        return
    windowed = Path(sys.executable).stem.lower().startswith("pythonw")
    executable = folder / ("pythonw.exe" if windowed else "python.exe")
    if executable.resolve() == Path(sys.executable).resolve():
        return
    if not executable.is_file():
        raise RuntimeError("Toolkit's bundled Python is incomplete. Extract the complete Windows bundle again.")
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(name, None)
    process = subprocess.Popen(
        [str(executable), *sys.orig_argv[1:]], env=environment,
        creationflags=0x0800_0000 if windowed else 0,
    )
    raise SystemExit(0 if windowed else process.wait())


def bundled() -> Path | None:
    """Source copies reuse the installed bundle; an adjacent bundle takes precedence."""
    folder = Path(__file__).resolve().parent / ".runtime"
    if folder.is_dir():
        return folder
    inherited = os.environ.get("TOOLKIT_BUNDLED_RUNTIME")
    return Path(inherited) if inherited and Path(inherited).is_dir() else None


def activate() -> None:
    if folder := bundled():
        metadata = cast(object, json.loads((folder / "manifest.json").read_text(encoding="utf-8")))
        version = cast("dict[str, object]", metadata).get("python") if isinstance(metadata, dict) else None
        if not isinstance(version, str) or len(parts := version.split(".")) != 2 or not all(part.isdecimal() for part in parts):
            raise RuntimeError("The bundled dependency manifest has no valid Python version. Extract the complete bundle again.")
        if sys.platform != "win32" or (sys.version_info.major, sys.version_info.minor) != (int(parts[0]), int(parts[1])) or struct.calcsize("P") != 8:
            raise RuntimeError(f"This bundle requires Windows x64 Python {version}. Run StartToolkit.cmd or .python\\python.exe.")
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
