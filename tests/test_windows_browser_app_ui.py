"""The public UI broker owns delivery and cancellation, without opening a browser."""

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

if sys.platform != "win32":
    raise unittest.SkipTest("Windows Qt browser-app broker")

ROOT = Path(__file__).resolve().parent.parent

_PROGRAM = textwrap.dedent("""\
    import queue
    import sys
    import threading
    from unittest.mock import patch
    from runtime import activate
    activate()
    from assistant import ui as module
    from assistant.browser_surface_operations import AppCancelled, AppFailed, AppSucceeded, ListApps
    from assistant.qt import QApplication
    from assistant.work_window import WorkSnapshot

    mode = sys.argv[1]
    ui = module.Ui()
    prepared = threading.Event()
    entered = threading.Event()
    failures = []
    calls = []
    owners = []

    class ExpiringRequest(threading.Event):
        def __init__(self, expired):
            super().__init__()
            self.expired = expired

        def is_set(self):
            if self.expired.is_set():
                self.set()
            return super().is_set()

    class Desktop:
        __slots__ = ("closed", "pending")

        def __init__(self, root, backend, actions, *, work_snapshot):
            assert threading.current_thread() is threading.main_thread()
            self.closed = False
            self.pending = []
            owners.append("created")

        def browser_request(self, operation, cancel, finished):
            assert threading.current_thread() is threading.main_thread()
            assert not self.closed and not cancel.is_set()
            calls.append((operation, cancel))
            if operation.offset == 3:
                def deliver(root):
                    finished(AppSucceeded("Listed owned apps"))
                    finished(AppFailed("Duplicate owner callback"))
                ui.post(deliver)
            elif operation.offset == 4:
                raise OSError("Fixture browser owner failed")
            elif operation.offset == 5:
                cancel.set()
                finished(AppSucceeded("The effect was confirmed before cancellation"))
            elif operation.offset == 10:
                self.pending.append(finished)
                entered.set()
            else:
                raise AssertionError("A cancelled or unavailable request reached the owner")

        def close(self):
            assert threading.current_thread() is threading.main_thread()
            self.closed = True
            owners.append("closed")
            for finished in self.pending:
                finished(AppSucceeded("Late owner callback during disposal"))

    def open_desktop():
        ui.desktop(
            lambda request: None, lambda command: None, WorkSnapshot,
            watch=lambda identities: None, show_agent=lambda: None, show_work=lambda: None,
            stop=lambda: None, overlay=lambda: None, on_problem=failures.append,
        )

    def request(offset, *, cancelled=False, cancel=None):
        if cancel is None:
            cancel = threading.Event()
        if cancelled:
            cancel.set()
        results = queue.Queue()
        ui.browser_app(ListApps(offset), cancel, results.put)
        return cancel, results

    def failed(results):
        result = results.get(timeout=5)
        assert isinstance(result, AppFailed), result
        return result

    def drive():
        try:
            if mode == "unavailable":
                cancel, results = request(0)
                prepared.set()
                assert "unavailable" in failed(results).reason
                assert not cancel.is_set() and not calls
            elif mode == "dispatch":
                open_desktop()
                before, before_results = request(1, cancelled=True)
                queued, queued_results = request(2)
                queued.set()
                expired = threading.Event()
                deadline, deadline_results = request(6, cancel=ExpiringRequest(expired))
                expired.set()
                success, success_results = request(3)
                error, error_results = request(4)
                effect, effect_results = request(5)
                prepared.set()
                assert before_results.get(timeout=5) is AppCancelled.REQUEST
                assert queued_results.get(timeout=5) is AppCancelled.REQUEST
                assert deadline_results.get(timeout=5) is AppCancelled.REQUEST
                assert deadline.is_set()
                assert success_results.get(timeout=5) == AppSucceeded("Listed owned apps")
                assert "could not be confirmed" in failed(error_results).reason
                assert effect_results.get(timeout=5) == AppSucceeded("The effect was confirmed before cancellation")
                assert calls == [(ListApps(3), success), (ListApps(4), error), (ListApps(5), effect)], calls
                assert before_results.empty() and queued_results.empty() and success_results.empty()
            else:
                open_desktop()
                cancel, results = request(10)
                prepared.set()
                if mode != "queued-shutdown":
                    assert entered.wait(5), "The request never reached its Qt owner"
                if mode == "desktop-shutdown":
                    ui.close_desktop()
                elif mode == "ui-shutdown":
                    ui.close()
                assert "could not be confirmed" in failed(results).reason
                assert cancel.is_set(), "Shutdown did not cancel deferred native work"
                late, late_results = request(11)
                failed(late_results)
                assert late.is_set()
                if mode != "queued-shutdown":
                    assert calls == [(ListApps(10), cancel)], calls
                else:
                    assert not calls and not owners, (calls, owners)
                assert results.empty(), "The owner completed a retired request again"
        except BaseException as error:
            failures.append(error)
        finally:
            prepared.set()
            ui.close()

    controller = threading.Thread(target=drive, name="browser app broker caller", daemon=True)
    with patch.object(module, "DesktopWindow", Desktop):
        controller.start()
        assert prepared.wait(5), "Browser submission blocked waiting for the Qt loop"
        if mode == "queued-shutdown":
            ui.close()
        ui.run()
        controller.join(timeout=10)
    assert not controller.is_alive(), "The browser caller did not finish"
    assert not failures, failures
    assert QApplication.instance() is None
    assert ui.prompt("Already closed") is None
    late = queue.Queue()
    ui.browser_app(ListApps(), threading.Event(), late.put)
    failed(late)
    print("Browser app broker passed: " + mode)
    """)


def _check(mode: str, folder: Path) -> None:
    checked = subprocess.run(
        [sys.executable, "-S", "-c", _PROGRAM, mode], cwd=ROOT,
        env=dict(os.environ, PYTHONUTF8="1", LOCALAPPDATA=str(folder)),
        capture_output=True, timeout=45, check=False,
    )
    output = (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert checked.returncode == 0, f"Native exit {checked.returncode & 0xffffffff:#x}: {output}"
    assert f"Browser app broker passed: {mode}" in output


def test_browser_app_requests_deliver_on_qt_without_blocking_and_settle_once(tmp_path: Path) -> None:
    _check("dispatch", tmp_path)


def test_browser_app_request_rejects_an_unavailable_owner(tmp_path: Path) -> None:
    _check("unavailable", tmp_path)


def test_desktop_close_cancels_pending_and_rejects_late_browser_app_requests(tmp_path: Path) -> None:
    _check("desktop-shutdown", tmp_path)


def test_ui_close_resolves_inflight_browser_app_requests(tmp_path: Path) -> None:
    _check("ui-shutdown", tmp_path)


def test_ui_close_before_run_prevents_queued_browser_app_effects(tmp_path: Path) -> None:
    _check("queued-shutdown", tmp_path)
