"""Application tools through the public agent boundary and durable journal."""

import threading
from pathlib import Path

from assistant.agent_session import ToolActivity
from assistant.browser_surface_operations import app_target_id
from assistant.llm import Json
from automation.actions import Observation, Resources, plan
from automation.execution import WorkExecution
from automation.task import ActionReturned

from .test_agent import FakeMachine, ToolRig
from .test_work_actions import claimed

TARGET = app_target_id("canvas-card", "page-instance", "first-document")


class AppContentMachine(FakeMachine):
    __slots__ = ("page_text",)

    def __init__(self, page_text: str) -> None:
        super().__init__()
        self.page_text = page_text

    def browse(self, operation: str, arguments: dict[str, Json], cancel: threading.Event) -> tuple[str, bytes | None]:
        super().browse(operation, arguments, cancel)
        return self.page_text, None


def test_live_app_content_is_returned_to_the_agent_but_omitted_from_generic_logs_and_previews() -> None:
    private_value = "PRIVATE-FIELD-EMBER-731"
    page_text = "PRIVATE-PAGE-CONTENT-COPPER-982"
    rig = ToolRig(machine=AppContentMachine(page_text))
    result = rig.call("browser_app_action", value=private_value, action="fill", id="field", target_id=TARGET)
    assert result.ok and result.text == page_text
    assert len(rig.machine.log) == 1 and private_value in rig.machine.log[0]
    assert len(rig.window.live) == 2 and len(rig.journal) == 1
    for event in rig.window.live:
        assert isinstance(event, ToolActivity)
        assert private_value not in event.detail + event.text and page_text not in event.detail + event.text
    for output in (repr(rig.journal), repr(rig.window.log)):
        assert private_value not in output and page_text not in output


def test_application_tools_reach_the_canvas_browser_and_keep_reads_out_of_the_journal(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    rig = ToolRig(work=WorkExecution(claim, store, Resources(), clock=clock))
    arguments: tuple[tuple[str, dict[str, Json]], ...] = (
        ("app_list", {}),
        ("app_snapshot", {"target_id": TARGET, "text_offset": 40}),
        ("app_action", {"target_id": TARGET, "id": "save", "action": "click"}),
    )
    for operation, fields in arguments:
        result = rig.call("browser_" + operation, **fields)
        assert result.ok, result.text
        assert rig.machine.log[-1].startswith("browser " + operation + " ")
    assert isinstance(plan("browser_app_list", {}), Observation)
    assert isinstance(plan("browser_app_snapshot", {"target_id": TARGET}), Observation)
    actions = store.actions(claim.task.id)
    assert len(actions) == 1 and actions[0].specification.tool == "browser_app_action"
    assert isinstance(actions[0].outcome, ActionReturned)


def test_application_effects_respect_the_configured_approval_mode() -> None:
    for operation, fields in (
        ("app_open", {"url": "https://example.test/editor"}),
        ("app_navigate", {"target_id": TARGET, "url": "https://example.test/editor"}),
        ("app_action", {"target_id": TARGET, "id": "subject", "action": "fill", "value": "Draft only"}),
    ):
        rig = ToolRig(approvals=True)
        declined = rig.call("browser_" + operation, **fields)
        assert not declined.ok and "declined" in declined.text
        assert len(rig.machine.log) == 1 and rig.machine.log[0].startswith("confirm ")
        rig.machine.confirms.append(True)
        accepted = rig.call("browser_" + operation, **fields)
        assert accepted.ok and rig.machine.log[-1].startswith("browser " + operation + " ")
    rig = ToolRig(approvals=True)
    assert rig.call("browser_app_list").ok
    assert rig.call("browser_app_snapshot", target_id=TARGET).ok
    assert all(entry.startswith("browser ") for entry in rig.machine.log)


def test_invalid_application_arguments_never_prompt_or_start_a_durable_effect(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    rig = ToolRig(approvals=True, work=WorkExecution(claim, store, Resources(), clock=clock))
    bad: tuple[tuple[str, dict[str, Json]], ...] = (
        ("app_open", {"url": "file:///C:/private.txt"}),
        ("app_open", {"url": "https://example.test", "profile_path": "C:/Users/Jim"}),
        ("app_navigate", {"target_id": TARGET, "url": "javascript:alert(1)"}),
        ("app_action", {"target_id": TARGET, "id": "field", "action": "fill"}),
        ("app_action", {"target_id": TARGET, "id": "field", "action": "click", "value": "unexpected"}),
        ("app_snapshot", {"target_id": TARGET, "limit": True}),
    )
    for operation, fields in bad:
        result = rig.call("browser_" + operation, **fields)
        assert not result.ok and result.text.startswith("Error:"), result.text
    assert not rig.machine.log and not store.actions(claim.task.id)


def test_a_stopped_application_request_cannot_reach_the_browser() -> None:
    rig = ToolRig()
    rig.agent.stop.set()
    result = rig.call("browser_app_open", url="https://example.test")
    assert not result.ok and not rig.machine.log
