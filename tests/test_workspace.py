"""The agent's workspace: a copy of the source it edits, checks, and asks to install."""

import shutil
from pathlib import Path

from assistant.workspace import SourceError, Workspace

REPO = Path(__file__).resolve().parent.parent


def live_tree(root: Path) -> Path:
    files = {
        "config.py": "GREETING = 'hej'\n",
        "keeper.py": "# the keeper\n",
        "tests/__init__.py": "",
        "tests/test_config.py": "from config import GREETING\n\ndef test_greeting() -> None:\n    assert GREETING\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    shutil.copy(REPO / "selfcheck.py", root / "selfcheck.py")
    shutil.copy(REPO / "runtime.py", root / "runtime.py")
    return root


def workspace(tmp_path: Path) -> Workspace:
    w = Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")
    w.reset()
    return w


def refused(action: object) -> str:
    """The message of the SourceError a call raises."""
    try:
        if callable(action):
            action()
    except SourceError as error:
        return str(error)
    raise AssertionError("expected SourceError")


def test_reset_copies_the_running_source(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    assert "config.py" in w.files()
    assert w.read("config.py") == "GREETING = 'hej'\n"
    assert w.changes() == []


def test_edits_show_as_changes_and_a_diff(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    w.replace("config.py", "'hej'", "'hello'")
    w.write("snippets.py", "X = 1\n")
    assert w.changes() == ["added snippets.py", "changed config.py"]
    diff = w.diff()
    assert "-GREETING = 'hej'" in diff
    assert "+GREETING = 'hello'" in diff
    assert "+X = 1" in diff
    assert (tmp_path / "live" / "config.py").read_text(encoding="utf-8") == "GREETING = 'hej'\n"  # live untouched


def test_replace_needs_exactly_one_match(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    assert "not found" in refused(lambda: w.replace("config.py", "nope", "x"))
    w.write("twice.py", "a\na\n")
    assert "2 times" in refused(lambda: w.replace("twice.py", "a", "b"))


def test_protected_files_and_paths_outside_are_refused(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    assert "protected" in refused(lambda: w.write("keeper.py", "hacked"))
    assert "protected" in refused(lambda: w.write("selfcheck.py", "hacked"))
    assert "protected" in refused(lambda: w.write("runtime.py", "hacked"))
    assert "protected" in refused(lambda: w.write(".runtime/agents/__init__.py", "hacked"))
    assert "protected" in refused(lambda: w.write(".python/pythonw.exe", "hacked"))
    assert "outside" in refused(lambda: w.write("../escape.py", "x"))
    assert "outside" in refused(lambda: w.read("C:/Windows/win.ini"))


def test_deleting_a_file(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    w.delete("config.py")
    assert w.changes() == ["deleted config.py"]


def test_check_runs_selfcheck_on_the_workspace(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    ok, report = w.check()
    assert ok, report
    assert "1 passed" in report
    w.write("config.py", "GREETING = ''\n")
    ok, report = w.check()
    assert not ok
    assert "FAILED tests.test_config.test_greeting" in report


def test_runtime_checks_cannot_change_the_source_that_was_checked(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    content = "from pathlib import Path\nPath(__file__).write_text('CHANGED = True\\n', encoding='utf-8')\n"
    w.write("candidate.py", content)
    ok, report = w.check()
    assert not ok and "changed the candidate source" in report
    assert w.read("candidate.py") == content



def test_validation_does_not_import_candidate_modules(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    marker = tmp_path / "outside"
    w.write("candidate.py", f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    assert w.validate()[0]
    assert not marker.exists()


def test_protected_file_aliases_are_refused(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    for name in ("KEEPER.PY", "keeper.py.", "keeper.py ", "SELFcheck.PY"):
        try:
            w.write(name, "hacked")
        except SourceError:
            continue
        raise AssertionError(f"accepted protected alias {name}")
