"""selfcheck: deciding whether a copy of the toolkit is sound, with only Python."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SELFCHECK = Path(__file__).resolve().parent.parent / "selfcheck.py"


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def selfcheck(root: Path) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, str(SELFCHECK), str(root)], capture_output=True, text=True, timeout=60, env=env, check=False)
    return done.returncode, done.stdout + done.stderr


SOUND = {
    "tools/__init__.py": "",
    "tools/maths.py": "def double(x: int) -> int:\n    return 2 * x\n",
    "tests/__init__.py": "",
    "tests/test_maths.py": (
        "from pathlib import Path\n"
        "from tools.maths import double\n"
        "def test_double() -> None:\n    assert double(2) == 4\n"
        "def test_with_a_folder(tmp_path: Path) -> None:\n    (tmp_path / 'x').write_text('hi')\n    assert (tmp_path / 'x').read_text() == 'hi'\n"
    ),
    "tests/test_later.py": "import unittest\nraise unittest.SkipTest('not here')\n",
}


def test_a_sound_tree_passes(tmp_path: Path) -> None:
    code, report = selfcheck(tree(tmp_path, SOUND))
    assert code == 0, report
    assert "2 passed" in report
    assert "1 module skipped" in report


def test_a_failing_test_fails(tmp_path: Path) -> None:
    files = {**SOUND, "tests/test_maths.py": "from tools.maths import double\ndef test_double() -> None:\n    assert double(2) == 5\n"}
    code, report = selfcheck(tree(tmp_path, files))
    assert code == 1
    assert "FAILED tests.test_maths.test_double" in report
    assert "AssertionError" in report


def test_a_syntax_error_fails(tmp_path: Path) -> None:
    code, report = selfcheck(tree(tmp_path, {**SOUND, "tools/broken.py": "def oops(:\n"}))
    assert code == 1
    assert "doesn't compile: tools/broken.py" in report


def test_a_module_that_cant_import_fails(tmp_path: Path) -> None:
    code, report = selfcheck(tree(tmp_path, {**SOUND, "tools/needs.py": "import no_such_thing\n"}))
    assert code == 1
    assert "doesn't import: tools.needs" in report


def test_a_crashing_test_module_fails(tmp_path: Path) -> None:
    code, report = selfcheck(tree(tmp_path, {**SOUND, "tests/test_bad.py": "raise RuntimeError('boom')\n"}))
    assert code == 1
    assert "boom" in report



def test_broken_windowless_launcher_fails(tmp_path: Path) -> None:
    code, report = selfcheck(tree(tmp_path, {**SOUND, "toolkit.pyw": "def broken(:\n"}))
    assert code == 1
    assert "doesn't compile: toolkit.pyw" in report
