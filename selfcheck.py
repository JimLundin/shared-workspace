"""selfcheck: is this copy of the toolkit sound? Uses installed or bundled SDK dependencies.

    python selfcheck.py [FOLDER] [--fast] [--durations N]

1. Every .py file compiles.
2. Every module imports (off Windows, the ones that need Windows are skipped).
3. Every test passes: functions named test_* in tests/test_*.py. A test may take
   `tmp_path` (a fresh folder); a test module may raise unittest.SkipTest.

Prints a report; exits 0 if sound, 1 if not. The keeper runs this on every change
before installing it, so the agent can't change it (see keeper.py).
The optional fast editing profile defers tests/test_windows_*.py. The keeper
always uses the default full profile. Both profiles compile and import all source.

It looks inside arbitrary modules, which is untyped by nature, so like the other
boundaries (winapi, assistant/ui.py) it's exempt from the "no Any" rule inside.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

import argparse
import importlib
import inspect
import os
import py_compile
import sys
import tempfile
import time
import traceback
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path

_WINDOWS_ONLY = ("WinDLL", "WINFUNCTYPE", "windll", "winreg", "msvcrt", "_winapi", "PySide6", "shiboken6", "win32more")
"""Signs that an import failed only because this isn't Windows."""


_ON_WINDOWS: bool = sys.platform == "win32"


def _windows_only(error: BaseException) -> bool:
    return not _ON_WINDOWS and isinstance(error, (ImportError, AttributeError, OSError)) and any(w in str(error) for w in _WINDOWS_ONLY)


def _sources(root: Path) -> Iterator[Path]:
    # Do not enumerate bundled interpreters and dependencies just to discard them.
    for folder, directories, files in os.walk(root):
        directories[:] = sorted(name for name in directories if not name.startswith((".", "__pycache__")))
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix in (".py", ".pyw") and not name.startswith((".", "__pycache__")):
                yield path


def _modules(root: Path) -> Iterator[str]:
    """Top-level modules, and every module of every top-level package (tests excepted)."""
    for path in _sources(root):
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts
        if parts[0] == "tests" or path.name == "selfcheck.py" or path.suffix == ".pyw":
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
        with tempfile.TemporaryDirectory(prefix="selfcheck-") as scratch:
            arguments[name] = Path(scratch)
            test(**arguments)
            return
    test(**arguments)


def run_tests(root: Path, problems: list[str], *, fast: bool = False, timings: list[tuple[float, str]] | None = None) -> tuple[int, int]:
    """Passed tests, and skipped test modules."""
    passed = skipped = 0
    for path in sorted((root / "tests").glob("test_*.py")):
        if fast and path.name.startswith("test_windows_"):
            continue
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
                started = time.perf_counter()
                try:
                    _call(test)
                    passed += 1
                except unittest.SkipTest:
                    pass
                except Exception as error:
                    problems.append(f"FAILED {name}.{attribute}\n{''.join(traceback.format_exception(error))}")
                finally:
                    if timings is not None:
                        timings.append((time.perf_counter() - started, f"{name}.{attribute}"))
    return passed, skipped


def check(root: Path, *, fast: bool = False, durations: int = 0) -> tuple[bool, str]:
    """Whether the copy at `root` is sound, and a report. Imports from `root`, so run
    it in a process of its own (as `main` does)."""
    os.environ.pop("TOOLKIT_E2E", None)  # never the end-to-end tests: they'd start a second toolkit
    sys.path.insert(0, str(root))
    os.chdir(root)
    from runtime import activate  # noqa: PLC0415

    activate()
    problems: list[str] = []
    timings: list[tuple[float, str]] = []
    started = time.perf_counter()
    compile_all(root, problems)
    timings.append((time.perf_counter() - started, "compile all source"))
    if not problems:
        started = time.perf_counter()
        import_all(root, problems)
        timings.append((time.perf_counter() - started, "import all modules"))
    passed, skipped = run_tests(root, problems, fast=fast, timings=timings if durations else None) if not problems else (0, 0)
    summary = f"{passed} passed, {len([p for p in problems if p.startswith('FAILED')])} failed, {skipped} module{'' if skipped == 1 else 's'} skipped"
    if fast:
        deferred = len(list((root / "tests").glob("test_windows_*.py")))
        summary += f", {deferred} native module{'' if deferred == 1 else 's'} deferred (full checks run before install)"
    if durations:
        summary += "\nSlowest checks:\n" + "\n".join(f"{seconds:7.3f}s {name}" for seconds, name in sorted(timings, reverse=True)[:durations])
    return not problems, "\n\n".join([*problems, summary]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, default=Path(__file__).parent)
    parser.add_argument("--fast", action="store_true", help="editing checks; defer Windows integration modules")
    parser.add_argument("--durations", type=int, default=0, metavar="N", help="report the N slowest checks")
    args = parser.parse_args()
    if args.durations < 0:
        parser.error("durations must be nonnegative")
    ok, report = check(args.folder.resolve(), fast=args.fast, durations=args.durations)
    sys.stdout.write(report)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
