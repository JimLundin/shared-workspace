"""Use the bundled SDK when present; installed packages otherwise."""

from __future__ import annotations

import os
import platform
import struct
import sys
from pathlib import Path


def activate() -> None:
    folder = Path(__file__).resolve().parent / ".runtime"
    if folder.is_dir():
        if sys.platform != "win32" or not platform.python_version().startswith("3.12.") or struct.calcsize("P") != 8:
            raise RuntimeError("This bundle requires Windows x64 Python 3.12. Launch it with your installed Python 3.12/Pythonw.")
        sys.path.insert(0, str(folder))
        previous = os.environ.get("PYTHONPATH", "")
        os.environ["PYTHONPATH"] = str(folder) + (os.pathsep + previous if previous else "")
