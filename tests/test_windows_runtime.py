"""Copied source uses the inherited SDK and Qt from the installed bundle."""

import os
import socket
import subprocess
import sys
import textwrap
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

if sys.platform != "win32":
    raise unittest.SkipTest("Windows bundle integration")

from keeper import copy_source
from runtime import LaunchMode, Relaunch, activate, execute_bootstrap

ROOT = Path(__file__).resolve().parent.parent


def test_windowed_handoff_returns_while_the_child_is_still_running() -> None:
    code = (
        "import socket,sys; "
        "channel=socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=5); channel.recv(1); channel.close()"
    )
    with socket.create_server(("127.0.0.1", 0)) as server, ThreadPoolExecutor(max_workers=1) as worker:
        server.settimeout(5)
        port = cast(tuple[str, int], server.getsockname())[1]
        plan = Relaunch((sys.executable, "-c", code, str(port)), tuple(os.environ.items()), LaunchMode.WINDOWED)
        pending = worker.submit(execute_bootstrap, plan)
        channel, _peer = server.accept()
        with channel:
            channel.settimeout(5)
            try:
                with unittest.TestCase().assertRaises(SystemExit) as exited:
                    pending.result(timeout=2)
                assert exited.exception.code == 0
            finally:
                channel.sendall(b"x")
            assert channel.recv(1) == b"", "The child must exit after its work is released."


def test_copied_source_starts_the_sdk_and_qt_with_the_inherited_bundle(tmp_path: Path) -> None:
    if not (ROOT / ".runtime").is_dir() and not os.environ.get("TOOLKIT_BUNDLED_RUNTIME"):
        raise unittest.SkipTest("Windows bundle integration")
    activate()
    source = tmp_path / "source"
    copy_source(ROOT, source)
    assert not (source / ".runtime").exists() and not (source / ".python").exists()
    script = (
        "from runtime import activate; activate(); import assistant.sdk, assistant.agent_panel; "
        "from assistant.qt_ui import EventLoop, application; "
        "root=application(); "
        "loop=EventLoop(root, lambda: None); loop.pump(); loop.close(); root.processEvents(); print('Copied SDK and Qt passed')"
    )
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=source, env=dict(os.environ, PYTHONUTF8="1"),
        capture_output=True, timeout=75, check=False,
    )
    assert checked.returncode == 0, (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert b"Copied SDK and Qt passed" in checked.stdout


def test_qt_import_keeps_asyncio_workers_usable_in_a_fresh_interpreter() -> None:
    activate()
    script = (
        "from runtime import activate; activate(); import asyncio; "
        "import assistant.qt_text; "
        "assert asyncio.run(asyncio.to_thread(lambda: 42)) == 42; print('Qt and async workers passed')"
    )
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"),
        capture_output=True, timeout=30, check=False,
    )
    assert checked.returncode == 0, (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert b"Qt and async workers passed" in checked.stdout


def test_bundled_interpreter_cannot_import_tkinter_and_owns_the_ui_loop() -> None:
    if not (ROOT / ".python").is_dir():
        raise unittest.SkipTest("Requires the rebuilt Windows bundle")
    script = (
        "from runtime import activate; activate(); import importlib.util,threading; "
        "assert importlib.util.find_spec('tkinter') is None; assert importlib.util.find_spec('_tkinter') is None; "
        "from assistant.ui import Ui; ui=Ui(); ready=threading.Event(); "
        "ui.post(lambda app: (ready.set(),ui.close())); ui.run(); assert ready.is_set(); print('Qt lifetime without Tk passed')"
    )
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"),
        capture_output=True, timeout=45, check=False,
    )
    assert checked.returncode == 0, (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert b"Qt lifetime without Tk passed" in checked.stdout


def test_work_window_closes_with_its_ui_owner_in_a_fresh_process(tmp_path: Path) -> None:
    script = textwrap.dedent("""\
        import faulthandler
        import threading
        from runtime import activate
        activate()
        from assistant.qt import QApplication
        from assistant.qt_ui import later
        from assistant.ui import Ui

        faulthandler.enable()

        class OffscreenUi(Ui):
            __slots__ = ()

            def _create_work(self, work):
                view = super()._create_work(work)
                view.window.move(-4000, -3000)
                return view

        ui = OffscreenUi()
        failures = []
        owners = []

        def control():
            try:
                work = ui.work(lambda command: None, focus=False)
                ready = threading.Event()
                def opened(root):
                    owners.append(threading.current_thread() is threading.main_thread())
                    later(root, 40, ready.set)
                ui.post(opened)
                assert ready.wait(10), "Work did not open on the UI thread"
                ui.close_work()
                assert work.closed_ready.is_set(), "Work did not acknowledge closing"
            except BaseException as error:
                failures.append(error)
            finally:
                ui.close()

        controller = threading.Thread(target=control, name="test application control")
        controller.start()
        ui.run()
        controller.join(timeout=10)
        assert not controller.is_alive(), "Application control did not close"
        assert not failures, failures
        assert owners == [True], "Qt widgets were created outside the main thread"
        assert QApplication.instance() is None, "The UI application remained alive"
        print("Work and UI owner closed")
        """)
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=ROOT,
        env=dict(os.environ, PYTHONUTF8="1", LOCALAPPDATA=str(tmp_path)),
        capture_output=True, timeout=60, check=False,
    )
    output = (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert checked.returncode == 0, f"Native exit {checked.returncode & 0xffffffff:#x}: {output}"
    assert "Work and UI owner closed" in output


def _native_controller_lifetime(tmp_path: Path, *, qt_first: bool) -> None:
    script = textwrap.dedent("""\
        import faulthandler
        import sys
        import threading
        import uuid
        from unittest.mock import patch
        from runtime import activate
        activate()
        from assistant import app
        from assistant.llm import Missing
        from assistant.qt import QApplication
        from assistant.ui import Ui
        from winapi import shell, system

        faulthandler.enable()
        title = "Toolkit controller lifetime " + uuid.uuid4().hex
        observed = []

        class OffscreenUi(Ui):
            __slots__ = ()

            def _create_work(self, work):
                view = super()._create_work(work)
                view.window.move(-4000, -3000)
                return view

        class Controller:
            __slots__ = ("owner", "ui", "window")

            def __init__(self, settings, ui):
                self.owner = threading.get_ident()
                assert self.owner != threading.main_thread().ident
                self.ui = ui
                self.window = shell.AppWindow(title, self.message)

            def run(self):
                self.ui.agent(None, lambda: None, reveal=False)
                self.ui.work(lambda command: None, focus=False)
                self.ui.post(self.ready)
                system.run_message_loop()

            def ready(self, root):
                observed.append(("qt", threading.current_thread() is threading.main_thread()))
                if sys.argv[1] == "qt-first":
                    root.quit()
                else:
                    assert system.post_message(self.window.handle, system.WM_CLOSE, 0, 0)

            def message(self, hwnd, message, wparam, lparam):
                if message == system.WM_CLOSE:
                    observed.append(("control", threading.get_ident() == self.owner))
                    self.ui.close_desktop()
                    self.ui.close_agent()
                    self.ui.close_work()
                    self.ui.close()
                    self.window.destroy()
                    system.post_quit()
                    return 0
                return None

        with patch.object(app, "Ui", OffscreenUi), patch.object(app, "AssistantApp", Controller), \\
             patch.object(app, "ASSISTANT_WINDOW", title), patch.object(app, "LOCK_NAME", "Local\\\\" + title):
            assert app.run(Missing(())) is app.Exit.QUIT
        assert observed == [("qt", True), ("control", True)], observed
        assert QApplication.instance() is None
        assert not any(thread.name == "assistant control" for thread in threading.enumerate())
        print("Native controller and Qt closed")
        """)
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script, "qt-first" if qt_first else "controller-first"], cwd=ROOT,
        env=dict(os.environ, PYTHONUTF8="1", LOCALAPPDATA=str(tmp_path)),
        capture_output=True, timeout=60, check=False,
    )
    output = (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert checked.returncode == 0, f"Native exit {checked.returncode & 0xffffffff:#x}: {output}"
    assert "Native controller and Qt closed" in output


def test_native_controller_messages_and_qt_share_a_clean_process_lifetime(tmp_path: Path) -> None:
    _native_controller_lifetime(tmp_path, qt_first=False)


def test_qt_exit_first_still_closes_the_native_controller(tmp_path: Path) -> None:
    _native_controller_lifetime(tmp_path, qt_first=True)


def test_controller_initialization_failure_closes_qt_and_propagates(tmp_path: Path) -> None:
    script = textwrap.dedent("""\
        import threading
        import uuid
        from unittest.mock import patch
        from runtime import activate
        activate()
        from assistant import app
        from assistant.llm import Missing
        from assistant.qt import QApplication

        title = "Toolkit startup failure " + uuid.uuid4().hex
        with patch.object(app, "AssistantApp", side_effect=RuntimeError("Fixture startup failed")), \\
             patch.object(app, "ASSISTANT_WINDOW", title), patch.object(app, "LOCK_NAME", "Local\\\\" + title):
            try:
                app.run(Missing(()))
            except RuntimeError as error:
                assert str(error) == "Fixture startup failed"
            else:
                raise AssertionError("The controller startup failure was lost")
        assert QApplication.instance() is None
        assert not any(thread.name == "assistant control" for thread in threading.enumerate())
        print("Controller failure closed Qt")
        """)
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=ROOT,
        env=dict(os.environ, PYTHONUTF8="1", LOCALAPPDATA=str(tmp_path)),
        capture_output=True, timeout=60, check=False,
    )
    output = (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert checked.returncode == 0, f"Native exit {checked.returncode & 0xffffffff:#x}: {output}"
    assert "Controller failure closed Qt" in output


def test_shutdown_resolves_pending_and_late_ui_requests(tmp_path: Path) -> None:
    script = textwrap.dedent("""\
        import threading
        from unittest.mock import patch
        from runtime import activate
        activate()
        from assistant import ui as module
        from assistant.qt import Qt, QtWidgets
        from assistant.qt_ui import later

        ui = module.Ui()
        answers = []
        failures = []

        def position(dialog, width, height):
            dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            dialog.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
            dialog.setGeometry(-4000, -3000, width, height)

        def close_pending(root, attempts=0):
            if any(isinstance(window, QtWidgets.QInputDialog) for window in root.topLevelWidgets()):
                root.quit()
            elif attempts < 100:
                later(root, 10, lambda: close_pending(root, attempts + 1))
            else:
                failures.append("The pending prompt never appeared")
                root.quit()

        def control():
            try:
                answers.append(ui.prompt("Isolated shutdown check"))
                ui.close()
                answers.append(ui.prompt("Already closed"))
                assert ui.choose_region(module.Rect(0, 0, 100, 100)) is None
                try:
                    ui.answer("Already closed", lambda text: None)
                except OSError:
                    pass
                else:
                    raise AssertionError("A closed UI must reject window creation")
            except BaseException as error:
                failures.append(error)

        controller = threading.Thread(target=control, name="pending UI request", daemon=True)
        with patch.object(module, "_near_pointer", side_effect=position), patch.object(module, "_bring_forward"):
            controller.start()
            ui.post(close_pending)
            ui.run()
            controller.join(timeout=10)
        assert not controller.is_alive(), "Shutdown left an input caller waiting"
        assert not failures, failures
        assert answers == [None, None], answers
        print("Pending and late UI requests resolved")
        """)
    checked = subprocess.run(
        [sys.executable, "-S", "-c", script], cwd=ROOT,
        env=dict(os.environ, PYTHONUTF8="1", LOCALAPPDATA=str(tmp_path)),
        capture_output=True, timeout=60, check=False,
    )
    output = (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
    assert checked.returncode == 0, f"Native exit {checked.returncode & 0xffffffff:#x}: {output}"
    assert "Pending and late UI requests resolved" in output
