"""Every class we define uses slots, so instances can't grow attributes that
nobody declared. Enums, exceptions, protocols, tuples and ctypes structures are
exempt: they manage their own storage."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("miryoku", "assistant", "winapi")
TOP_LEVEL = ("protocol", "config", "usage", "keeper", "selfcheck")


def modules() -> list[ModuleType]:
    """Every module of the toolkit. Off Windows, the ones that need Windows are skipped."""
    names = [*TOP_LEVEL]
    for package in PACKAGES:
        names += [f"{package}.{m.name}" for m in pkgutil.iter_modules([str(ROOT / package)])]
    found: list[ModuleType] = []
    for name in names:
        try:
            found.append(importlib.import_module(name))
        except (ImportError, AttributeError, OSError):
            if sys.platform == "win32":
                raise
    return found


def exempt(cls: type) -> bool:
    bases = [(base.__module__, base.__qualname__) for base in cls.__mro__]
    return (
        issubclass(cls, (Enum, BaseException, tuple))
        or ("typing", "Protocol") in bases
        or any(module in ("ctypes", "_ctypes") for module, _ in bases)
    )


def test_every_class_uses_slots() -> None:
    # A class's instances have no __dict__ (so no undeclared attributes) exactly
    # when __dictoffset__ is 0, which needs __slots__ on it and all its bases.
    missing = [
        f"{module.__name__}.{name}"
        for module in modules()
        for name, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == module.__name__ and not exempt(cls) and cls.__dictoffset__ != 0
    ]
    assert not missing, f"classes without __slots__: {missing}"
