"""Durable effects through real SQLite, files, and cancellable ownership."""

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from unittest import TestCase

from automation.actions import ActionCancelledError, Actions, LostRunError, Resources, UncertainEffectError, plan
from automation.execution import ApprovalReview, Reported, WorkExecution
from automation.store import TaskStore
from automation.task import (
    Action,
    ActionNotApplied,
    ActionPending,
    ActionSpec,
    ActionVerified,
    Approval,
    ApprovalDecision,
    Blocked,
    Claim,
    Completed,
    Control,
    FileDigest,
    RequestSource,
    RunRef,
    Waiting,
)


@dataclass(slots=True)
class Clock:
    value: float = 2

    def __call__(self) -> float:
        self.value += 1
        return self.value


def claimed(tmp_path: Path) -> tuple[TaskStore, Claim, Clock]:
    store = TaskStore(tmp_path / "work.sqlite3")
    store.submit("task", "Prepare notes", "Save the project notes.", RequestSource("chat"), at=1)
    claim = store.claim("worker", "run", at=2)
    assert claim is not None
    return store, claim, Clock()


def test_storage_commit_precedes_effect_and_failure_refuses_the_effect(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    destination = tmp_path / "notes.txt"
    args = {"path": str(destination), "content": "notes"}
    actions = Actions(store, claim.run, Resources(), clock=clock)
    executed: list[str] = []

    def write() -> int:
        pending = TaskStore(tmp_path / "work.sqlite3").actions("task")
        assert len(pending) == 1 and pending[0].outcome is ActionPending.RUNNING
        executed.append("write")
        return destination.write_text("notes", encoding="utf-8")

    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("CREATE TRIGGER refuse_attempt BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT,'fixture commit failure'); END")
    with TestCase().assertRaises(sqlite3.DatabaseError):
        actions.execute(plan("write_file", args), write, lambda count: count > 0, threading.Event())
    assert not destination.exists() and not executed and not store.actions("task")
    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("DROP TRIGGER refuse_attempt")
    expected = FileDigest(str(destination), hashlib.sha256(b"notes").hexdigest())
    actions.execute(plan("write_file", args, verification=expected), write, lambda count: count > 0, threading.Event())
    assert destination.read_text(encoding="utf-8") == "notes" and executed == ["write"]
    assert isinstance(TaskStore(tmp_path / "work.sqlite3").actions("task")[0].outcome, ActionVerified)


def test_lost_result_commit_recovers_by_readback_without_replaying(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    destination = tmp_path / "notes.txt"
    expected = FileDigest(str(destination), hashlib.sha256(b"written once").hexdigest())
    spec = plan("write_file", {"path": str(destination), "content": "written once"}, verification=expected)
    actions = Actions(store, claim.run, Resources(), clock=clock)
    executions: list[str] = []

    def write() -> int:
        executions.append("write")
        return destination.write_text("written once", encoding="utf-8")

    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("CREATE TRIGGER refuse_result BEFORE UPDATE ON actions BEGIN SELECT RAISE(ABORT,'fixture lost acknowledgement'); END")
    with TestCase().assertRaises(sqlite3.DatabaseError):
        actions.execute(spec, write, lambda count: count > 0, threading.Event())
    assert destination.read_text(encoding="utf-8") == "written once"
    assert store.actions("task")[0].outcome is ActionPending.RUNNING
    with TestCase().assertRaises(UncertainEffectError):
        actions.execute(spec, write, lambda count: count > 0, threading.Event())
    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("DROP TRIGGER refuse_result")
    reopened = TaskStore(tmp_path / "work.sqlite3")
    assert reopened.recover("restarted-worker", at=clock())
    resumed = reopened.claim("restarted-worker", "second-run", at=clock())
    assert resumed is not None
    recovered = Actions(reopened, resumed.run, Resources(), clock=clock)
    assert recovered.reconcile(threading.Event()) == ()
    assert executions == ["write"] and isinstance(reopened.actions("task")[0].outcome, ActionVerified)


def test_mismatched_recovery_file_stays_uncertain_and_cannot_be_overwritten(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    destination = tmp_path / "notes.txt"
    spec = plan("write_file", {"path": str(destination), "content": "desired"},
                verification=FileDigest(str(destination), hashlib.sha256(b"desired").hexdigest()))
    actions = Actions(store, claim.run, Resources(), clock=clock)

    def interrupted_write() -> bool:
        destination.write_text("partial", encoding="utf-8")
        raise OSError("The application disconnected after changing the file.")

    with TestCase().assertRaises(OSError):
        actions.execute(spec, interrupted_write, bool, threading.Event())
    other = tmp_path / "another-project.txt"
    other.write_text("untouched", encoding="utf-8")
    different = plan("write_file", {"path": str(other), "content": "new notes"},
                     verification=FileDigest(str(other), hashlib.sha256(b"new notes").hexdigest()))
    with TestCase().assertRaises(UncertainEffectError):
        actions.execute(different, lambda: other.write_text("new notes", encoding="utf-8"), bool, threading.Event())
    assert other.read_text(encoding="utf-8") == "untouched" and len(store.actions("task")) == 1
    assert len(actions.reconcile(threading.Event())) == 1
    with TestCase().assertRaises(UncertainEffectError):
        actions.execute(spec, lambda: destination.write_text("desired", encoding="utf-8"), bool, threading.Event())
    assert destination.read_text(encoding="utf-8") == "partial"
    destination.write_text("desired", encoding="utf-8")  # A later external repair can establish the intended state.
    assert actions.reconcile(threading.Event()) == ()
    actions.execute(different, lambda: other.write_text("new notes", encoding="utf-8"), bool, threading.Event())
    assert other.read_text(encoding="utf-8") == "new notes"


def test_stop_cancels_resource_wait_and_releases_the_next_owner(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    resources, stopped, entered = Resources(), threading.Event(), threading.Event()
    destination = tmp_path / "must-not-exist.txt"
    actions = Actions(store, claim.run, resources, clock=clock)

    def waiting() -> None:
        entered.set()
        actions.execute(plan("write_file", {"path": str(destination), "content": "no"}),
                        lambda: destination.write_text("no", encoding="utf-8"), bool, stopped)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with resources.hold(("foreground",), threading.Event()):
            future = pool.submit(waiting)
            assert entered.wait(5)
            stopped.set()
            with TestCase().assertRaises(ActionCancelledError):
                future.result(5)
        with resources.hold(("clipboard",), threading.Event()):
            assert not destination.exists() and not store.actions("task")


class StoppingStore(TaskStore):
    """A real store whose public commit acknowledgement races cancellation."""

    __slots__ = ("stop",)

    def __init__(self, path: Path, stop: threading.Event) -> None:
        super().__init__(path)
        self.stop = stop

    def begin_action(self, run: RunRef, action_id: str, specification: ActionSpec, *, at: float) -> Action | None:
        result = super().begin_action(run, action_id, specification, at=at)
        self.stop.set()
        return result


def test_stale_run_and_stop_after_commit_cannot_enter_the_effect(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    destination = tmp_path / "must-not-exist.txt"
    spec = plan("write_file", {"path": str(destination), "content": "no"})
    store.control("task", Control.PAUSE, at=clock())
    store.control("task", Control.RESUME, at=clock())
    later = store.claim("worker", "new-run", at=clock())
    assert later is not None
    with TestCase().assertRaises(LostRunError):
        Actions(store, claim.run, Resources(), clock=clock).execute(
            spec, lambda: destination.write_text("no", encoding="utf-8"), bool, threading.Event(),
        )
    stop = threading.Event()
    cancelling = StoppingStore(tmp_path / "work.sqlite3", stop)
    with TestCase().assertRaises(ActionCancelledError):
        Actions(cancelling, later.run, Resources(), clock=clock).execute(
            spec, lambda: destination.write_text("no", encoding="utf-8"), bool, stop,
        )
    assert not destination.exists() and isinstance(store.actions("task")[0].outcome, ActionNotApplied)


def test_reconciliation_requires_post_attempt_same_target_and_is_durable(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    pages = {"target-tab": "before", "other-tab": "unrelated"}
    before = work.observed("browser_snapshot", {"target_id": "target-tab"}, successful=True, result=pages["target-tab"])
    spec = plan("browser_action", {"target_id": "target-tab", "id": "save", "action": "click"})

    def interrupted_save() -> bool:
        pages["target-tab"] = "Saved successfully"
        raise OSError("Lost the browser acknowledgement")

    with TestCase().assertRaises(OSError):
        work.actions.execute(spec, interrupted_save, bool, threading.Event())
    action = store.actions("task")[0]
    with TestCase().assertRaisesRegex(ValueError, "after this action"):
        work.reconcile(action.id, before, True)
    wrong = work.observed("browser_snapshot", {"target_id": "other-tab"}, successful=True, result=pages["other-tab"])
    with TestCase().assertRaisesRegex(ValueError, "target"):
        work.reconcile(action.id, wrong, True)
    fresh = work.observed("browser_snapshot", {"target_id": "target-tab"}, successful=True, result=pages["target-tab"])
    assert "Saved" in pages["target-tab"]
    work.reconcile(action.id, fresh, True)
    assert isinstance(work.outcome(Reported("Saved."), threading.Event()), Completed)
    assert isinstance(TaskStore(tmp_path / "work.sqlite3").actions("task")[0].outcome, ActionVerified)


def test_script_result_requires_explicit_inspection_and_reconciliation(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    destination = tmp_path / "script-output.txt"
    work.actions.execute(plan("run_powershell", {"command": "script writes requested output"}),
                         lambda: destination.write_text("complete", encoding="utf-8"), bool, threading.Event())
    assert isinstance(work.outcome(Reported("Done"), threading.Event()), Waiting)
    content = destination.read_text(encoding="utf-8")
    observed = work.observed("read_file", {"path": str(destination)}, successful=True, result=content)
    assert content == "complete"
    work.reconcile(store.actions("task")[0].id, observed, True)
    assert isinstance(work.outcome(Reported("The output was inspected."), threading.Event()), Completed)


def test_native_tab_verification_uses_the_listed_target_even_when_the_strip_is_paged(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    work.actions.execute(plan("browser_tab", {"tab_id": 17, "action": "pin"}), lambda: True, bool, threading.Event())
    action = store.actions("task")[0]
    wrong = work.observed(
        "browser_tabs", {"view": "strip"}, successful=True,
        result='{"view":"strip","tabs":[{"tab_id":18,"pinned":true}],"next_offset":1}',
    )
    with TestCase().assertRaisesRegex(ValueError, "target"):
        work.reconcile(action.id, wrong, True)
    correct = work.observed(
        "browser_tabs", {"view": "strip", "offset": 1}, successful=True,
        result='{"view":"strip","tabs":[{"tab_id":17,"pinned":true}],"next_offset":2}',
    )
    work.reconcile(action.id, correct, True)
    assert isinstance(work.outcome(Reported("Pinned the requested tab."), threading.Event()), Completed)


def test_open_chat_requires_matching_one_to_one_membership_not_account_status(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    work.actions.execute(plan("teams_open_chat", {"user_id": "requested-recipient"}), lambda: True, bool, threading.Event())
    action = store.actions("task")[0]
    for evidence in (
        work.observed("teams_status", {}, successful=True, result='{"configured":true,"saved_sign_in":true}'),
        work.observed("teams_list_chats", {}, successful=True,
                      result='{"chats":[{"chatType":"oneOnOne","members":[{"userId":"someone-else"}]}]}'),
        work.observed("teams_list_chats", {}, successful=True,
                      result='{"chats":[{"chatType":"group","members":[{"userId":"requested-recipient"}]}]}'),
    ):
        with TestCase().assertRaisesRegex(ValueError, "target"):
            work.reconcile(action.id, evidence, True)
    correct = work.observed("teams_list_chats", {}, successful=True,
                            result='{"chats":[{"chatType":"oneOnOne","members":[{"userId":"requested-recipient"}]}]}')
    work.reconcile(action.id, correct, True)
    assert isinstance(work.outcome(Reported("The recipient's chat exists."), threading.Event()), Completed)


def test_old_unverified_action_cannot_fall_off_the_completion_page(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    work.actions.execute(plan("set_clipboard", {"text": "needs verification"}), lambda: True, bool, threading.Event())
    for index in range(100):
        spec = plan("write_file", {"path": f"note-{index}.txt", "content": "verified"})
        assert isinstance(spec, ActionSpec)
        assert store.begin_action(claim.run, f"later-{index}", spec, at=clock()) is not None
        assert store.finish_action(f"later-{index}", ActionVerified("Read back the file."), at=clock()) is not None
    assert len(store.actions("task", limit=100)) == 100
    assert isinstance(work.outcome(Reported("Everything is complete."), threading.Event()), Waiting)


def test_exact_approval_is_once_only_and_private_review_never_enters_storage(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    reviews: list[ApprovalReview] = []

    def display(review: ApprovalReview) -> bool:
        reviews.append(review)
        return True

    original = WorkExecution(claim, store, Resources(), review=display, clock=clock)
    arguments = {"target_id": "secret-tab", "id": "control", "action": "fill", "value": "private-message-content"}
    original.bind("browser_action", arguments)
    assert not original.approve("Fill this control?", "private-full-browser-review")
    waiting = original.outcome(Reported("Waiting."), threading.Event())
    assert isinstance(waiting, Waiting) and isinstance(waiting.reason, Approval)
    assert "private-message-content" in reviews[0].detail
    assert store.finish(claim.run, waiting, at=clock()) is not None
    assert store.answer("task", waiting.reason.id, ApprovalDecision.APPROVED, at=clock()) is not None
    resumed = store.claim("worker", "approved-run", at=clock())
    assert resumed is not None
    work = WorkExecution(resumed, store, Resources(), review=display, clock=clock)
    work.bind("browser_action", arguments)
    assert work.approve("Fill this control?", "private-full-browser-review")
    work.actions.execute(plan("browser_action", arguments), lambda: True, bool, threading.Event())
    assert not work.approve("Fill this control?", "private-full-browser-review")
    assert len(reviews) == 2 and reviews[0].request.id != reviews[1].request.id
    work.checkpoint([{"type": "reasoning", "encrypted_content": "private-reasoning"},
                     {"type": "function_call", "name": "browser_action", "arguments": json.dumps(arguments)}])
    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        persisted = "\n".join(database.iterdump())
    assert all(secret not in persisted for secret in ("secret-tab", "private-message-content", "private-full-browser-review", "private-reasoning"))


def test_approval_needs_live_preview_and_cannot_authorize_changed_arguments_or_goal(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    work.bind("teams_send_message", {"chat_id": "recipient-one", "text": "first draft"})
    assert not work.approve("Send message?", "Review the draft")
    assert isinstance(work.disposition, Waiting) and isinstance(work.disposition.reason, Blocked)
    reviews: list[ApprovalReview] = []

    def display(review: ApprovalReview) -> bool:
        reviews.append(review)
        return True

    original = WorkExecution(claim, store, Resources(), review=display, clock=clock)
    original.bind("teams_send_message", {"chat_id": "recipient-one", "text": "first draft"})
    assert not original.approve("Send message?", "Review the draft")
    assert original.disposition is not None
    assert store.finish(claim.run, original.disposition, at=clock()) is not None
    assert store.answer("task", reviews[0].request.id, ApprovalDecision.APPROVED, at=clock()) is not None
    resumed = store.claim("worker", "changed-arguments", at=clock())
    assert resumed is not None
    changed = WorkExecution(resumed, store, Resources(), review=display, clock=clock)
    changed.bind("teams_send_message", {"chat_id": "recipient-two", "text": "first draft"})
    assert not changed.approve("Send message?", "Review the draft")
    assert store.revise("task", "Only prepare a draft; do not send it.", at=clock()) is not None
    redirected = store.claim("worker", "changed-goal", at=clock())
    assert redirected is not None
    new_goal = WorkExecution(redirected, store, Resources(), review=display, clock=clock)
    new_goal.bind("teams_send_message", {"chat_id": "recipient-one", "text": "first draft"})
    assert not new_goal.approve("Send message?", "Review the draft")
    assert not store.actions("task")


def test_message_reconciliation_requires_same_chat_new_matching_complete_text(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    work.actions.execute(plan("teams_send_message", {"chat_id": "release-chat", "text": "Ship Friday"}),
                         lambda: True, bool, threading.Event())
    action = store.actions("task")[0]

    def inspect(chat: str, content: str, created: str, *, truncated: bool = False) -> str:
        message: dict[str, object] = {
            "message_id": "sent-message", "content": content, "created_at": created,
            "content_type": "text", "content_truncated": truncated,
        }
        payload: dict[str, object] = {"chat_id": chat, "messages": [message]}
        result = json.dumps(payload)
        return work.observed("teams_read_messages", {"chat_id": chat}, successful=True, result=result)

    for evidence in (
        inspect("other-chat", "Ship Friday", "1970-01-01T00:00:10+00:00"),
        inspect("release-chat", "Ship Thursday", "1970-01-01T00:00:10+00:00"),
        inspect("release-chat", "Ship Friday", "1970-01-01T00:00:01+00:00"),
        inspect("release-chat", "Ship Friday", "1970-01-01T00:00:10+00:00", truncated=True),
    ):
        with TestCase().assertRaises(ValueError):
            work.reconcile(action.id, evidence, True)
    correct = inspect("release-chat", "Ship Friday", "1970-01-01T00:00:10+00:00")
    with TestCase().assertRaisesRegex(ValueError, "Absence"):
        work.reconcile(action.id, correct, False)
    work.reconcile(action.id, correct, True)
    assert isinstance(work.outcome(Reported("Message sent and read back."), threading.Event()), Completed)
