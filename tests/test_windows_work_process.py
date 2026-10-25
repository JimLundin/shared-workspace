"""Isolated native worker lifetimes, real journals, local pipes, and HWND cleanup."""

import contextlib
import os
import socket
import subprocess
import sys
import unittest
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

if sys.platform != "win32":
    raise unittest.SkipTest("Windows work process ownership")

from automation.bridge import BridgeClient, ProfileLease, read_discovery
from automation.routines import Disabled, RoutineStore
from automation.store import TaskStore
from automation.task import Interrupted, RequestSource, Running, RunRef
from winapi import system, windows

ROOT = Path(__file__).resolve().parent.parent
_PROGRAM = r'''
import os, socket, sys, threading
from pathlib import Path
from runtime import activate
activate()
from automation.app import run
from automation.store import TaskStore
from automation.task import Completed, Interrupted, Running
from assistant.llm import Missing
from winapi import windows

folder, title, port, mode = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
channel = socket.create_connection(("127.0.0.1", port), timeout=10)
lock = threading.Lock()
def notify(message):
    with lock:
        channel.sendall((message + "\n").encode("utf-8"))

class Execution:
    def run_work(self, claim, store, log, stop):
        assert isinstance(claim.previous, Interrupted), claim.previous
        assert claim.task.checkpoint.summary == "Read and checked the transcript."
        # A real execution-owned file is closed before controller.wait returns.
        with (folder / "execution-resource.txt").open("w", encoding="utf-8") as resource:
            resource.write(claim.run.run_id)
            resource.flush()
            log.step("Awaiting the native shutdown request.")
            notify("claimed:" + claim.run.run_id)
            assert stop.wait(20), "The worker did not cancel its execution adapter."
        notify("cleaned:" + claim.run.run_id)
        return Completed("Adapter returned while shutting down.", ("Owned file released.",))

def ready():
    hwnd = windows.find(title)
    assert hwnd and windows.process_id(hwnd) == os.getpid()
    for module in ("assistant.app", "assistant.desktop", "assistant.ui", "winapi.audio", "winapi.recording", "tkinter", "PySide6"):
        assert not any(name == module or name.startswith(module + ".") for name in sys.modules), module
    if mode == "fixture":
        task = TaskStore(folder / "work.sqlite3").get("notes")
        assert task is not None
        assert not isinstance(task.state, Running) or task.state.owner != "dead-worker", "Readiness preceded recovery."
    notify("ready")

try:
    run(Missing(("OPENAI_API_KEY",)), folder=folder, title=title, ready=ready,
        execution=Execution() if mode == "fixture" else None)
    assert windows.find(title) is None, "The worker's native HWND survived cleanup."
    notify("closed")
finally:
    channel.close()
'''


@dataclass(slots=True)
class NativeWorker:
    process: subprocess.Popen[bytes]
    peer: socket.socket
    title: str

    def receive(self) -> str:
        message = bytearray()
        while len(message) < 256:
            character = self.peer.recv(1)
            if not character:
                stdout, stderr = self.process.communicate(timeout=5)
                raise AssertionError((stdout + stderr).decode("utf-8", errors="replace"))
            if character == b"\n":
                return message.decode("utf-8")
            message.extend(character)
        raise AssertionError("The worker fixture sent an unbounded status.")

    def close_window(self) -> None:
        hwnd = windows.find(self.title)
        assert hwnd is not None and windows.process_id(hwnd) == self.process.pid
        assert system.post_message(hwnd, system.WM_CLOSE, 0, 0)


@contextlib.contextmanager
def worker(folder: Path, *, mode: str) -> Generator[NativeWorker]:
    title = "toolkit automation test " + uuid.uuid4().hex
    with socket.create_server(("127.0.0.1", 0)) as server:
        server.settimeout(12)
        port = cast(tuple[str, int], server.getsockname())[1]
        environment = dict(os.environ, LOCALAPPDATA=str(folder / "profile"), OPENAI_API_KEY="", OPENAI_BASE_URL="", PYTHONUTF8="1")
        process: subprocess.Popen[bytes] = subprocess.Popen(
            [sys.executable, "-c", _PROGRAM, str(folder), title, str(port), mode],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            peer, _address = server.accept()
            with peer:
                peer.settimeout(12)
                yield NativeWorker(process, peer, title)
        finally:
            if process.poll() is None:
                process.kill()  # Only this isolated fixture child; never the desktop Toolkit.
            stdout, stderr = process.communicate(timeout=10)
        assert process.wait(timeout=10) == 0, (stdout + stderr).decode("utf-8", errors="replace")


def test_native_worker_recovers_parks_and_restarts_without_ui_or_audio(tmp_path: Path) -> None:
    tasks = TaskStore(tmp_path / "work.sqlite3")
    tasks.submit("notes", "Meeting notes", "Prepare meeting notes.", RequestSource("conversation"), at=1)
    first = tasks.claim("dead-worker", "abruptly-lost-run", at=2)
    assert first is not None
    tasks.progress(first.run, "Read and checked the transcript.", at=3)
    previous = first.run
    for _ in range(2):
        with worker(tmp_path, mode="fixture") as native:
            signals = {native.receive(), native.receive()}
            assert "ready" in signals
            claimed = next(message.removeprefix("claimed:") for message in signals if message.startswith("claimed:"))
            active = tasks.get("notes")
            assert active is not None and isinstance(active.state, Running)
            assert active.state.run == RunRef("notes", claimed) and active.state.run != previous
            with unittest.TestCase().assertRaises(OSError), ProfileLease(tmp_path):
                raise AssertionError("A second process acquired the active worker profile.")
            client = BridgeClient(tmp_path)
            try:
                assert client.connect() == read_discovery(tmp_path).epoch
                client.wake()
            finally:
                client.close()
            native.close_window()
            assert native.receive() == "cleaned:" + claimed
            assert native.receive() == "closed"
            assert native.process.wait(timeout=10) == 0
            previous = RunRef("notes", claimed)
        parked = tasks.get("notes")
        assert parked is not None and isinstance(parked.state, Interrupted)
        assert parked.state.run == previous
        assert parked.checkpoint is not None and parked.checkpoint.summary == "Read and checked the transcript."
        assert (tmp_path / "execution-resource.txt").read_text(encoding="utf-8") == previous.run_id
        assert not (tmp_path / "worker.json").exists()


def test_native_worker_constructs_default_machine_and_disabled_routine(tmp_path: Path) -> None:
    with worker(tmp_path, mode="native") as native:
        assert native.receive() == "ready"
        client = BridgeClient(tmp_path)
        try:
            assert client.connect() == read_discovery(tmp_path).epoch
            assert client.poll()
        finally:
            client.close()
        routines = RoutineStore(tmp_path / "work.sqlite3").list()
        assert len(routines) == 1 and isinstance(routines[0].state, Disabled)
        native.close_window()
        assert native.receive() == "closed"
        assert native.process.wait(timeout=10) == 0
