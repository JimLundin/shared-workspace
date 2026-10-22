"""Tab/group contracts, ownership choices and failures before browser effects."""

import json
import threading
from pathlib import Path
from unittest import TestCase

from assistant.browser import MAX_OUTPUT
from assistant.browser_tabs import Call, Control, TabStrip
from assistant.browser_values import (
    BrowserError,
    DefaultGroup,
    Group,
    InGroup,
    NewGroup,
    Opening,
    Tab,
    TabState,
    Ungrouped,
    group_of,
    group_request,
    opening,
    tab_of,
)
from assistant.llm import Json

from .test_browser import Channel, connected_browser, decoded


def test_default_open_reuses_the_users_expanded_toolkit_group_without_changing_it() -> None:
    first = Group(10, 1, "Toolkit", "blue", True, False)
    expanded = Group(20, 2, "Toolkit", "purple", False, False)
    state = TabState((), (first, expanded), (1, 2), 2)
    assert opening(state, DefaultGroup.TOOLKIT, None) == Opening(2, InGroup(20))
    assert opening(state, DefaultGroup.TOOLKIT, 1) == Opening(1, InGroup(10))
    assert expanded == Group(20, 2, "Toolkit", "purple", False, False)
    assert opening(state, Ungrouped.TABS, None) == Opening(2, Ungrouped.TABS)
    with TestCase().assertRaisesRegex(BrowserError, "match"):
        opening(state, InGroup(10), 2)
    with TestCase().assertRaisesRegex(BrowserError, "group is closed"):
        opening(state, InGroup(30), None)


def test_default_open_does_not_add_work_to_other_or_shared_groups() -> None:
    state = TabState((), (Group(10, 1, "Personal", "blue", False, False), Group(20, 1, "Toolkit", "blue", False, True)), (1,), 1)
    assert opening(state, DefaultGroup.TOOLKIT, None) == Opening(1, NewGroup("Toolkit", "blue", True))
    with TestCase().assertRaisesRegex(BrowserError, "window is closed"):
        opening(state, DefaultGroup.TOOLKIT, 2)


def test_invalid_tab_edits_do_not_create_a_controller_or_change_any_tabs(tmp_path: Path) -> None:
    channel = Channel()
    browser = connected_browser(tmp_path / "unused", channel)
    cases: tuple[tuple[str, dict[str, Json]], ...] = (
        ("group", {}), ("group", {"tab_ids": [True]}), ("group", {"tab_ids": ["1"]}),
        ("group", {"tab_ids": [1], "collapsed": "yes"}), ("group", {"tab_ids": [1], "color": "invisible"}),
        ("group", {"group_id": True}), ("group", {"tab_ids": [1], "window_id": -1}),
        ("group", {"tab_ids": [1], "title": "x" * 301}),
        ("ungroup", {"tab_ids": []}), ("tab", {"tab_id": 1, "action": "move"}),
        ("tab", {"tab_id": 1, "action": "pin", "index": 0}),
        # The WebUI mute API dereferences a missing extension and crashes Edge.
        ("tab", {"tab_id": 1, "action": "mute"}), ("tab", {"tab_id": 1, "action": "unmute"}),
        ("close", {"tab_ids": []}), ("close", {"group_id": -1}),
        ("close", {"target_id": "page", "tab_ids": [1]}),
        ("open", {"url": "about:blank", "group_id": "1"}),
        ("open", {"url": "about:blank", "target_id": "page", "group_id": 1}),
    )
    try:
        for operation, arguments in cases:
            with TestCase().assertRaises(BrowserError):
                browser.request(operation, arguments, threading.Event())
            assert not channel.sent, (operation, arguments)
    finally:
        browser.close()


def test_native_observations_require_the_declared_field_types() -> None:
    tab = Tab(1, 2, 0, Ungrouped.TABS, "Page", "https://example.com", False, False, False)
    source = decoded(json.dumps(tab.data()))
    assert tab_of(source) == tab
    for name, value in (("tab_id", True), ("active", "false"), ("group_id", -2), ("url", None)):
        with TestCase().assertRaises(BrowserError):
            tab_of({**source, name: value})
    source.pop("active")
    with TestCase().assertRaisesRegex(BrowserError, "incomplete"):
        tab_of(source)
    group = decoded(json.dumps(Group(1, 2, "Toolkit", "blue", True, False).data()))
    with TestCase().assertRaises(BrowserError):
        group_of({**group, "collapsed": 1})


class FixedStrip(TabStrip):
    __slots__ = ("state",)

    def __init__(self, state: TabState) -> None:
        super().__init__()
        self.state = state

    def snapshot(self, call: Call, cancel: threading.Event) -> TabState:
        return self.state


def test_native_tab_pagination_keeps_ids_and_group_details_with_escaped_long_titles(tmp_path: Path) -> None:
    tabs = tuple(Tab(index, 1, index, InGroup(index // 2), "\x00" * 300, "https://example.com/" + "\x00" * 970,
                     False, False, False) for index in range(65))
    groups = tuple(Group(index, 1, "\x00" * 300, "blue", True, False) for index in range(33))
    browser = connected_browser(tmp_path / "unused", Channel(), strip=FixedStrip(TabState(tabs, groups, (1,), 1)))
    browser.native_targets["last-page"] = 64
    offset = 0
    seen: list[int] = []
    try:
        while True:
            output = browser.request("tabs", {"view": "strip", "offset": offset, "limit": 40}, threading.Event()).text
            assert len(output) <= MAX_OUTPUT
            data = decoded(output)
            rows, group_rows = data["tabs"], data["groups"]
            assert isinstance(rows, list) and isinstance(group_rows, list)
            page = tuple(tab_of(row) for row in rows)
            seen.extend(tab.id for tab in page)
            assert {group_of(group).id for group in group_rows} == {tab.group_id for tab in page}
            if "next_offset" not in data:
                assert isinstance(rows[-1], dict) and rows[-1]["target_id"] == "last-page"
                break
            next_offset = data["next_offset"]
            assert isinstance(next_offset, int) and next_offset > offset
            offset = next_offset
        assert seen == list(range(65))
    finally:
        browser.close()


def test_unavailable_native_apis_close_only_the_hidden_controller() -> None:
    commands: list[tuple[str, dict[str, Json]]] = []

    def call(method: str, params: dict[str, Json], cancel: threading.Event, *, session: str = "", timeout: float = 15) -> dict[str, Json]:
        commands.append((method, params))
        responses: dict[str, dict[str, Json]] = {
            "Target.createTarget": {"targetId": "hidden"}, "Target.attachToTarget": {"sessionId": "control"},
            "Runtime.evaluate": {"result": {"value": {"ready": False}}}, "Target.closeTarget": {"success": True},
        }
        return responses[method]

    strip = TabStrip(clock=iter((0.0, 6.0)).__next__)
    with TestCase().assertRaisesRegex(BrowserError, "native tab/group APIs"):
        strip.snapshot(call, threading.Event())
    assert commands[0] == ("Target.createTarget", {"url": "edge://favorites/", "background": True, "hidden": True})
    assert commands[-1] == ("Target.closeTarget", {"targetId": "hidden"}) and strip.control is None


def test_a_failed_group_change_is_not_replayed() -> None:
    commands: list[str] = []

    def call(method: str, params: dict[str, Json], cancel: threading.Event, *, session: str = "", timeout: float = 15) -> dict[str, Json]:
        commands.append(method)
        return {"exceptionDetails": {"text": "The group was changed, but its window was closed."}}

    strip = TabStrip()
    strip.control = Control("hidden", "session")
    with TestCase().assertRaisesRegex(BrowserError, "Inspect browser_tabs"):
        strip.group(call, group_request({"group_id": 7, "title": "Research", "window_id": 8}), threading.Event())
    assert commands == ["Runtime.evaluate"]
