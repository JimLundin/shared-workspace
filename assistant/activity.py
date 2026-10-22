"""Short-lived work context: window changes in memory, sampled without input hooks."""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, assert_never

from winapi.shapes import Window

from .llm import Json

type ContextState = Literal["active", "toolkit", "unavailable", "disabled"]
type Observation = Window | Literal["toolkit", "unavailable", "disabled"]


@dataclass(frozen=True, slots=True)
class Activity:
    window: Window
    started: float
    seen: float
    agent_active: bool

    def data(self) -> dict[str, Json]:
        return {
            "handle": self.window.handle, "app": self.window.exe[:256], "title": self.window.title[:1000],
            "started": self.started, "last_seen": self.seen, "agent_active": self.agent_active,
        }


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    captured: float
    state: ContextState
    recent: tuple[Activity, ...] = ()

    @property
    def window(self) -> Window | None:
        return self.recent[-1].window if self.recent and self.state in ("active", "toolkit") else None

    def describe(self, limit: int = 12) -> str:
        data: dict[str, Json] = {
            "captured": self.captured, "desktop_state": self.state,
            "work_window": self.recent[-1].data() if self.window is not None else None,
            "recent_activity": [entry.data() for entry in self.recent[-max(1, min(limit, 64)):]],
        }
        return json.dumps(data, ensure_ascii=False)


def observed(
    previous: ContextSnapshot, observation: Observation, now: float, *, agent_active: bool = False,
    max_age: float = 1800, capacity: int = 128,
) -> ContextSnapshot:
    """Age, coalesce, and cap window history without reading the desktop or clock."""
    if capacity < 0:
        raise ValueError("Activity capacity must be nonnegative.")
    expired = 0
    while expired < len(previous.recent) and now - previous.recent[expired].seen > max_age:
        expired += 1
    entries = previous.recent[expired:]
    match observation:
        case Window() as window:
            last = entries[-1] if entries else None
            if last is not None and last.window == window and last.agent_active == agent_active:
                entries = (*entries[:-1], replace(last, seen=now))
            else:
                entries = (*entries, Activity(window, now, now, agent_active))
            return ContextSnapshot(now, "active", entries[-capacity:] if capacity else ())
        case "toolkit" | "unavailable":
            return ContextSnapshot(now, observation, entries[-capacity:] if capacity else ())
        case "disabled":
            return ContextSnapshot(now, "disabled")
        case _:
            assert_never(observation)


class ActivityTracker:
    """Poll only foreground app/title changes; never capture idle screenshots or keys."""

    __slots__ = ("_state", "agent_active", "capacity", "enabled", "lock", "max_age", "read", "stopped", "thread")

    def __init__(
        self, read: Callable[[], Observation], agent_active: Callable[[], bool],
        *, enabled: bool = True, max_age: float = 1800, capacity: int = 128,
    ) -> None:
        self.read, self.agent_active, self.enabled, self.max_age = read, agent_active, enabled, max_age
        if capacity < 0:
            raise ValueError("Activity capacity must be nonnegative.")
        self.capacity = capacity
        self.lock = threading.Lock()
        self._state = ContextSnapshot(0, "unavailable" if enabled else "disabled")
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def observe(self, observation: Observation, now: float, *, agent_active: bool = False) -> ContextSnapshot:
        with self.lock:
            if self.stopped.is_set():
                return self._state
            self._state = observed(self._state, observation if self.enabled else "disabled", now,
                                   agent_active=agent_active, max_age=self.max_age, capacity=self.capacity)
            return self._state

    def snapshot(self) -> ContextSnapshot:
        if self.stopped.is_set():
            with self.lock:
                return self._state
        if not self.enabled:
            return ContextSnapshot(time.time(), "disabled")
        try:
            observation = self.read()
        except OSError:
            observation = "unavailable"
        return self.observe(observation, time.time(), agent_active=self.agent_active())

    def start(self) -> None:
        if self.enabled and self.thread is None:
            self.thread = threading.Thread(target=self.run, name="work context", daemon=True)
            self.thread.start()

    def run(self) -> None:
        while not self.stopped.is_set():
            self.snapshot()
            self.stopped.wait(1)

    def close(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        with self.lock:
            self._state = ContextSnapshot(time.time(), "disabled")
