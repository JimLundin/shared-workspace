"""Publish reviewed source to a new Git branch, independently of local installation."""

import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from keeper import copy_source, source_diff, source_digest, source_files

DEFAULT_REMOTE = "https://github.com/JimLundin/toolkit.git"


class PublishError(Exception):
    """A publishing failure whose message can be shown to the user."""


def git(folder: Path, *arguments: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    try:
        result = subprocess.run(
            ["git", "-C", str(folder), *arguments], capture_output=True, text=True, timeout=120, env=env, check=False
        )
    except FileNotFoundError as error:
        raise PublishError("Publishing needs Git for Windows on PATH and GitHub authentication.") from error
    except subprocess.TimeoutExpired as error:
        raise PublishError("Git timed out; check network access and GitHub authentication.") from error
    if result.returncode:
        raise PublishError(f"Git {arguments[0]} failed: {(result.stderr or result.stdout)[-2000:]}")
    return result.stdout.strip()


def publish_source(
    live: Path,
    workspace: Path,
    summary: str,
    approve: Callable[[str, str], bool],
    stopped: Callable[[], bool],
    *,
    remote: str = DEFAULT_REMOTE,
    base: str = "main",
) -> str:
    """Freeze, review, test, commit and push. No force push and no edits to live source."""
    if not summary.strip():
        raise PublishError("A commit summary is required.")
    with tempfile.TemporaryDirectory(prefix="toolkit-publish-") as scratch:
        root = Path(scratch)
        candidate, checkout = root / "candidate", root / "checkout"
        copy_source(workspace, candidate)
        digest = source_digest(candidate)
        diff = source_diff(live, candidate)
        if not diff:
            raise PublishError("There are no source changes to publish.")
        branch = f"toolkit/agent-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        detail = f"Push to {remote}\nNew branch: {branch}\nCommit: {summary}\n\nRun this code and its tests before publishing?\n\n{diff}"
        if not approve("Publish toolkit changes?", detail):
            raise PublishError("Publishing cancelled; nothing was pushed.")
        if stopped():
            raise PublishError("Publishing stopped; nothing was pushed.")
        git(root, "-c", "core.autocrlf=false", "clone", "--quiet", "--single-branch", "--branch", base, "--", remote, str(checkout))
        remote_files, live_files = source_files(checkout), source_files(live)
        if remote_files != live_files or any(
            (checkout / name).read_bytes().replace(b"\r\n", b"\n") != (live / name).read_bytes().replace(b"\r\n", b"\n")
            for name in remote_files
        ):
            raise PublishError("The remote base differs from the running source. Update your local toolkit before publishing.")
        if source_digest(candidate) != digest:
            raise PublishError("Source changed during review; review it again.")
        try:
            checked = subprocess.run(
                [sys.executable, str(live / "selfcheck.py"), str(candidate)], capture_output=True, timeout=300, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise PublishError("Runtime checks timed out; nothing was pushed.") from error
        if checked.returncode:
            raise PublishError("Runtime checks failed; nothing was pushed.\n" + (checked.stdout + checked.stderr).decode(errors="replace")[-3000:])
        if source_digest(candidate) != digest:
            raise PublishError("Runtime checks changed the source; review it again.")
        names = sorted(set(source_files(checkout)) | set(source_files(candidate)))
        git(checkout, "checkout", "-b", branch)
        copy_source(candidate, checkout)
        git(checkout, "add", "-A", "--", *names)
        git(
            checkout, "-c", f"user.name={os.environ.get('TOOLKIT_GIT_AUTHOR_NAME', 'Toolkit Agent')}",
            "-c", f"user.email={os.environ.get('TOOLKIT_GIT_AUTHOR_EMAIL', 'toolkit@localhost')}", "commit", "-m", summary,
        )
        if stopped():
            raise PublishError("Publishing stopped; nothing was pushed.")
        git(checkout, "push", "origin", f"HEAD:refs/heads/{branch}")
        return f"Published {git(checkout, 'rev-parse', 'HEAD')} to {remote}, branch {branch}. Main and the running toolkit were not changed."
