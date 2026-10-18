"""selfcheck: is this copy of the toolkit sound? Needs nothing but Python.

    python selfcheck.py [FOLDER]

1. Every .py file compiles.
2. Every module imports (off Windows, the ones that need Windows are skipped).
3. Every test passes: functions named test_* in tests/test_*.py. A test may take
   `tmp_path` (a fresh folder); a test module may raise unittest.SkipTest.

Prints a report; exits 0 if sound, 1 if not. The keeper runs this on every change
before installing it, so the agent can't change it (see keeper.py).

It looks inside arbitrary modules, which is untyped by nature, so like the other
boundaries (winapi, assistant/ui.py) it's exempt from the "no Any" rule inside.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

from __future__ import annotations

import importlib
import inspect
import os
import py_compile
import sys
import tempfile
import traceback
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path

_WINDOWS_ONLY = ("WinDLL", "WINFUNCTYPE", "windll", "tkinter", "_tkinter", "winreg", "msvcrt", "_winapi")
"""Signs that an import failed only because this isn't Windows."""


_ON_WINDOWS: bool = sys.platform == "win32"


def _windows_only(error: BaseException) -> bool:
    return not _ON_WINDOWS and isinstance(error, (ImportError, AttributeError, OSError)) and any(w in str(error) for w in _WINDOWS_ONLY)


def _sources(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if not any(part.startswith((".", "__pycache__")) for part in relative.parts):
            yield path


def _modules(root: Path) -> Iterator[str]:
    """Top-level modules, and every module of every top-level package (tests excepted)."""
    for path in _sources(root):
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts
        if parts[0] == "tests" or path.name == "selfcheck.py":
            continue
        if len(parts) == 1 or (root / parts[0] / "__init__.py").exists():
            yield ".".join(p for p in parts if p != "__init__") if parts[-1] == "__init__" else ".".join(parts)


def compile_all(root: Path, problems: list[str]) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        for i, path in enumerate(_sources(root)):
            try:
                py_compile.compile(str(path), cfile=os.path.join(scratch, f"{i}.pyc"), doraise=True)
            except py_compile.PyCompileError as error:
                problems.append(f"doesn't compile: {path.relative_to(root).as_posix()}\n{error.msg}")


def import_all(root: Path, problems: list[str]) -> None:
    for name in _modules(root):
        try:
            importlib.import_module(name)
        except Exception as error:
            if not _windows_only(error):
                problems.append(f"doesn't import: {name}\n{''.join(traceback.format_exception(error))}")


def _call(test: Callable[..., object]) -> None:
    arguments: dict[str, object] = {}
    for name in inspect.signature(test).parameters:
        if name != "tmp_path":
            raise TypeError(f"selfcheck can only give a test tmp_path, not {name}")
        arguments[name] = Path(tempfile.mkdtemp(prefix="selfcheck-"))
    test(**arguments)


def run_tests(root: Path, problems: list[str]) -> tuple[int, int]:
    """Passed tests, and skipped test modules."""
    passed = skipped = 0
    for path in sorted((root / "tests").glob("test_*.py")):
        name = f"tests.{path.stem}"
        try:
            module = importlib.import_module(name)
        except unittest.SkipTest:
            skipped += 1
            continue
        except Exception as error:
            if _windows_only(error):
                skipped += 1
            else:
                problems.append(f"FAILED {name} (while loading)\n{''.join(traceback.format_exception(error))}")
            continue
        for attribute, test in sorted(vars(module).items()):
            if attribute.startswith("test_") and callable(test) and getattr(test, "__module__", None) == name:
                try:
                    _call(test)
                    passed += 1
                except unittest.SkipTest:
                    pass
                except Exception as error:
                    problems.append(f"FAILED {name}.{attribute}\n{''.join(traceback.format_exception(error))}")
    return passed, skipped


def check(root: Path) -> tuple[bool, str]:
    """Whether the copy at `root` is sound, and a report. Imports from `root`, so run
    it in a process of its own (as `main` does)."""
    sys.path.insert(0, str(root))
    os.chdir(root)
    problems: list[str] = []
    compile_all(root, problems)
    if not problems:
        import_all(root, problems)
    passed, skipped = run_tests(root, problems) if not problems else (0, 0)
    summary = f"{passed} passed, {len([p for p in problems if p.startswith('FAILED')])} failed, {skipped} module{'' if skipped == 1 else 's'} skipped"
    return not problems, "\n\n".join([*problems, summary]) + "\n"


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent).resolve()
    ok, report = check(root)
    sys.stdout.write(report)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
