"""Persistence acknowledgment follows the completed store write, including coalescing."""

import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from assistant import canvas_model as m
from assistant.canvas_controller import CanvasController
from assistant.canvas_store import CanvasStore

from .test_canvas import RecordingBackend


class GatedStore(CanvasStore):
    __slots__ = ("entered", "failure", "release")

    def __init__(self, path: Path, *, failure: bool = False) -> None:
        super().__init__(path)
        self.entered, self.release = threading.Event(), threading.Event()
        self.failure = failure

    def save(self, canvas: m.Canvas) -> None:
        self.entered.set()
        if not self.release.wait(15):
            raise OSError("The test did not release the store.")
        if self.failure:
            raise OSError("The fixture disk is unavailable.")
        super().save(canvas)


class TwoWriteStore(CanvasStore):
    __slots__ = ("first_entered", "first_release", "second_entered", "second_release")

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.first_entered, self.first_release = threading.Event(), threading.Event()
        self.second_entered, self.second_release = threading.Event(), threading.Event()

    def save(self, canvas: m.Canvas) -> None:
        entered, release = (self.second_entered, self.second_release) if self.first_entered.is_set() else (self.first_entered, self.first_release)
        entered.set()
        if not release.wait(15):
            raise OSError("The test did not release this write.")
        super().save(canvas)


def until(condition: Callable[[], bool]) -> None:
    end = time.monotonic() + 3
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("The writer did not publish its save observation.")
        time.sleep(.005)


def test_save_acknowledgment_waits_for_write_and_tracks_the_latest_coalesced_state(tmp_path: Path) -> None:
    store = GatedStore(tmp_path / "canvas.json")
    controller = CanvasController(store, RecordingBackend(), changed=lambda: None)
    item = m.Item("web-app", "Opening Office", m.WebApp("https://www.microsoft365.com/", "default"))
    try:
        controller.commit(m.add(controller.state, item))
        assert store.entered.wait(2)
        assert controller.saved_state is not None and controller.saved_state.item(item.id) is None
        redirected = replace(item, title="Sign in", content=m.WebApp("https://login.microsoftonline.com/", "default"))
        controller.commit(m.put(controller.state, redirected))
        for index in range(20):
            controller.commit(m.with_space(controller.state, replace(controller.state.space, composer=f"Draft {index}")))
        assert controller.saved_state.item(item.id) is None
    finally:
        store.release.set()
        controller.close()
    assert controller.saved_state == controller.state == store.load()
    assert controller.saved_state.item(item.id) == redirected
    assert controller.save_failure is None


def test_acknowledgment_identifies_the_completed_write_while_the_next_write_is_blocked(tmp_path: Path) -> None:
    store = TwoWriteStore(tmp_path / "canvas.json")
    controller = CanvasController(store, RecordingBackend(), changed=lambda: None)
    first = m.add(controller.state, m.Item("note", "Working note", m.Note("First write")))
    second = m.edit(first, "note", "Second write")
    try:
        controller.commit(first)
        assert store.first_entered.wait(2)
        controller.commit(second)
        store.first_release.set()
        assert store.second_entered.wait(2)
        assert controller.state == second
        assert controller.saved_state == first == store.load()
    finally:
        store.first_release.set()
        store.second_release.set()
        controller.close()
    assert controller.saved_state == second == store.load()


def test_failed_save_retains_previous_acknowledgment_and_identifies_the_attempt(tmp_path: Path) -> None:
    path = tmp_path / "canvas.json"
    initial = m.add(m.Canvas(), m.Item("note", "Saved note", m.Note("Keep me")))
    CanvasStore(path).save(initial)
    store = GatedStore(path, failure=True)
    controller = CanvasController(store, RecordingBackend(), changed=lambda: None)
    proposed = m.add(initial, m.Item("web-app", "VS Code", m.WebApp("https://vscode.dev/", "default")))
    try:
        controller.commit(proposed)
        assert store.entered.wait(2)
        store.release.set()
        until(lambda: controller.save_failure is not None)
        assert controller.saved_state == initial == CanvasStore(path).load()
        assert controller.save_failure is not None and controller.save_failure.state == proposed
        assert "fixture disk" in controller.save_failure.reason
        controller.drain()
        assert "could not be saved" in controller.status
    finally:
        store.release.set()
        with unittest.TestCase().assertRaises(OSError):
            controller.close()


def test_a_successful_retry_clears_the_failed_save_and_allows_clean_shutdown(tmp_path: Path) -> None:
    store = GatedStore(tmp_path / "canvas.json", failure=True)
    store.release.set()
    controller = CanvasController(store, RecordingBackend(), changed=lambda: None)
    first = m.add(controller.state, m.Item("note", "Working note", m.Note("Unconfirmed write")))
    recovered = m.edit(first, "note", "Confirmed write")
    try:
        controller.commit(first)
        until(lambda: controller.save_failure is not None)
        failure = controller.save_failure
        assert failure is not None and failure.state == first
        store.failure = False
        controller.commit(recovered)
        controller.close()
        assert controller.saved_state == recovered == store.load()
        assert controller.save_failure is None
    finally:
        if not controller.closed:
            store.failure = False
            controller.commit(recovered)
            controller.close()


def test_recovery_state_is_not_acknowledged_until_its_own_file_is_saved(tmp_path: Path) -> None:
    path = tmp_path / "canvas.json"
    path.write_text('{"version":999}', encoding="utf-8")
    controller = CanvasController(CanvasStore(path), RecordingBackend(), changed=lambda: None)
    try:
        initial_saved = controller.saved_state
        assert initial_saved is None
        controller.commit(m.add(controller.state, m.Item("app", "VS Code", m.WebApp("https://vscode.dev/", "default"))))
    finally:
        controller.close()
    assert controller.saved_state == controller.state
    assert controller.store.path != path and '999' in path.read_text(encoding="utf-8")
