"""The agent execution adapter owns SDK history, workspace, and turn cleanup."""

import contextlib
import threading
import time
import traceback
from collections.abc import Callable, Mapping

from usage import Detail

from .activity import ContextSnapshot
from .agent import Agent, Log, Machine
from .agent_runner import Task
from .llm import Json, Settings
from .workspace import Workspace


class AgentExecution:
    __slots__ = ("close_background", "context", "history", "journal", "machine", "record", "report_error", "settings", "workspace")

    def __init__(
        self, settings: Settings, machine: Machine, *, journal: Callable[[dict[str, Json]], None],
        record: Callable[[Mapping[str, Detail]], None], report_error: Callable[[str], None], close_background: Callable[[], None],
        context: Callable[[], ContextSnapshot] | None = None,
    ) -> None:
        self.settings, self.machine, self.context = settings, machine, context
        self.journal, self.record, self.report_error, self.close_background = journal, record, report_error, close_background
        self.history: list[Json] = []
        self.workspace = Workspace()

    def run(self, task: Task, log: Log, stop: threading.Event) -> None:
        started = time.monotonic()
        finished = False
        agent = Agent(
            self.settings, self.machine, log, stop, journal=self.journal, workspace=self.workspace, history=self.history,
            context=task.context, context_reader=self.context,
        )
        try:
            finished = agent.run(task.text)
        except Exception:
            self.report_error(traceback.format_exc())
            log.problem("Something went wrong; it's in the assistant's log.")
        finally:
            if stop.is_set():
                self.close_background()
            with contextlib.suppress(OSError):
                self.record({"ok": finished, "seconds": round(time.monotonic() - started, 1), **agent.timings})

    def reset(self) -> None:
        self.history.clear()
        self.workspace = Workspace()
