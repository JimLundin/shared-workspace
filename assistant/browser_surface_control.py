"""Run-bound app-page RPC beside the existing independent Edge capabilities."""

import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, assert_never

from automation.bridge import StopSignal
from automation.task import RunRef, variant

from .browser import BrowserResult
from .browser_surface_operations import AppCancelled, AppFailed, AppOperation, AppResult, AppSucceeded, app_operation
from .browser_values import BrowserError, InterruptedError
from .llm import Json


class BrowserEndpoint(Protocol):
    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult: ...
    def close(self) -> None: ...


class AppBridge(Protocol):
    def browser_app(self, run: RunRef, operation: AppOperation, stop: StopSignal, *, timeout_s: float = 20) -> AppResult: ...


@dataclass(frozen=True, slots=True)
class _BoundRun:
    run: RunRef
    stop: threading.Event
    closed: threading.Event


class _Unbound(Enum):
    CONTROL = auto()


@dataclass(frozen=True, slots=True)
class _RequestStop:
    task: threading.Event
    request: threading.Event
    closed: threading.Event

    def is_set(self) -> bool:
        return self.task.is_set() or self.request.is_set() or self.closed.is_set()


class AppBrowserControl:
    """Each ordinary run borrows control; closing never owns an app page/profile."""

    __slots__ = ("_binding", "_lock", "bridge")

    def __init__(self, bridge: AppBridge) -> None:
        self.bridge = bridge
        self._binding: _BoundRun | _Unbound = _Unbound.CONTROL
        self._lock = threading.Lock()

    @contextmanager
    def bind(self, run: RunRef, stop: threading.Event) -> Generator[None]:
        variant(run, RunRef, "browser app run")
        binding = _BoundRun(run, stop, threading.Event())
        with self._lock:
            if isinstance(self._binding, _BoundRun):
                raise RuntimeError("Browser app control is already bound to a work run.")
            self._binding = binding
        try:
            yield
        finally:
            binding.closed.set()
            with self._lock:
                if self._binding is binding:
                    self._binding = _Unbound.CONTROL

    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        try:
            selected = app_operation(operation, args)
        except ValueError as error:
            raise BrowserError(str(error)) from error
        with self._lock:
            binding = self._binding
        if not isinstance(binding, _BoundRun):
            raise BrowserError("No ordinary work run owns browser app control.")
        stop = _RequestStop(binding.stop, cancel, binding.closed)
        if stop.is_set():
            raise InterruptedError("Browser app control has stopped.")
        result = self.bridge.browser_app(binding.run, selected, stop)
        if stop.is_set():
            raise InterruptedError("Browser app control stopped. Inspect the card before retrying; its action may have completed.")
        match result:
            case AppSucceeded(message):
                return BrowserResult(message)
            case AppFailed(reason):
                raise BrowserError(reason)
            case AppCancelled.REQUEST:
                raise InterruptedError("The app request was cancelled. Inspect the card before retrying; its action may have completed.")
            case _:
                assert_never(result)

    def close(self) -> None:
        with self._lock:
            if isinstance(self._binding, _BoundRun):
                self._binding.closed.set()


class CompositeBrowser:
    """Only explicit app_* operations reach app cards; Edge keeps its own mode."""

    __slots__ = ("apps", "edge")

    def __init__(self, edge: BrowserEndpoint, apps: AppBrowserControl) -> None:
        self.edge, self.apps = edge, apps

    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        if operation.startswith("app_"):
            return self.apps.request(operation, args, cancel)
        return self.edge.request(operation, args, cancel)

    def close(self) -> None:
        try:
            self.apps.close()
        finally:
            self.edge.close()
