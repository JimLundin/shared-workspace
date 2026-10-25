"""Cancellable effect ownership and a durable record before every consequential call."""

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from assistant.browser_surface_operations import target_card_id

from .files import filesystem_path
from .task import (
    Action,
    ActionNotApplied,
    ActionOutcome,
    ActionReturned,
    ActionSpec,
    ActionUncertain,
    ActionVerified,
    FileDigest,
    NoVerification,
    RunRef,
    Task,
    Verification,
    owns,
)

MAX_VERIFICATION_BYTES = 64 * 1024 * 1024


class ActionError(Exception):
    """The effect cannot proceed or its outcome cannot be established."""


class ActionCancelledError(ActionError):
    pass


class LostRunError(ActionError):
    pass


class UncertainEffectError(ActionError):
    pass


class ActionJournal(Protocol):
    def get(self, task_id: str, /) -> Task | None: ...
    def begin_action(self, run: RunRef, action_id: str, specification: ActionSpec, *, at: float) -> Action | None: ...
    def finish_action(self, action_id: str, outcome: ActionOutcome, *, at: float) -> Action | None: ...
    def actions(self, task_id: str, *, limit: int = 100) -> tuple[Action, ...]: ...
    def unresolved_actions(self, task_id: str) -> tuple[Action, ...]: ...
    def unverified_actions(self, task_id: str) -> tuple[Action, ...]: ...
    def action_since(self, task_id: str, fingerprint: str, *, after: float) -> bool: ...


@dataclass(frozen=True, slots=True)
class Observation:
    resources: tuple[str, ...]


type Plan = Observation | ActionSpec


_READS = frozenset({
    "list_directory", "read_file", "find_files", "get_clipboard", "list_windows", "desktop_state", "get_context",
    "inspect_ui", "screenshot", "wait", "ask_user", "list_source", "read_source", "show_changes",
    "work_actions", "reconcile_action",
    "browser_tabs", "browser_snapshot", "browser_screenshot", "browser_app_list", "browser_app_snapshot",
    "teams_status", "teams_find_users", "teams_list_chats", "teams_read_messages", "meeting_status", "meeting_list",
    "meeting_read_sources", "meeting_followup_result",
})
_FOREGROUND = frozenset({
    "focus_window", "manage_window", "click", "move_mouse", "drag", "type_text", "paste_text", "press_keys", "scroll",
})


def fingerprint(tool: str, arguments: Mapping[str, object]) -> str:
    """Bind a review or record to the exact operation without retaining its arguments."""
    values: dict[str, object] = dict(arguments)
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256((tool + "\0" + encoded).encode("utf-8")).hexdigest()


def ownership(tool: str, arguments: Mapping[str, object]) -> tuple[str, ...]:
    if tool == "meeting_save_draft":
        return ("outlook", "filesystem")
    if tool in ("meeting_read_sources", "meeting_publish_followup", "meeting_followup_result"):
        return ("filesystem",)
    if tool in ("run_powershell", "check_changes", "publish_changes"):
        return ("exclusive",)
    if tool in _FOREGROUND or tool in ("ui_action", "inspect_ui", "screenshot", "desktop_state", "list_windows"):
        return ("foreground", "clipboard") if tool == "paste_text" else ("foreground",)
    if tool in ("get_clipboard", "set_clipboard"):
        return ("clipboard",)
    if tool.startswith("browser_app_"):
        return ("browser-app",)
    if tool.startswith("browser_"):
        return ("browser", "foreground") if tool == "browser_sign_in" or (
            tool == "browser_tab" and arguments.get("action") == "activate"
        ) else ("browser",)
    if tool.startswith("teams_"):
        return ("teams",)
    if tool.startswith("meeting_"):
        return ("meeting",)
    if tool.endswith("_source") or tool.endswith("_changes"):
        return ("source",)
    if tool in ("write_file", "read_file", "find_files", "list_directory"):
        return ("filesystem",)
    return ("foreground",) if tool == "open" else ()


def target(kind: str, value: object) -> str:
    """Keep target identity for recovery without storing integration content."""
    return "target:" + kind + ":" + fingerprint(kind, {"value": value})


def resources(tool: str, arguments: Mapping[str, object]) -> tuple[str, ...]:
    """Ownership and inspectable target markers have separate matching rules."""
    selected = ownership(tool, arguments)
    if tool in ("meeting_publish_followup", "meeting_followup_result"):
        task_id, version = arguments.get("task_id"), arguments.get("version")
        if isinstance(task_id, str) and isinstance(version, str) and version:
            return (*selected, target("meeting-package", (task_id, version)))
    if tool == "meeting_save_draft":
        task_id, draft_id, marker = arguments.get("task_id"), arguments.get("draft_id"), arguments.get("mail_target")
        if isinstance(task_id, str) and isinstance(draft_id, str) and isinstance(marker, str):
            return (*selected, target("meeting-mail", (task_id, draft_id)), marker)
    if tool in ("read_file", "write_file") and isinstance(path := arguments.get("path"), str):
        return (*selected, target("file", os.path.normcase(str(Path(path).expanduser().absolute()))))
    if tool in ("read_source", "write_source", "edit_source", "delete_source") and isinstance(path := arguments.get("path"), str):
        return (*selected, target("source", os.path.normcase(os.path.normpath(path.replace("\\", "/")))))
    if tool in ("check_changes", "discard_changes", "show_changes"):
        return (*selected, target("source-tree", "workspace"))
    if tool == "apply_changes":
        digest, destination = arguments.get("source_digest"), arguments.get("destination")
        if isinstance(digest, str) and len(digest) == 64 and isinstance(destination, str):
            return (*selected, target("installed-root", destination), "source-digest:" + digest)
    if tool.startswith("browser_app_"):
        if card := target_card_id(arguments.get("target_id")):
            return (*selected, target("browser-app-card", card))
        if tool == "browser_app_open":
            # Only the matching saved-card receipt verifies creation. A list
            # containing an older card at the same URL cannot prove this open.
            return (*selected, target("browser-app-open", fingerprint(tool, arguments)))
        return selected
    if tool.startswith("browser_"):
        if isinstance(page := arguments.get("target_id"), str) and page:
            return (*selected, target("browser-page", page))
        if tool == "browser_tab" and isinstance(tab := arguments.get("tab_id"), int) and not isinstance(tab, bool):
            return (*selected, target("browser-tab", tab))
        if tool == "browser_group" and isinstance(group := arguments.get("group_id"), int) and not isinstance(group, bool) and group >= 0:
            return (*selected, target("browser-group", group))
        if tool in ("browser_group", "browser_ungroup", "browser_tab", "browser_close"):
            return (*selected, target("browser-strip", "tabs"))
        return (*selected, target("browser-session", "connection"))
    if tool in ("teams_send_message", "teams_read_messages") and isinstance(chat := arguments.get("chat_id"), str):
        selected += (target("teams-chat", chat),)
        if tool == "teams_send_message" and isinstance(content := arguments.get("text"), str):
            selected += ("message-body:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),)
        return selected
    if tool == "teams_open_chat" and isinstance(recipient := arguments.get("user_id"), str):
        return (*selected, target("teams-recipient", recipient))
    if tool.startswith("teams_"):
        return (*selected, target("teams-account", "current"))
    if tool in ("focus_window", "manage_window", "inspect_ui", "ui_action", "screenshot"):
        handle = arguments.get("handle")
        if isinstance(handle, int) and handle > 0:
            return (*selected, target("window", handle))
    return selected


def _objects(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(cast("dict[str, object]", item) for item in cast("list[object]", value)[:200] if isinstance(item, dict))


def observed_resources(tool: str, arguments: Mapping[str, object], result: str) -> tuple[str, ...]:
    selected = resources(tool, arguments)
    if tool == "desktop_state":
        return (*selected, "all:window")
    if tool not in ("browser_tabs", "browser_app_list", "teams_list_chats"):
        return selected
    try:
        raw: object = json.loads(result)
    except ValueError:
        return selected
    if not isinstance(raw, dict):
        return selected
    data = cast("dict[str, object]", raw)
    if tool == "browser_app_list":
        for row in _objects(data.get("apps")):
            if card := target_card_id(row.get("target_id")):
                selected += (target("browser-app-card", card),)
        return selected
    if tool == "teams_list_chats":
        for chat in _objects(data.get("chats")):
            if chat.get("chatType") == "oneOnOne":
                for member in _objects(chat.get("members")):
                    if isinstance(recipient := member.get("userId"), str):
                        selected += (target("teams-recipient", recipient),)
        return selected
    if data.get("view") == "strip":
        for row in _objects(data.get("tabs")):
            if isinstance(tab := row.get("tab_id"), int) and not isinstance(tab, bool):
                selected += (target("browser-tab", tab),)
        for row in _objects(data.get("groups")):
            if isinstance(group := row.get("group_id"), int) and not isinstance(group, bool):
                selected += (target("browser-group", group),)
    # A paged or filtered list cannot establish that a target was closed.
    complete = (
        arguments.get("offset", 0) == 0 and "next_offset" not in data
        and "window_id" not in arguments and "group_id" not in arguments
    )
    if complete and isinstance(data.get("tabs"), list):
        selected += (target("browser-strip", "tabs"),) if data.get("view") == "strip" else ("all:browser-page",)
    return selected


def evidence_matches(specification: ActionSpec, tool: str, observed: tuple[str, ...]) -> bool:
    """A lock category is never sufficient proof for an operation with a target."""
    if specification.tool == "browser_app_open":
        return False
    if specification.tool == "browser_app_action" and tool != "browser_app_snapshot":
        return False
    if specification.tool == "browser_app_navigate" and tool not in ("browser_app_list", "browser_app_snapshot"):
        return False
    if specification.tool == "meeting_save_draft":
        # The adapter verifies the exact saved revision and original mailbox.
        # A model assertion about another observation cannot replace that proof.
        return False
    if specification.tool == "run_powershell":
        # Arbitrary commands have no inferred target. The agent must explicitly
        # reconcile them using a later inspection of the actual requested result.
        return bool(ownership(tool, {})) and tool not in ("teams_status", "browser_tabs", "browser_app_list", "meeting_list")
    expected = tuple(item for item in specification.resources if item.startswith("target:"))
    if expected:
        broad = (
            "window" if specification.tool in ("focus_window", "manage_window")
            else "browser-page" if specification.tool == "browser_close" else ""
        )
        return all(item in observed or (
            item.startswith("target:" + broad + ":") and "all:" + broad in observed
        ) for item in expected)
    return bool(set(specification.resources).intersection(observed))


def plan(tool: str, arguments: Mapping[str, object], *, verification: Verification = NoVerification.NONE) -> Plan:
    """Unknown tools and arbitrary scripting remain consequential by default."""
    selected = resources(tool, arguments)
    if tool in _READS or (tool == "ui_action" and arguments.get("action") == "read"):
        return Observation(selected)
    return ActionSpec(tool, tool.replace("_", " "), fingerprint(tool, arguments), selected, verification)


class Resources:
    """One process owns its effects; waiting for that owner is cancellable.

    The first runtime executes one task at a time. A shared gate also prevents
    separate execution adapters from interleaving foreground and clipboard
    operations. Resource names are retained for a later independent-work policy.
    """

    __slots__ = ("_gate",)

    def __init__(self) -> None:
        self._gate = threading.Lock()

    @contextlib.contextmanager
    def hold(self, selected: tuple[str, ...], cancel: threading.Event) -> Generator[None]:
        if cancel.is_set():
            raise ActionCancelledError("Stopped before acquiring execution resources.")
        if not selected:
            yield
            return
        while not self._gate.acquire(timeout=.05):
            if cancel.is_set():
                raise ActionCancelledError("Stopped while waiting for execution resources.")
        try:
            if cancel.is_set():
                raise ActionCancelledError("Stopped before using execution resources.")
            yield
        finally:
            self._gate.release()


class FileVerifier:
    """Inspect bounded file contents without retaining the contents themselves."""

    __slots__ = ("maximum",)

    def __init__(self, maximum: int = MAX_VERIFICATION_BYTES) -> None:
        if maximum < 0:
            raise ValueError("The verification bound cannot be negative.")
        self.maximum = maximum

    def verify(self, expected: FileDigest) -> ActionVerified | ActionUncertain:
        try:
            digest = hashlib.sha256()
            size = 0
            with filesystem_path(Path(expected.path)).open("rb") as source:
                while chunk := source.read(min(65_536, self.maximum - size + 1)):
                    size += len(chunk)
                    if size > self.maximum:
                        return ActionUncertain("The file exceeds the bounded verification size.")
                    digest.update(chunk)
            if digest.hexdigest() == expected.sha256:
                return ActionVerified(f"File content matches SHA-256 {expected.sha256}.")
            return ActionUncertain("The file does not match the intended content; inspect it before another write.")
        except OSError:
            return ActionUncertain("The intended file could not be inspected; its previous write remains uncertain.")


class Actions:
    __slots__ = ("clock", "identity", "journal", "resources", "run", "verifier")

    def __init__(
        self, journal: ActionJournal, run: RunRef, resources: Resources, *,
        clock: Callable[[], float] = time.time, identity: Callable[[], str] = lambda: uuid.uuid4().hex,
        verifier: FileVerifier | None = None,
    ) -> None:
        self.journal, self.run, self.resources = journal, run, resources
        self.clock, self.identity = clock, identity
        self.verifier = verifier if verifier is not None else FileVerifier()

    def unresolved(self) -> tuple[Action, ...]:
        return self.journal.unresolved_actions(self.run.task_id)

    def reconcile(self, cancel: threading.Event) -> tuple[Action, ...]:
        """A successful readback can resolve a lost file-write acknowledgement."""
        with self.resources.hold(("filesystem",), cancel):
            for action in self.unresolved():
                if isinstance(action.specification.verification, FileDigest):
                    outcome = self.verifier.verify(action.specification.verification)
                    if isinstance(outcome, ActionVerified):
                        self.journal.finish_action(action.id, outcome, at=self.clock())
        return self.unresolved()

    def execute[T](
        self, specification: Plan, perform: Callable[[], T], succeeded: Callable[[T], bool], cancel: threading.Event,
        *, verify: Callable[[T], ActionVerified | ActionUncertain] | None = None,
    ) -> T:
        with self.resources.hold(specification.resources, cancel):
            if isinstance(specification, Observation):
                if cancel.is_set():
                    raise ActionCancelledError("Stopped before inspection.")
                return perform()
            if self.unresolved():
                raise UncertainEffectError("An earlier action has an uncertain outcome. Inspect and reconcile it before making another change.")
            action = self.journal.begin_action(self.run, self.identity(), specification, at=self.clock())
            if action is None:
                raise LostRunError("This execution no longer owns the task.")
            task = self.journal.get(self.run.task_id)
            if cancel.is_set() or task is None or not owns(task, self.run):
                self.journal.finish_action(action.id, ActionNotApplied("The task stopped before the effect began."), at=self.clock())
                raise ActionCancelledError("Stopped before the effect began.")
            try:
                result = perform()
            except BaseException:
                # Losing the result record cannot turn an uncertain operation
                # into a retry. Its durable pending record remains sufficient.
                with contextlib.suppress(OSError, sqlite3.Error):
                    self.journal.finish_action(
                        action.id, ActionUncertain("The effect did not return a conclusive result."), at=self.clock(),
                    )
                raise
            outcome: ActionOutcome
            if verify is not None:
                outcome = verify(result)
            elif not succeeded(result):
                outcome = ActionUncertain("The effect did not return a successful result; it may have partially completed.")
            elif isinstance(specification.verification, FileDigest):
                outcome = self.verifier.verify(specification.verification)
            else:
                outcome = ActionReturned(f"{specification.tool} returned successfully; verify the requested outcome from fresh state.")
            if self.journal.finish_action(action.id, outcome, at=self.clock()) is None:
                raise UncertainEffectError("The effect returned but its outcome could not be committed. Inspect it before another change.")
            # Preserve failed tool output so the model can inspect and reconcile
            # partial effects. Another mutation remains blocked by unresolved().
            return result
