"""Background integrations share the agent's defaults, Stop and journal rules."""

import json
import threading
from typing import cast

from assistant.agent import BROWSER_SPECS, MEETING_SPECS, TEAMS_SPECS, Agent
from assistant.agent_session import ToolActivity
from assistant.geometry import Shot
from assistant.llm import Json, Settings, ToolCall
from winapi.shapes import Rect

from .fake_endpoint import fake_endpoint
from .test_agent import FakeMachine, FakeWindow, Rig, calls, says


def test_meeting_voice_commands_use_the_recording_service_without_default_approvals() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint, calls(("meeting_start", {"source": "both", "title": "Private planning"})),
                  calls(("meeting_status", {})), calls(("meeting_stop", {})), says("The transcript is saved."))
        rig.run("Record this meeting, check its progress, and then stop it.")
        assert "meeting start both Private planning" in rig.machine.log
        assert any(line.startswith("meeting stop") for line in rig.machine.log)
        assert not any(line.startswith("confirm") for line in rig.machine.log)
        assert "Private planning" not in json.dumps(rig.journal)
        assert all("Private planning" not in item.text for item in rig.window.live if isinstance(item, ToolActivity))
        request = cast(dict[str, Json], json.loads(endpoint.received[0].body))
        specs = cast(list[dict[str, Json]], request["tools"])
        assert {spec.name for spec in MEETING_SPECS} <= {spec["name"] for spec in specs if isinstance(spec["name"], str)}


def test_stopped_agent_cannot_start_recording_and_explicit_approval_mode_is_preserved() -> None:
    machine, cancel = FakeMachine(), threading.Event()
    agent = Agent(Settings("", "", "", "", "", "", "", agent_approvals=True), machine, FakeWindow(), cancel, journal=lambda _: None)
    assert not agent.call(ToolCall("record", "meeting_start", '{}')).ok
    assert not any(line.startswith("meeting") for line in machine.log)
    cancel.set()
    assert not agent.call(ToolCall("stopped", "meeting_start", '{}')).ok
    assert not any(line.startswith("meeting") for line in machine.log)


def test_background_tools_are_exposed_to_the_model_and_run_without_default_prompts() -> None:
    with fake_endpoint() as endpoint:
        rig = Rig(endpoint,
                  calls(("browser_attach", {"browser": "edge"})),
                  calls(("browser_open", {"url": "https://example.com"})),
                  calls(("browser_action", {"target_id": "page", "id": "field", "action": "fill", "value": "fixture-private"})),
                  calls(("teams_status", {})), calls(("teams_send_message", {"chat_id": "chat", "text": "requested message"})), says("Done."))
        rig.run("Fill the fixture form and send the specified Teams message.")
        assert any(line.startswith("browser action") for line in rig.machine.log)
        assert any(line.startswith("teams send_message") for line in rig.machine.log)
        assert not any(line.startswith("confirm") for line in rig.machine.log)
        request = cast(dict[str, Json], json.loads(endpoint.received[0].body))
        specs = cast(list[dict[str, Json]], request["tools"])
        names = {spec["name"] for spec in specs if isinstance(spec["name"], str)}
        assert {spec.name for spec in (*BROWSER_SPECS, *TEAMS_SPECS)} <= names
        assert "fixture-private" not in json.dumps(rig.journal) and "requested message" not in json.dumps(rig.journal)
        assert not any("fixture-private" in line for line in rig.window.log)
        activities = [item for item in rig.window.live if isinstance(item, ToolActivity)]
        assert activities and all(item.detail == "" for item in activities)
        assert all("fixture-private" not in item.text and "requested message" not in item.text for item in activities)


def test_explicit_approval_mode_can_decline_browser_changes_and_teams_sends() -> None:
    machine = FakeMachine()
    agent = Agent(Settings("", "", "", "", "", "", "", agent_approvals=True), machine, FakeWindow(), threading.Event(), journal=lambda _: None)
    for name, arguments in (
        ("browser_attach", {"browser": "edge"}),
        ("browser_action", {"target_id": "page", "id": "button", "action": "click"}),
        ("browser_group", {"tab_ids": [7], "title": "Toolkit"}),
        ("browser_ungroup", {"tab_ids": [7]}),
        ("browser_tab", {"tab_id": 7, "action": "pin"}),
        ("browser_close", {"group_id": 23}),
        ("teams_send_message", {"chat_id": "chat", "text": "message"}),
    ):
        assert not agent.call(ToolCall("fixture", name, json.dumps(arguments))).ok
    assert sum(line.startswith("confirm") for line in machine.log) == 7
    assert not any(line.startswith(("browser ", "teams ")) for line in machine.log)


def test_browser_images_invalidate_desktop_coordinates() -> None:
    machine = FakeMachine()
    agent = Agent(Settings("", "", "", "", "", "", ""), machine, FakeWindow(), threading.Event(), journal=lambda _: None)
    agent.shot = Shot(Rect(0, 0, 1920, 1080), 960, 540)
    result = agent.call(ToolCall("browser", "browser_screenshot", '{"target_id":"page"}'))
    assert result.image is not None
    click = agent.call(ToolCall("desktop", "click", '{"x":10,"y":10}'))
    assert not click.ok and "screenshot first" in click.text
    assert not any(line.startswith("click ") for line in machine.log)


def test_agent_can_manage_tabs_and_groups_without_default_approval_prompts() -> None:
    machine = FakeMachine()
    agent = Agent(Settings("", "", "", "", "", "", ""), machine, FakeWindow(), threading.Event(), journal=lambda _: None)
    for name, arguments in (
        ("browser_tabs", {"view": "strip"}),
        ("browser_open", {"url": "about:blank", "group_id": 23}),
        ("browser_group", {"tab_ids": [7, 8], "title": "Toolkit", "collapsed": True}),
        ("browser_group", {"group_id": 23, "title": "Research", "index": -1}),
        ("browser_ungroup", {"tab_ids": [8]}),
        ("browser_tab", {"tab_id": 7, "action": "pin"}),
        ("browser_close", {"group_id": 23}),
    ):
        result = agent.call(ToolCall("tabs", name, json.dumps(arguments)))
        assert result.ok, (name, result.text)
    assert len(machine.log) == 7 and all(line.startswith("browser ") for line in machine.log)
    assert {"browser_group", "browser_ungroup", "browser_tab"} <= {spec.name for spec in BROWSER_SPECS}


def test_stopped_agent_does_not_start_background_tools_or_log_their_arguments() -> None:
    machine, window, cancel = FakeMachine(), FakeWindow(), threading.Event()
    journal: list[dict[str, Json]] = []
    agent = Agent(Settings("", "", "", "", "", "", ""), machine, window, cancel, journal=journal.append)
    cancel.set()
    for name in ("browser_action", "teams_send_message"):
        result = agent.call(ToolCall("stopped", name, '{"text":"private-secret"}'))
        assert not result.ok
    assert not machine.log
    assert "private-secret" not in json.dumps(journal) and "private-secret" not in str(window.log)
    assert all("private-secret" not in item.text for item in window.live)
