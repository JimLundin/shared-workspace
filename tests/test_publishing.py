"""Publish through real local Git repositories; never contact GitHub in tests."""

import shutil
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from unittest import TestCase

from assistant.publishing import PublishError, git, publish_source
from automation.actions import Resources, plan
from automation.execution import ApprovalReview, WorkExecution
from automation.task import ActionVerified, ApprovalDecision, Waiting
from keeper import copy_source

from .test_work_actions import claimed
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


def test_durable_publication_records_before_candidate_code_and_remote_push(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    live, workspace, remote = repository(tmp_path / "repo")
    work = WorkExecution(claim, store, Resources(), clock=clock)
    marker = tmp_path / "candidate-executed"
    (workspace / "probe.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8",
    )

    def approve(title: str, detail: str) -> bool:
        work.bind("publish_changes", {"summary": "Reviewed update", "publication_review": detail})
        return True

    def execute(perform: Callable[[], str]) -> str:
        return work.actions.execute(
            plan("publish_changes", work.arguments), perform, bool, threading.Event(),
            verify=lambda _: ActionVerified("Remote readback matched the published commit."),
        )

    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("CREATE TRIGGER refuse_publication BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT,'cannot record'); END")
    with TestCase().assertRaises(sqlite3.DatabaseError):
        publish_source(live, workspace, "Reviewed update", approve, lambda: False,
                       remote=str(remote), branch="toolkit/agent-durable", execute=execute)
    assert not marker.exists() and not git(remote, "for-each-ref", "refs/heads/toolkit/")
    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("DROP TRIGGER refuse_publication")
    result = publish_source(live, workspace, "Reviewed update", approve, lambda: False,
                            remote=str(remote), branch="toolkit/agent-durable", execute=execute)
    assert marker.exists() and "Published" in result
    assert git(remote, "show", "refs/heads/toolkit/agent-durable:config.py") == "GREETING = 'hello'"
    assert isinstance(store.actions("task")[0].outcome, ActionVerified)


def test_durable_publication_approval_is_bound_to_the_frozen_candidate_and_destination(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    live, workspace, remote = repository(tmp_path / "repo")
    reviews: list[ApprovalReview] = []

    def display(review: ApprovalReview) -> bool:
        reviews.append(review)
        return True

    work = WorkExecution(claim, store, Resources(), review=display, clock=clock)

    def approve(title: str, detail: str) -> bool:
        work.bind("publish_changes", {"summary": "Update", "publication_review": detail})
        return work.approve(title, detail)

    with TestCase().assertRaisesRegex(PublishError, "cancelled"):
        publish_source(live, workspace, "Update", approve, lambda: False, remote=str(remote), branch="toolkit/agent-durable")
    assert isinstance(work.disposition, Waiting)
    assert store.finish(claim.run, work.disposition, at=clock()) is not None
    assert store.answer("task", reviews[0].request.id, ApprovalDecision.APPROVED, at=clock()) is not None
    resumed = store.claim("worker", "new-run", at=clock())
    assert resumed is not None
    work = WorkExecution(resumed, store, Resources(), review=display, clock=clock)
    (workspace / "config.py").write_text("GREETING = 'changed after review'\n", encoding="utf-8")
    with TestCase().assertRaisesRegex(PublishError, "cancelled"):
        publish_source(live, workspace, "Update", approve, lambda: False, remote=str(remote), branch="toolkit/agent-durable")
    assert len(reviews) == 2 and reviews[0].fingerprint != reviews[1].fingerprint
    assert "changed after review" in reviews[1].detail and str(remote) in reviews[1].detail
    assert not git(remote, "for-each-ref", "refs/heads/toolkit/")


if shutil.which("git") is None:
    import unittest

    raise unittest.SkipTest("Git publishing tests need Git")
