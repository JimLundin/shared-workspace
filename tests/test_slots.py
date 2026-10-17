"""Every class we define uses slots, so instances can't grow attributes that
nobody declared. Enums, exceptions, protocols, tuples and ctypes structures are
exempt: they manage their own storage."""

from __future__ import annotations

import inspect
import sys
from enum import Enum
from types import ModuleType

from miryoku import engine, layout


def modules() -> list[ModuleType]:
    found = [layout, engine]
    if sys.platform == "win32":
        from miryoku import win, win32  # noqa: PLC0415 (Windows-only modules)

        found += [win, win32]
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
