"""Typed app-page capabilities over the real authenticated work bridge."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import TestCase

from assistant.browser import BrowserResult
from assistant.browser_surface_control import AppBrowserControl, CompositeBrowser
from assistant.browser_surface_operations import (
    MAX_RESULT,
    MAX_VALUE,
    AppCancelled,
    AppFailed,
    AppOperation,
    AppResult,
    AppSucceeded,
    CheckApp,
    ClickApp,
    FillApp,
    ListApps,
    NavigateApp,
    OpenApp,
    SelectApp,
    SnapshotApp,
    app_operation,
    app_target_id,
    operation_data,
    operation_of,
    result_data,
    result_of,
    target_card_id,
)
from assistant.browser_values import BrowserError, InterruptedError
from assistant.llm import Json
from automation import ipc
from automation.bridge import BridgeClient, StopSignal
from automation.task import RunRef

from .test_automation_ipc import RUN, bridge, drain, read_until

TARGET = app_target_id("card", "instance", "document")


def test_all_app_operation_variants_preserve_exact_values_through_json(tmp_path: Path) -> None:
    operations: tuple[AppOperation, ...] = (
        ListApps(12, 40), OpenApp("https://example.invalid/office", "Office"), OpenApp("https://example.invalid/code"),
        NavigateApp(TARGET, "https://example.invalid/document"), SnapshotApp(TARGET, 35, 100, 6_000),
        ClickApp(TARGET, "snapshot-0"), FillApp(TARGET, "snapshot-1", "  A draft 🌍\n"),
        SelectApp(TARGET, "snapshot-2", ""), CheckApp(TARGET, "snapshot-3", False),
    )
    path = tmp_path / "operations.json"
    encoded = [operation_data(operation) for operation in operations]
    path.write_text(json.dumps(encoded, ensure_ascii=False), encoding="utf-8")
    values = cast(list[object], json.loads(path.read_text(encoding="utf-8")))
    assert tuple(operation_of(value) for value in values) == operations
    assert app_operation("app_list", {}) == ListApps()
    assert app_operation("app_open", {"url": "https://example.invalid/office"}) == OpenApp("https://example.invalid/office")
    assert app_operation("app_snapshot", {"target_id": TARGET}) == SnapshotApp(TARGET)
    assert app_operation("app_action", {"target_id": TARGET, "id": "snapshot-1", "action": "fill", "value": ""}) == FillApp(
        TARGET, "snapshot-1", "",
    )


def test_app_tool_contract_rejects_privileged_and_ambiguous_arguments() -> None:
    attempts: tuple[tuple[str, dict[str, Json]], ...] = (
        ("app_evaluate", {"expression": "globalThis"}), ("app_close", {"target_id": TARGET}),
        ("app_list", {"browser": "edge"}), ("app_open", {"url": "https://example.invalid", "profile": "shell"}),
        ("app_snapshot", {"target_id": "qt-page-1"}), ("app_snapshot", {"target_id": TARGET, "frame_id": "privileged-frame"}),
        ("app_snapshot", {"target_id": TARGET, "expression": "read secrets"}),
        ("app_action", {"target_id": TARGET, "id": "control", "action": "fill"}),
        ("app_action", {"target_id": TARGET, "id": "control", "action": "click", "value": "ignored"}),
        ("app_action", {"target_id": TARGET, "id": "control", "action": "check", "checked": 1}),
        ("app_action", {"target_id": TARGET, "id": "control", "action": "evaluate", "value": "arbitrary script"}),
    )
    for name, arguments in attempts:
        with TestCase().assertRaises(ValueError):
            app_operation(name, arguments)
    encoded = operation_data(SnapshotApp(TARGET))
    with TestCase().assertRaises(ValueError):
        operation_of({**encoded, "script": "unexpected"})


def test_app_urls_and_payload_limits_are_validated_before_transport() -> None:
    for url in ("file:///private", "qrc:/qtwebchannel/qwebchannel.js", "devtools://inspect", "chrome://settings", "javascript:alert(1)",
                "https://user:secret@example.invalid", "https://example.invalid/\nother", "https://example.invalid:99999",
                " https://example.invalid/", "https://example.invalid/\udfff"):
        with TestCase().assertRaises(ValueError):
            app_operation("app_open", {"url": url})
    invalid: tuple[tuple[str, dict[str, Json]], ...] = (
        ("app_list", {"offset": True}), ("app_list", {"limit": 41}), ("app_list", {"offset": -1}),
        ("app_snapshot", {"target_id": TARGET, "limit": 0}), ("app_snapshot", {"target_id": TARGET, "limit": 101}),
        ("app_snapshot", {"target_id": TARGET, "text_offset": 10_000_001}),
        ("app_action", {"target_id": TARGET, "id": "control", "action": "fill", "value": "x" * (MAX_VALUE + 1)}),
        ("app_action", {"target_id": TARGET, "id": "control", "action": "fill", "value": "\ud800"}),
    )
    for name, arguments in invalid:
        with TestCase().assertRaises(ValueError):
            app_operation(name, arguments)


def test_document_targets_retain_only_a_stable_card_key_for_recovery() -> None:
    first = app_target_id("card", "instance", "document")
    navigated = app_target_id("card", "instance", "next-document")
    reopened = app_target_id("card", "replacement-instance", "document")
    other = app_target_id("other-card", "instance", "document")
    assert len(first) == 97 and len({first, navigated, reopened, other}) == 4
    assert target_card_id(first) == target_card_id(navigated) == target_card_id(reopened)
    assert target_card_id(first) != target_card_id(other)
    assert app_target_id("card", "instance", "document") == first
    for malformed in (first[:32], first + ".extra", first.upper(), "qt-1", None, True):
        assert target_card_id(malformed) is None
    with TestCase().assertRaises(ValueError):
        app_operation("app_snapshot", {"target_id": first[:32]})


def test_app_result_contract_is_bounded_and_separate_from_transport_errors() -> None:
    results: tuple[AppResult, ...] = (AppSucceeded('{"apps":[]}'), AppFailed("The card was closed."), AppCancelled.REQUEST)
    for result in results:
        assert result_of(result_data(result)) == result
    for value in ({"kind": "succeeded", "text": "x" * (MAX_RESULT + 1)}, {"kind": "failed", "reason": ""},
                  {"kind": "cancelled", "script": "ignored"}, {"kind": "unknown"}):
        with TestCase().assertRaises(ValueError):
            result_of(value)


def test_app_ipc_roundtrips_run_request_expiry_and_cancellation() -> None:
    request = ipc.BrowserAppRequest(RUN, "request", FillApp(TARGET, "control", "Private fixture text"), 123)
    message = ipc.Envelope("epoch", 1, request)
    cancelled = ipc.Envelope("epoch", 2, ipc.BrowserAppCancelled(RUN, "request"))
    batch = ipc.Batch("epoch", (message, cancelled))
    assert ipc.decode_reply(ipc.encode_reply(batch)) == batch
    response = ipc.Request("epoch", "client", ipc.BrowserAppResponse(RUN, "request", AppSucceeded('{"filled":true}')))
    assert ipc.decode_request(ipc.encode_request(response)) == response
    data = ipc.event_data(message)
    payload = data["payload"]
    assert isinstance(payload, dict)
    for changed in ({**payload, "expires_at": True}, {**payload, "script": "unexpected"},
                    {**payload, "operation": {"kind": "evaluate", "expression": "globalThis"}}):
        with TestCase().assertRaises(ValueError):
            ipc.decode_event({**data, "payload": changed})


def test_public_app_control_requires_exact_live_worker_run_and_request_response(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        control = AppBrowserControl(server)
        with control.bind(RUN, threading.Event()):
            pending = pool.submit(control.request, "app_snapshot", {"target_id": TARGET}, threading.Event())
            message = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
            assert isinstance(message.payload, ipc.BrowserAppRequest)
            assert message.payload.operation == SnapshotApp(TARGET)
            assert time.time() < message.payload.expires_at <= time.time() + 20
            result = AppSucceeded('{"target_id":"' + TARGET + '","text":"Exact app fixture"}')
            assert not client.respond_browser_app(replace(message, epoch="old-epoch"), result)
            assert not client.respond_browser_app(replace(message, payload=replace(message.payload, run=RunRef("notes", "old-run"))), result)
            assert not client.respond_browser_app(replace(message, payload=replace(message.payload, request_id="other-request")), result)
            assert client.respond_browser_app(message, result)
            assert pending.result(timeout=2) == BrowserResult(result.text)
            assert not client.respond_browser_app(message, result)
            assert not any(isinstance(item.payload, ipc.BrowserAppCancelled) for item in drain(client))
        assert b"Exact app fixture" not in (tmp_path / "worker.json").read_bytes()


def test_unbound_or_precancelled_app_control_never_queues_a_host_operation(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client):
        control = AppBrowserControl(server)
        with TestCase().assertRaisesRegex(BrowserError, "No ordinary work run"):
            control.request("app_list", {}, threading.Event())
        cancel = threading.Event()
        cancel.set()
        with control.bind(RUN, threading.Event()), TestCase().assertRaises(InterruptedError):
            control.request("app_list", {}, cancel)
        assert drain(client) == ()


def test_app_control_cancellation_invalidates_the_exact_pending_request_without_replay(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        stop = threading.Event()
        pending = pool.submit(server.browser_app, RUN, ClickApp(TARGET, "control"), stop)
        message = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
        assert isinstance(message.payload, ipc.BrowserAppRequest)
        stop.set()
        assert pending.result(timeout=2) is AppCancelled.REQUEST
        cancelled = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppCancelled))
        assert cancelled.payload == ipc.BrowserAppCancelled(RUN, message.payload.request_id)
        assert not client.respond_browser_app(message, AppSucceeded('{"clicked":true}'))
        assert drain(client) == ()


def test_app_deadline_expires_queued_work_and_rejects_late_results(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(server.browser_app, RUN, SnapshotApp(TARGET), threading.Event(), timeout_s=.25)
        received: list[ipc.Envelope] = []
        while not pending.done():
            received.extend(client.poll())
            threading.Event().wait(.01)
        result = pending.result(timeout=1)
        received.extend(drain(client))
        assert isinstance(result, AppFailed) and "did not respond" in result.reason
        requests = [item for item in received if isinstance(item.payload, ipc.BrowserAppRequest)]
        assert len(requests) == 1 and isinstance(requests[0].payload, ipc.BrowserAppRequest)
        assert any(item.payload == ipc.BrowserAppCancelled(RUN, requests[0].payload.request_id) for item in received)
        assert not client.respond_browser_app(requests[0], AppSucceeded('{"text":"late"}'))


def test_lost_run_releases_app_request_even_without_setting_its_stop_event(tmp_path: Path) -> None:
    owning = threading.Event()
    owning.set()
    with bridge(tmp_path, owns=lambda run: run == RUN and owning.is_set()) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        stop = threading.Event()
        pending = pool.submit(server.browser_app, RUN, ListApps(), stop)
        message = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
        owning.clear()
        assert pending.result(timeout=2) is AppCancelled.REQUEST and not stop.is_set()
        assert not client.respond_browser_app(message, AppSucceeded('{"apps":[]}'))


def test_assistant_replacement_drops_app_waits_without_replaying_to_the_new_owner(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(server.browser_app, RUN, OpenApp("https://example.invalid/office"), threading.Event())
        read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
        replacement = BridgeClient(tmp_path)
        try:
            replacement.connect()
            result = pending.result(timeout=2)
            assert isinstance(result, AppFailed) and "connection changed" in result.reason
            assert not any(isinstance(item.payload, ipc.BrowserAppRequest) for item in drain(replacement))
        finally:
            replacement.close()


def test_close_disconnects_app_control_without_cancelling_the_task_or_destroying_cards(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        control = AppBrowserControl(server)
        stop = threading.Event()
        with control.bind(RUN, stop):
            pending = pool.submit(control.request, "app_list", {}, threading.Event())
            message = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
            assert isinstance(message.payload, ipc.BrowserAppRequest) and message.payload.operation == ListApps()
            control.close()
            with TestCase().assertRaises(InterruptedError):
                pending.result(timeout=2)
            assert not stop.is_set()
            cancelled = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppCancelled))
            assert cancelled.payload == ipc.BrowserAppCancelled(RUN, message.payload.request_id)
        with control.bind(RUN, stop):
            pending = pool.submit(control.request, "app_list", {}, threading.Event())
            resumed = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
            assert client.respond_browser_app(resumed, AppSucceeded('{"apps":["same-card"]}'))
            assert pending.result(timeout=2).text == '{"apps":["same-card"]}'
        assert not stop.is_set()


class RecordingEdge:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Json], threading.Event]] = []
        self.closed = False

    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        self.requests.append((operation, args, cancel))
        return BrowserResult("existing Edge result")

    def close(self) -> None:
        self.closed = True


def test_composite_keeps_edge_operations_unchanged_and_never_falls_back_for_app_failures(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        edge, apps = RecordingEdge(), AppBrowserControl(server)
        composite = CompositeBrowser(edge, apps)
        cancel = threading.Event()
        args: dict[str, Json] = {"target_id": "native-edge-tab", "expression": "existing Edge expression"}
        assert composite.request("evaluate", args, cancel).text == "existing Edge result"
        assert edge.requests == [("evaluate", args, cancel)]
        with apps.bind(RUN, threading.Event()):
            with TestCase().assertRaises(BrowserError):
                composite.request("app_evaluate", {"target_id": TARGET, "expression": "forbidden"}, cancel)
            with TestCase().assertRaises(BrowserError):
                composite.request("app_snapshot", {"target_id": "native-edge-tab"}, cancel)
            pending = pool.submit(composite.request, "app_snapshot", {"target_id": TARGET}, cancel)
            message = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
            assert client.respond_browser_app(message, AppFailed("This document target has closed."))
            with TestCase().assertRaisesRegex(BrowserError, "target has closed"):
                pending.result(timeout=2)
            composite.close()
            with TestCase().assertRaises(InterruptedError):
                composite.request("app_list", {}, cancel)
        assert edge.closed and len(edge.requests) == 1 and drain(client) == ()


class CompletedDuringCancellation:
    def __init__(self, cancel: threading.Event) -> None:
        self.cancel = cancel

    def browser_app(self, run: RunRef, operation: AppOperation, stop: StopSignal, *, timeout_s: float = 20) -> AppResult:
        assert not stop.is_set()
        self.cancel.set()
        return AppSucceeded('{"text":"Result completed as cancellation arrived"}')


def test_app_adapter_rechecks_cancellation_after_its_owner_returns_a_valid_result() -> None:
    cancel = threading.Event()
    control = AppBrowserControl(CompletedDuringCancellation(cancel))
    with control.bind(RUN, threading.Event()), TestCase().assertRaises(InterruptedError):
        control.request("app_list", {}, cancel)


def test_app_bridge_admits_only_one_pending_request(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(server.browser_app, RUN, ListApps(), threading.Event())
        message = read_until(client, lambda item: isinstance(item.payload, ipc.BrowserAppRequest))
        blocked = server.browser_app(RUN, SnapshotApp(TARGET), threading.Event())
        assert isinstance(blocked, AppFailed) and "Another" in blocked.reason
        assert client.respond_browser_app(message, AppSucceeded('{"apps":[]}'))
        assert pending.result(timeout=2) == AppSucceeded('{"apps":[]}')
