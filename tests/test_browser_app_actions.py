"""Saved-card receipts and exact app evidence through real durable execution."""

import threading
from pathlib import Path
from unittest import TestCase

from assistant.browser_app_receipts import opened_card
from assistant.browser_surface_operations import app_target_id
from assistant.llm import Json
from automation.actions import Resources, UncertainEffectError, plan
from automation.execution import Reported, WorkExecution
from automation.task import ActionUncertain, ActionVerified, Completed

from .test_agent import FakeMachine, ToolRig, dumps
from .test_work_actions import claimed

URL = "https://example.test/editor"
BEFORE = app_target_id("app-card", "instance", "before")
AFTER = app_target_id("app-card", "instance", "after")
OTHER = app_target_id("other-card", "instance", "after")


class SavedCardMachine(FakeMachine):
    __slots__ = ("receipt",)

    def __init__(self, receipt: str) -> None:
        super().__init__()
        self.receipt = receipt

    def browse(self, operation: str, arguments: dict[str, Json], cancel: threading.Event) -> tuple[str, bytes | None]:
        super().browse(operation, arguments, cancel)
        return self.receipt, None


def test_open_is_verified_only_by_the_matching_persisted_card_receipt(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    receipt: dict[str, Json] = {"operation": "open", "item_id": "app-card", "requested_url": URL, "saved": True, "state": "loading"}
    machine = SavedCardMachine(dumps(receipt))
    work = WorkExecution(claim, store, Resources(), clock=clock)
    rig = ToolRig(machine=machine, work=work)
    assert rig.call("browser_app_open", url=URL).ok
    action = store.actions(claim.task.id)[0]
    assert isinstance(action.outcome, ActionVerified)
    assert isinstance(work.outcome(Reported("The application card was saved."), threading.Event()), Completed)
    assert len(machine.log) == 1
    for changed in (
        {**receipt, "saved": False}, {**receipt, "saved": 1}, {**receipt, "operation": "navigate"},
        {**receipt, "requested_url": URL + "/other"}, {**receipt, "item_id": ""}, {**receipt, "item_id": True},
        {key: value for key, value in receipt.items() if key != "item_id"},
    ):
        assert isinstance(opened_card(URL, dumps(changed)), ActionUncertain)
    for malformed in ("Created!", "[]", "null", "{}"):
        assert isinstance(opened_card(URL, malformed), ActionUncertain)


def test_a_missing_open_receipt_cannot_be_retried_or_proved_by_an_unrelated_app_list(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    machine = SavedCardMachine('{"operation":"open","saved":false}')
    work = WorkExecution(claim, store, Resources(), clock=clock)
    rig = ToolRig(machine=machine, work=work)
    rig.call("browser_app_open", url=URL)
    action = store.actions(claim.task.id)[0]
    assert isinstance(action.outcome, ActionUncertain)
    listed = work.observed("browser_app_list", {}, successful=True,
                           result=dumps({"apps": [{"item_id": "app-card", "target_id": BEFORE, "url": URL}]}))
    with TestCase().assertRaisesRegex(ValueError, "target"):
        work.reconcile(action.id, listed, True)
    repeated = rig.call("browser_app_open", url=URL)
    assert not repeated.ok and len(machine.log) == 1
    assert len(store.actions(claim.task.id)) == 1


def test_app_action_needs_fresh_same_card_content_even_after_document_navigation(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    args = {"target_id": BEFORE, "id": "save", "action": "click"}
    stale = work.observed("browser_app_snapshot", {"target_id": BEFORE}, successful=True, result="Unsaved changes")
    applied: list[str] = []

    def interrupted() -> bool:
        applied.append("Saved")
        raise OSError("Navigation completed but the action result was lost.")

    with TestCase().assertRaises(OSError):
        work.actions.execute(plan("browser_app_action", args), interrupted, bool, threading.Event())
    action = store.actions(claim.task.id)[0]
    with TestCase().assertRaises(UncertainEffectError):
        work.actions.execute(plan("browser_app_action", args), interrupted, bool, threading.Event())
    with TestCase().assertRaisesRegex(ValueError, "after this action"):
        work.reconcile(action.id, stale, True)
    for tool, arguments, result in (
        ("browser_snapshot", {"target_id": BEFORE}, "Unrelated Edge page"),
        ("browser_app_snapshot", {"target_id": OTHER}, "Other card"),
        ("browser_app_list", {}, dumps({"apps": [{"target_id": AFTER, "title": "Saved"}]})),
    ):
        observed = work.observed(tool, arguments, successful=True, result=result)
        with TestCase().assertRaisesRegex(ValueError, "target"):
            work.reconcile(action.id, observed, True)
    fresh = work.observed("browser_app_snapshot", {"target_id": AFTER}, successful=True, result="Saved successfully")
    work.reconcile(action.id, fresh, True)
    assert applied == ["Saved"] and isinstance(store.actions(claim.task.id)[0].outcome, ActionVerified)
    assert isinstance(work.outcome(Reported("Saved and checked the current document."), threading.Event()), Completed)


def test_navigation_can_be_checked_on_its_current_card_in_a_paged_list(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources(), clock=clock)
    work.actions.execute(plan("browser_app_navigate", {"target_id": BEFORE, "url": URL}), lambda: True, bool, threading.Event())
    action = store.actions(claim.task.id)[0]
    for result in ("{}", "{invalid", '{"apps":{}}', dumps({"apps": [{"target_id": OTHER, "url": URL}]})):
        observed = work.observed("browser_app_list", {}, successful=True, result=result)
        with TestCase().assertRaisesRegex(ValueError, "target"):
            work.reconcile(action.id, observed, True)
    observed = work.observed("browser_app_list", {"offset": 10}, successful=True,
                             result=dumps({"apps": [{"target_id": AFTER, "url": URL}], "next_offset": 20}))
    work.reconcile(action.id, observed, True)
    assert isinstance(store.actions(claim.task.id)[0].outcome, ActionVerified)
