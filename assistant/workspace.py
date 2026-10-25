"""The agent's workspace: a copy of the toolkit's source it may edit freely. Nothing
it does here touches the running toolkit; the keeper installs the workspace, after
checking it, when asked (see keeper.py)."""

import difflib
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from keeper import LIVE, PROTECTED, WORKSPACE, copy_source, source_digest, source_files
from selfcheck import compile_all

from . import commands


class SourceError(Exception):
    """A change the workspace refuses; its message is for the agent."""


@dataclass(frozen=True, slots=True)
class CheckCompleted:
    passed: bool
    report: str


@dataclass(frozen=True, slots=True)
class CheckInterrupted:
    reason: str


type CheckResult = CheckCompleted | CheckInterrupted


class Workspace:
    __slots__ = ("live", "prepared", "root")

    def __init__(self, live: Path = LIVE, root: Path = WORKSPACE, *, resume: bool = False) -> None:
        self.live = live
        self.root = root
        self.prepared = resume and (root / ".toolkit-workspace-ready").is_file()

    def ensure(self) -> None:
        """Copy source on the first source tool, then keep edits for follow-up requests."""
        if not self.prepared:
            self.reset()

    def reset(self) -> None:
        """Starts over from the running version."""
        self.prepared = False
        (self.root / ".toolkit-workspace-ready").unlink(missing_ok=True)
        copy_source(self.live, self.root)
        (self.root / ".toolkit-workspace-ready").write_text("ready", encoding="utf-8")
        self.prepared = True

    def files(self) -> list[str]:
        return source_files(self.root)

    def _path(self, name: str) -> Path:
        relative = PurePosixPath(name.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or ":" in name or any(p.endswith((".", " ")) for p in relative.parts):
            raise SourceError(f"{name} is outside the toolkit's source")
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise SourceError(f"{name} is outside the toolkit's source")
        return path

    def _writable(self, name: str) -> Path:
        path = self._path(name)
        if PurePosixPath(name.replace("\\", "/")).as_posix().lower() in PROTECTED or any(
            part.lower() in (".runtime", ".python") for part in PurePosixPath(name.replace("\\", "/")).parts
        ):
            raise SourceError(f"{name} is protected: the keeper and its checks can only be changed by a person")
        return path

    def read(self, name: str) -> str:
        path = self._path(name)
        if not path.is_file():
            raise SourceError(f"there's no file {name}")
        return path.read_text(encoding="utf-8")

    def write(self, name: str, text: str) -> None:
        path = self._writable(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def replace(self, name: str, old: str, new: str) -> None:
        """Replaces text that appears exactly once."""
        text = self.read(name)
        count = text.count(old)
        if count != 1:
            raise SourceError(f"the text to replace was not found in {name}" if count == 0 else f"the text to replace is in {name} {count} times")
        self.write(name, text.replace(old, new))

    def delete(self, name: str) -> None:
        path = self._writable(name)
        if not path.is_file():
            raise SourceError(f"there's no file {name}")
        path.unlink()

    def changes(self) -> list[str]:
        """What differs from the running version: `added x`, `changed y`, `deleted z`."""
        mine, running = set(self.files()), set(source_files(self.live))
        changed = [f for f in sorted(mine & running) if (self.root / f).read_bytes() != (self.live / f).read_bytes()]
        return [
            *(f"added {f}" for f in sorted(mine - running)),
            *(f"changed {f}" for f in changed),
            *(f"deleted {f}" for f in sorted(running - mine)),
        ]

    def diff(self) -> str:
        """A unified diff against the running version."""
        parts: list[str] = []
        for change in self.changes():
            name = change.split(" ", 1)[1]
            before = (self.live / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if (self.live / name).exists() else []
            after = (self.root / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if (self.root / name).exists() else []
            parts.append("".join(difflib.unified_diff(before, after, f"running/{name}", f"changed/{name}")))
        return "\n".join(parts)

    def validate(self) -> tuple[bool, str]:
        """Check syntax before spending time on runtime checks."""
        problems: list[str] = []
        compile_all(self.root, problems)
        return not problems, "\n".join(problems) or "Syntax checks pass."

    def check(self, timeout_s: float = 300, *, fast: bool = False) -> tuple[bool, str]:
        result = self.inspect_check(timeout_s, fast=fast)
        return (result.passed, result.report) if isinstance(result, CheckCompleted) else (False, result.reason)

    def inspect_check(
        self, timeout_s: float = 300, *, fast: bool = False, cancel: threading.Event | None = None,
    ) -> CheckResult:
        """Run the stable selfcheck on a fresh copy so tests cannot leave stale bytecode in the draft."""
        with tempfile.TemporaryDirectory(prefix="toolkit-check-") as scratch:
            candidate = Path(scratch) / "source"
            copy_source(self.root, candidate)
            digest = source_digest(candidate)
            command = [sys.executable, str(self.live / "selfcheck.py"), str(candidate)]
            if fast:
                command.append("--fast")
            code, report = commands.run(command, timeout_s=timeout_s, cancel=cancel)
            if code == -1:
                return CheckInterrupted(report)
            if source_digest(candidate) != digest:
                return CheckCompleted(False, "Runtime checks changed the candidate source; fix the tests and check again.")
            return CheckCompleted(code == 0, report)
