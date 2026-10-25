"""Real Windows sharing handles exercise atomic package publication and cleanup."""

import ctypes
import json
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import SkipTest, TestCase

if sys.platform != "win32":
    raise SkipTest("Windows directory sharing")

from assistant.meeting_followup import parse_followup
from assistant.meeting_followup_store import FollowupStore
from automation.actions import FileVerifier
from automation.files import filesystem_path
from automation.task import ActionVerified
from winapi._core import fn, kernel32

from .test_meeting_followup import followup_response, followup_sources

_open_directory = cast(
    Callable[[str, int, int, None, int, int, None], int | None],
    fn(kernel32, "CreateFileW", wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE),
)
_close_handle = cast(Callable[[int], bool], fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE))


@contextmanager
def directory_reader(path: Path) -> Generator[None]:
    # GENERIC_READ + OPEN_EXISTING + FILE_FLAG_BACKUP_SEMANTICS opens a reader.
    # A metadata-only handle does not enforce the delete-sharing constraint.
    handle = _open_directory(str(filesystem_path(path)), 0x80000000, 0x1 | 0x2, None, 3, 0x02000000, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not _close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def test_publication_recovers_after_a_directory_reader_releases_its_handle(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    attempts: list[tuple[Path, Path]] = []
    blocked: list[int] = []

    def install(source: Path, destination: Path) -> None:
        attempts.append((source, destination))
        if len(attempts) == 1:
            with directory_reader(source):
                try:
                    filesystem_path(source).rename(filesystem_path(destination))
                except OSError as error:
                    blocked.append(error.winerror)
                    raise
            raise AssertionError("The real directory reader did not block the rename.")
        filesystem_path(source).rename(filesystem_path(destination))

    store = FollowupStore(tmp_path / "packages", install=install)
    plan = store.plan("release-task", followup)
    published = store.publish(plan)
    assert blocked and all(code in (5, 32, 33) for code in blocked)
    assert 2 <= len(attempts) <= 6 and len(set(attempts)) == 1
    assert attempts[0][1] == plan.directory
    assert store.read(plan.task_id, plan.version) == published
    assert isinstance(FileVerifier().verify(plan.verification), ActionVerified)
    assert {path.name for path in filesystem_path(plan.directory.parent).iterdir()} == {plan.version}


def test_persistent_directory_readers_exhaust_retries_without_losing_prior_versions(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    original_store = FollowupStore(tmp_path / "packages")
    original = original_store.publish(original_store.plan("release-task", followup))
    failures: list[OSError] = []

    def install(source: Path, destination: Path) -> None:
        assert len(failures) < 6, "Publication did not stop retrying the persistent sharing failure."
        with directory_reader(source):
            try:
                filesystem_path(source).rename(filesystem_path(destination))
            except OSError as error:
                failures.append(error)
                raise
        raise AssertionError("The real directory reader did not block the rename.")

    store = FollowupStore(original_store.root, install=install)
    plan = store.plan(original.task_id, replace(followup, title="A later package"))
    with TestCase().assertRaises(OSError) as raised:
        store.publish(plan)
    assert 2 <= len(failures) <= 6 and raised.exception is failures[-1]
    assert all(error.winerror in (5, 32, 33) for error in failures)
    assert store.versions(original.task_id) == (original,)
    assert {path.name for path in filesystem_path(plan.directory.parent).iterdir()} == {original.version}
    recovered = original_store.publish(plan)
    assert recovered.version == plan.version
    assert original_store.read(original.task_id, original.version) == original


def test_native_access_and_lock_errors_retry_the_same_atomic_publication(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    for code in (5, 33):  # Access denied and lock violation use the sharing retry policy.
        failure = ctypes.WinError(code)
        attempts: list[tuple[Path, Path]] = []

        def install(
            source: Path, destination: Path, *, error: OSError = failure, observed: list[tuple[Path, Path]] = attempts,
        ) -> None:
            observed.append((source, destination))
            if len(observed) == 1:
                raise error
            filesystem_path(source).rename(filesystem_path(destination))

        store = FollowupStore(tmp_path / f"error-{code}", install=install)
        plan = store.plan("release-task", followup)
        published = store.publish(plan)
        assert 2 <= len(attempts) <= 6 and len(set(attempts)) == 1
        assert attempts[0][1] == plan.directory
        assert store.read(plan.task_id, plan.version) == published


def test_nonsharing_windows_install_errors_propagate_without_retry(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    for code in (3, 87, 112):  # Missing path, invalid parameter, disk full.
        failure = ctypes.WinError(code)
        attempts: list[Path] = []

        def install(source: Path, destination: Path, *, error: OSError = failure, destinations: list[Path] = attempts) -> None:
            destinations.append(destination)
            raise error

        store = FollowupStore(tmp_path / f"error-{code}", install=install)
        plan = store.plan("release-task", followup)
        with TestCase().assertRaises(OSError) as raised:
            store.publish(plan)
        assert raised.exception is failure and attempts == [plan.directory]
        assert store.versions(plan.task_id) == ()
        assert list(filesystem_path(plan.directory.parent).iterdir()) == []
