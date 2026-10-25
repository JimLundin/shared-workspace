"""The agent execution adapter owns SDK history, workspace, and turn cleanup."""

import contextlib
import hashlib
import sqlite3
import threading
import time
import traceback
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from automation.actions import ActionError, Resources, target
from automation.execution import ApprovalReview, Incomplete, Outcome, WorkExecution
from automation.store import TaskStore
from automation.task import ActionVerified, Blocked, CanvasSource, Claim, RequestSource, Waiting
from keeper import source_digest
from usage import Detail

from .activity import ContextSnapshot
from .agent import Agent, Log, Machine
from .agent_runner import Task
from .canvas_execution import CanvasWork
from .llm import Json, Settings
from .meeting_execution import MeetingWork
from .workspace import Workspace


@dataclass(slots=True)
class Conversation:
    history: list[Json]
    workspace: Workspace


class AgentExecution:
    __slots__ = (
        "canvas", "close_background", "context", "conversations", "history", "journal", "machine", "meeting", "record",
        "report_error", "resources", "settings", "workspace",
    )

    def __init__(
        self, settings: Settings, machine: Machine, *, journal: Callable[[dict[str, Json]], None],
        record: Callable[[Mapping[str, Detail]], None], report_error: Callable[[str], None], close_background: Callable[[], None],
        context: Callable[[], ContextSnapshot] | None = None,
        workspace: Workspace | None = None, resources: Resources | None = None,
        meeting: MeetingWork | None = None,
        canvas: CanvasWork | None = None,
    ) -> None:
        self.settings, self.machine, self.context = settings, machine, context
        self.journal, self.record, self.report_error, self.close_background = journal, record, report_error, close_background
        self.history: list[Json] = []
        self.workspace = workspace if workspace is not None else Workspace()
        self.resources = resources if resources is not None else Resources()
        self.meeting = meeting
        self.canvas = canvas
        self.conversations: OrderedDict[str, Conversation] = OrderedDict()

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
        self.workspace = Workspace(self.workspace.live, self.workspace.root)

    def conversation(self, claim: Claim) -> Conversation:
        source = claim.task.source
        name = "conversation:" + source.conversation_id if isinstance(source, RequestSource) else "task:" + claim.task.id
        return self._conversation(name)

    def _conversation(self, name: str) -> Conversation:
        key = hashlib.sha256(name.encode("utf-8")).hexdigest()
        if key not in self.conversations:
            draft = self.workspace.root.parent / "workspaces" / key
            self.conversations[key] = Conversation([], Workspace(self.workspace.live, draft, resume=True))
        self.conversations.move_to_end(key)
        while len(self.conversations) > 8:
            self.conversations.popitem(last=False)
        return self.conversations[key]

    def run_work(
        self, claim: Claim, store: TaskStore, log: Log, stop: threading.Event, *,
        context: ContextSnapshot | None = None, context_text: str = "", review: Callable[[ApprovalReview], bool] | None = None,
    ) -> Outcome:
        """Execute one durable claim; the controller commits the returned disposition."""
        started = time.monotonic()
        try:
            if isinstance(claim.task.source, CanvasSource) and self.canvas is None:
                raise ValueError("Captured canvas execution is not configured for this work process.")
            canvas = self.canvas.session(claim.task) if self.canvas is not None else None
            if canvas is None and self.meeting is None and claim.task.id.startswith("meeting-followup-"):
                raise ValueError("Meeting follow-up is not configured for this work process.")
            followup = self.meeting.session(claim.task) if self.meeting is not None and canvas is None else None
        except (OSError, ValueError, sqlite3.Error) as error:
            kind = "Canvas" if isinstance(claim.task.source, CanvasSource) else "Meeting"
            reason = f"{kind} preparation could not be opened: {error}"[:4000]
            log.problem(reason)
            return Waiting(Blocked(reason))
        selected = canvas is not None or followup is not None
        work = WorkExecution(claim, store, self.resources, context_text="" if selected else context_text, review=review)
        session = self._conversation("task:" + claim.task.id) if selected else self.conversation(claim)
        agent = Agent(
            self.settings, self.machine, log, stop, journal=self.journal, workspace=session.workspace, history=session.history,
            context=None if selected else context, context_reader=None if selected else self.context,
            work=work, install_root=self.workspace.root, followup=followup, canvas=canvas,
        )
        try:
            if stop.is_set():
                return Waiting(Blocked("Execution stopped before it started."))
            work.actions.reconcile(stop)
            if followup is not None:
                followup.recover(work, stop)
            for action in store.unverified_actions(claim.task.id):
                if action.specification.tool != "apply_changes":
                    continue
                expected = "source-digest:" + source_digest(session.workspace.live)
                location = target("installed-root", str(session.workspace.live.absolute()))
                if expected not in action.specification.resources or location not in action.specification.resources:
                    return Waiting(Blocked(
                        "The keeper's installed source does not match the requested update. Inspect its installation result before retrying.",
                    ))
                if store.finish_action(
                    action.id, ActionVerified("The running source matches the checked installation's SHA-256 digest."), at=time.time(),
                ) is None:
                    return Waiting(Blocked("The installed source matched, but verification could not be recorded. Resume to inspect it again."))
            agent.run(claim.task.prompt)
            outcome = work.outcome(agent.report, stop)
            return followup.completed(outcome) if followup is not None else outcome
        except (ActionError, OSError, ValueError) as error:
            self.report_error(traceback.format_exc())
            reason = "Execution could not establish a durable result. Resume to inspect its state."
            log.problem(reason)
            agent.report = Incomplete(str(error))
            return Waiting(Blocked(reason))
        except Exception:
            self.report_error(traceback.format_exc())
            log.problem("Something went wrong; it's in the assistant's log.")
            return Waiting(Blocked("Execution failed before a verified result. Resume to inspect its state."))
        finally:
            if stop.is_set():
                self.close_background()
            with contextlib.suppress(OSError):
                self.record({"durable": True, "seconds": round(time.monotonic() - started, 1), **agent.timings})
