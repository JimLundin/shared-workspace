"""Native filesystem spelling without changing saved or displayed path identities."""

import os
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    """Use Windows extended paths at file effects, never in manifests or shell targets."""
    if os.name != "nt":
        return path
    value = str(path)
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        return path
    absolute = os.path.abspath(value)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)
