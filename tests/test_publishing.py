"""Publish through real local Git repositories; never contact GitHub in tests."""

import shutil
from pathlib import Path

from assistant.publishing import PublishError, git, publish_source
from keeper import copy_source

from .test_workspace import live_tree


def repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    live = live_tree(tmp_path / "live")
    git(live, "init")
    git(live, "symbolic-ref", "HEAD", "refs/heads/main")
    git(live, "add", ".")
    git(live, "-c", "user.name=Toolkit Test", "-c", "user.email=toolkit@example.invalid", "commit", "-m", "baseline")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", str(live), str(remote))
    workspace = tmp_path / "workspace"
    copy_source(live, workspace)
    (workspace / "config.py").write_text("GREETING = 'hello'\n", encoding="utf-8")
    return live, workspace, remote


def test_reviewed_change_is_pushed_to_new_branch(tmp_path: Path) -> None:
    live, workspace, remote = repository(tmp_path)
    main = git(remote, "rev-parse", "refs/heads/main")
    result = publish_source(live, workspace, "English greeting", lambda _title, _diff: True, lambda: False, remote=str(remote))
    refs = git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/toolkit/")
    assert refs.startswith("refs/heads/toolkit/agent-")
    assert git(remote, "show", f"{refs}:config.py") == "GREETING = 'hello'"
    assert git(remote, "rev-parse", "refs/heads/main") == main
    assert "'hej'" in (live / "config.py").read_text(encoding="utf-8")
    assert "Published" in result


def test_declined_review_never_executes_candidate_or_pushes(tmp_path: Path) -> None:
    live, workspace, remote = repository(tmp_path)
    marker = tmp_path / "executed"
    (workspace / "candidate.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    try:
        publish_source(live, workspace, "unapproved", lambda _title, _diff: False, lambda: False, remote=str(remote))
    except PublishError as error:
        assert "cancelled" in str(error)
    else:
        raise AssertionError("declined publishing must stop")
    assert not marker.exists()
    assert not git(remote, "for-each-ref", "refs/heads/toolkit/")


def test_failed_runtime_checks_never_push(tmp_path: Path) -> None:
    live, workspace, remote = repository(tmp_path)
    (workspace / "config.py").write_text("GREETING = ''\n", encoding="utf-8")
    try:
        publish_source(live, workspace, "broken", lambda _title, _diff: True, lambda: False, remote=str(remote))
    except PublishError as error:
        assert "Runtime checks failed" in str(error)
    else:
        raise AssertionError("failing tests must prevent push")
    assert not git(remote, "for-each-ref", "refs/heads/toolkit/")


def test_remote_divergence_blocks_publishing(tmp_path: Path) -> None:
    live, workspace, remote = repository(tmp_path)
    (live / "config.py").write_text("GREETING = 'local change'\n", encoding="utf-8")
    try:
        publish_source(live, workspace, "change", lambda _title, _diff: True, lambda: False, remote=str(remote))
    except PublishError as error:
        assert "remote base differs" in str(error)
    else:
        raise AssertionError("diverged base must be refused")


if shutil.which("git") is None:
    import unittest

    raise unittest.SkipTest("Git publishing tests need Git")
