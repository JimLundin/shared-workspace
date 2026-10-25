"""Browser requests through public work, journal, and asynchronous UI boundaries."""

import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from assistant.browser_surface_operations import AppCancelled, AppFailed, AppOperation, AppResult, AppSucceeded, ListApps
from assistant.canvas_model import Request as CanvasRequest
from assistant.work_agent import WorkAgent
from assistant.work_service import BrowserAppHistory, browser_app_source, remember_browser_app
from assistant.work_window import ControlTask, Redirect
from automation.bridge import BridgeClient, BridgeServer
from automation.context import ContextStore
from automation.ipc import BrowserAppCancelled, BrowserAppRequest, Resync
from automation.routines import RoutineStore
from automation.store import TaskStore
from automation.task import CanvasSource, Control, RequestSource, RoutineSource, Running, RunRef, Task, TaskStatus, owns

from .test_agent_window import RecordingWindow
from .test_work_agent import RecordingBridge, WorkRig


@dataclass(frozen=True, slots=True)
class AppCall:
    operation: AppOperation
    cancel: threading.Event
    complete: Callable[[AppResult], None]


class AppUi:
    def __init__(self) -> None:
        self.calls: list[AppCall] = []

    def request(self, operation: AppOperation, cancel: threading.Event, complete: Callable[[AppResult], None]) -> None:
        self.calls.append(AppCall(operation, cancel, complete))


def test_browser_completion_keeps_the_observer_responsive_and_transport_off_the_ui_thread(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
        assert claim is not None
        request = BrowserAppRequest(claim.run, "app-request", ListApps(), rig.clock() + 120)
        rig.bridge.publish(request)
        rig.refresh()
        assert len(ui.calls) == 1 and ui.calls[0].operation == ListApps()
        assert not ui.calls[0].cancel.is_set() and not rig.bridge.browser_responses

        rig.agent.start("Queue another task while the app is busy")
        rig.refresh()
        assert rig.task("Queue another task while the app is busy")
        ui.calls[0].complete(AppSucceeded("The notes app is open."))
        rig.refresh()
        assert [(message.payload, result) for message, result in rig.bridge.browser_responses] == [
            (request, AppSucceeded("The notes app is open.")),
        ]
        assert all(identity != threading.get_ident() for identity in rig.bridge.browser_response_threads)

        ui.calls[0].complete(AppSucceeded("A duplicate callback must not replace the first result."))
        rig.refresh()
        assert len(rig.bridge.browser_responses) == 1 and len(ui.calls) == 1
        assert not rig.errors
    finally:
        rig.close()


def test_duplicate_browser_requests_do_not_repeat_an_effect_before_or_after_completion(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
        assert claim is not None
        request = BrowserAppRequest(claim.run, "app-request", ListApps(), rig.clock() + 120)
        rig.bridge.publish(request)
        rig.refresh()
        rig.bridge.publish(request)
        rig.refresh()
        assert len(ui.calls) == 1
        ui.calls[0].complete(AppSucceeded("The notes app is open."))
        rig.refresh()
        rig.bridge.publish(request)
        rig.refresh()
        assert len(ui.calls) == 1 and len(rig.bridge.browser_responses) == 1
        assert not rig.errors
    finally:
        rig.close()


def test_cancellation_arriving_before_a_browser_request_prevents_its_later_delivery(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
        assert claim is not None
        rig.bridge.publish(BrowserAppCancelled(claim.run, "cancelled-before-delivery"))
        rig.refresh()
        rig.bridge.publish(BrowserAppRequest(claim.run, "cancelled-before-delivery", ListApps(), rig.clock() + 120))
        rig.refresh()
        assert not ui.calls and not rig.bridge.browser_responses
        assert not rig.errors
    finally:
        rig.close()


def test_user_cancellation_fences_queued_page_actions_before_the_observer_finishes_its_poll(tmp_path: Path) -> None:
    armed, entered, release = threading.Event(), threading.Event(), threading.Event()

    def poll() -> None:
        if armed.is_set():
            armed.clear()
            entered.set()
            assert release.wait(5)

    ui = AppUi()
    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_poll=poll), browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
        assert claim is not None
        rig.bridge.publish(BrowserAppRequest(claim.run, "app-request", ListApps(), rig.clock() + 120))
        rig.refresh()
        assert len(ui.calls) == 1
        armed.set()
        observed = rig.agent.refresh()
        assert entered.wait(5)

        rig.agent.command(ControlTask(claim.task.id, Control.CANCEL))
        assert ui.calls[0].cancel.is_set(), "Accepted cancellation must fence a queued Qt action without waiting for transport."
        release.set()
        assert observed.wait(5)
        rig.refresh()
        cancelled = rig.tasks.get(claim.task.id)
        assert cancelled is not None and cancelled.state is TaskStatus.CANCELLED
        ui.calls[0].complete(AppSucceeded("A late completion cannot revive cancelled work."))
        rig.refresh()
        assert not rig.bridge.browser_responses and len(ui.calls) == 1
        assert not rig.errors
    finally:
        release.set()
        rig.close()


def test_queued_page_actions_expire_even_while_the_observer_is_waiting_on_transport(tmp_path: Path) -> None:
    armed, entered, release = threading.Event(), threading.Event(), threading.Event()

    def poll() -> None:
        if armed.is_set():
            armed.clear()
            entered.set()
            assert release.wait(5)

    ui = AppUi()
    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_poll=poll), browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
        assert claim is not None
        request = BrowserAppRequest(claim.run, "app-request", ListApps(), rig.clock() + 120)
        rig.bridge.publish(request)
        rig.refresh()
        assert len(ui.calls) == 1
        armed.set()
        observed = rig.agent.refresh()
        assert entered.wait(5)
        with rig.clock.lock:
            rig.clock.value = request.expires_at - 2
        assert not ui.calls[0].cancel.is_set(), "The action remains eligible before its deadline."
        assert ui.calls[0].cancel.is_set(), "A Qt action must observe expiry without waiting for the observer's next poll."
        release.set()
        assert observed.wait(5)
        ui.calls[0].complete(AppSucceeded("This completion arrived after expiry."))
        rig.refresh()
        rig.bridge.publish(BrowserAppRequest(claim.run, request.request_id, ListApps(), rig.clock() + 120))
        rig.refresh()
        assert not rig.bridge.browser_responses and len(ui.calls) == 1
        assert not rig.errors
    finally:
        release.set()
        rig.close()


def test_task_controls_accepted_during_poll_prevent_a_new_browser_request_from_dispatching(tmp_path: Path) -> None:
    controls: tuple[tuple[str, Callable[[WorkRig, str], None]], ...] = (
        ("cancel", lambda rig, identity: rig.agent.command(ControlTask(identity, Control.CANCEL))),
        ("pause", lambda rig, identity: rig.agent.command(ControlTask(identity, Control.PAUSE))),
        ("redirect", lambda rig, identity: rig.agent.command(Redirect(identity, "Inspect a different app instead"))),
        ("stop conversation", lambda rig, _: rig.agent.stop()),
    )
    for name, control in controls:
        armed, entered, release = threading.Event(), threading.Event(), threading.Event()

        def poll(armed: threading.Event = armed, entered: threading.Event = entered, release: threading.Event = release) -> None:
            if armed.is_set():
                armed.clear()
                entered.set()
                assert release.wait(5)

        ui = AppUi()
        rig = WorkRig(tmp_path / name, bridge=RecordingBridge(on_poll=poll), browser_app=ui.request)
        try:
            rig.agent.start("Inspect my app")
            rig.refresh()
            claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
            assert claim is not None
            armed.set()
            observed = rig.agent.refresh()
            assert entered.wait(5)
            rig.bridge.publish(BrowserAppRequest(claim.run, "app-request", ListApps(), rig.clock() + 120))
            control(rig, claim.task.id)
            release.set()
            assert observed.wait(5)
            rig.refresh()
            current = rig.tasks.get(claim.task.id)
            assert current is not None and not owns(current, claim.run)
            assert not ui.calls, f"{name} was accepted before the new page action was dispatched."
            assert not rig.bridge.browser_responses and not rig.errors
        finally:
            release.set()
            rig.close()


def test_overlapping_browser_requests_are_refused_without_queueing_late_page_effects(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
        assert claim is not None
        first = BrowserAppRequest(claim.run, "first-request", ListApps(), rig.clock() + 120)
        overlapping = BrowserAppRequest(claim.run, "overlapping-request", ListApps(offset=20), rig.clock() + 120)
        rig.bridge.publish(first)
        rig.refresh()
        rig.bridge.publish(overlapping)
        rig.refresh()
        assert len(ui.calls) == 1
        assert [(message.payload, result) for message, result in rig.bridge.browser_responses] == [
            (overlapping, AppFailed("Another browser app request is still pending. Wait for its result before trying again.")),
        ]
        ui.calls[0].complete(AppSucceeded("The notes app is open."))
        rig.refresh()
        rig.bridge.publish(overlapping)
        rig.refresh()
        assert len(ui.calls) == 1 and len(rig.bridge.browser_responses) == 2
        assert not rig.errors
    finally:
        rig.close()


def test_connection_or_cursor_loss_cancels_pending_apps_without_replaying_them(tmp_path: Path) -> None:
    for loss in ("reconnect", "new epoch", "cursor loss"):
        ui = AppUi()
        rig = WorkRig(tmp_path / loss, browser_app=ui.request)
        try:
            rig.agent.start("Inspect my app")
            rig.refresh()
            claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
            assert claim is not None
            request = BrowserAppRequest(claim.run, "app-request", ListApps(), rig.clock() + 120)
            rig.bridge.publish(request)
            rig.refresh()
            assert len(ui.calls) == 1
            if loss == "reconnect":
                rig.bridge.disconnect()
            elif loss == "new epoch":
                rig.bridge.restart("worker-two")
            else:
                rig.bridge.publish(Resync("The live event cursor was lost."))
            rig.refresh()
            assert ui.calls[0].cancel.is_set(), loss
            ui.calls[0].complete(AppSucceeded("A result from the abandoned connection."))
            rig.bridge.reconnect()
            rig.bridge.publish(request)
            rig.refresh()
            assert len(ui.calls) == 1 and not rig.bridge.browser_responses, loss

            fresh = BrowserAppRequest(claim.run, "fresh-request", ListApps(offset=20), rig.clock() + 120)
            rig.bridge.publish(fresh)
            rig.refresh()
            assert len(ui.calls) == 2 and not ui.calls[1].cancel.is_set()
            ui.calls[0].complete(AppSucceeded("The abandoned callback must not resolve the new request."))
            ui.calls[1].complete(AppSucceeded("A fresh app observation."))
            rig.refresh()
            assert [(message.payload, result) for message, result in rig.bridge.browser_responses] == [
                (fresh, AppSucceeded("A fresh app observation.")),
            ]
            assert not rig.errors
        finally:
            rig.close()


def test_cancellation_and_completion_are_bound_to_the_exact_browser_request_and_run(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "first-run", at=rig.clock())
        assert claim is not None
        first = BrowserAppRequest(claim.run, "reused-request-id", ListApps(), rig.clock() + 120)
        rig.bridge.publish(first)
        rig.refresh()
        rig.bridge.publish(BrowserAppCancelled(claim.run, first.request_id), epoch="old-worker")
        rig.bridge.publish(BrowserAppCancelled(RunRef(claim.task.id, "wrong-run"), first.request_id))
        rig.bridge.publish(BrowserAppCancelled(claim.run, "wrong-request"))
        rig.refresh()
        assert len(ui.calls) == 1 and not ui.calls[0].cancel.is_set()

        rig.tasks.control(claim.task.id, Control.PAUSE, at=rig.clock())
        rig.refresh()
        assert ui.calls[0].cancel.is_set()
        rig.tasks.control(claim.task.id, Control.RESUME, at=rig.clock())
        resumed = rig.tasks.claim("worker-one", "second-run", at=rig.clock())
        assert resumed is not None
        second = BrowserAppRequest(resumed.run, first.request_id, ListApps(offset=20), rig.clock() + 120)
        rig.bridge.publish(first)
        rig.bridge.publish(second)
        rig.refresh()
        assert len(ui.calls) == 2 and ui.calls[1].operation == ListApps(offset=20)
        ui.calls[0].complete(AppSucceeded("A late result from the retired run."))
        rig.refresh()
        assert not rig.bridge.browser_responses and not ui.calls[1].cancel.is_set()
        ui.calls[1].complete(AppSucceeded("The second run's current result."))
        rig.refresh()
        assert [(message.payload, result) for message, result in rig.bridge.browser_responses] == [
            (second, AppSucceeded("The second run's current result.")),
        ]
        cancelled = BrowserAppRequest(resumed.run, "cancelled-request", ListApps(), rig.clock() + 120)
        rig.bridge.publish(cancelled)
        rig.refresh()
        assert len(ui.calls) == 3
        rig.bridge.publish(BrowserAppCancelled(resumed.run, cancelled.request_id))
        rig.refresh()
        assert ui.calls[2].cancel.is_set()
        ui.calls[2].complete(AppSucceeded("A late result from the cancelled request."))
        rig.bridge.publish(cancelled)
        rig.refresh()
        assert len(rig.bridge.browser_responses) == 1 and len(ui.calls) == 3
        assert not rig.errors
    finally:
        rig.close()


def test_selected_source_canvas_work_cannot_gain_live_browser_app_access(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        rig.agent.submit_canvas(CanvasRequest("canvas-task", "workspace", "canvas-result", "Use only the selected evidence", ()))
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "canvas-run", at=rig.clock())
        assert claim is not None and isinstance(claim.task.source, CanvasSource)
        rig.bridge.publish(BrowserAppRequest(claim.run, "forbidden-live-read", ListApps(), rig.clock() + 120))
        rig.refresh()
        assert not ui.calls and not rig.bridge.browser_responses
        assert not rig.errors
    finally:
        rig.close()


def test_only_current_unexpired_browser_requests_from_a_standing_instruction_are_delivered(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        task = rig.tasks.submit("routine-task", "Check the app", "Check the app", RoutineSource("routine", 0, "occurrence"), at=1)
        claim = rig.tasks.claim("worker-one", "routine-run", at=2)
        assert claim is not None and claim.task.id == task.id
        rig.bridge.publish(BrowserAppRequest(RunRef(task.id, "retired-run"), "wrong-run", ListApps(), rig.clock() + 120))
        rig.bridge.publish(BrowserAppRequest(claim.run, "wrong-epoch", ListApps(), rig.clock() + 120), epoch="old-worker")
        rig.bridge.publish(BrowserAppRequest(claim.run, "expired", ListApps(), 100))
        rig.refresh()
        assert not ui.calls and not rig.bridge.browser_responses
        rig.bridge.publish(BrowserAppRequest(claim.run, "expired", ListApps(), rig.clock() + 120))
        rig.refresh()
        assert not ui.calls, "A new expiry cannot revive an already expired request identity."

        request = BrowserAppRequest(claim.run, "current-request", ListApps(), rig.clock() + 120)
        rig.bridge.publish(request)
        rig.refresh()
        assert len(ui.calls) == 1 and not ui.calls[0].cancel.is_set()
        ui.calls[0].complete(AppSucceeded("The standing instruction observed the app."))
        rig.refresh()
        assert [(message.payload, result) for message, result in rig.bridge.browser_responses] == [
            (request, AppSucceeded("The standing instruction observed the app.")),
        ]
        assert not rig.errors
    finally:
        rig.close()


def test_ui_terminal_results_are_preserved_but_agent_shutdown_discards_late_callbacks(tmp_path: Path) -> None:
    ui = AppUi()
    rig = WorkRig(tmp_path, browser_app=ui.request)
    try:
        rig.agent.start("Inspect my app")
        rig.refresh()
        claim = rig.tasks.claim("worker-one", "app-run", at=rig.clock())
        assert claim is not None
        results: tuple[AppResult, ...] = (
            AppSucceeded("The effect was observed before the page closed."),
            AppFailed("Browser apps are closing; the operation could not be confirmed."),
            AppCancelled.REQUEST,
        )
        for index, result in enumerate(results):
            request = BrowserAppRequest(claim.run, f"request-{index}", ListApps(), rig.clock() + 120)
            rig.bridge.publish(request)
            rig.refresh()
            assert len(ui.calls) == index + 1
            ui.calls[index].cancel.set()
            ui.calls[index].complete(result)
            rig.refresh()
            assert rig.bridge.browser_responses[index][0].payload == request
        assert [result for _, result in rig.bridge.browser_responses] == list(results)

        rig.bridge.publish(BrowserAppRequest(claim.run, "request-at-shutdown", ListApps(), rig.clock() + 120))
        rig.refresh()
        assert len(ui.calls) == 4
        rig.agent.close()
        assert ui.calls[3].cancel.is_set()
        ui.calls[3].complete(AppSucceeded("This late callback cannot reopen the bridge."))
        assert rig.agent.wait_closed(5)
        assert len(rig.bridge.browser_responses) == 3 and rig.bridge.closed.is_set()
        assert not rig.errors
    finally:
        rig.close()


def test_real_bridge_delivers_an_async_browser_result_while_work_commands_keep_running(tmp_path: Path) -> None:
    folder = tmp_path / "ipc"
    folder.mkdir()
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    received: queue.Queue[AppCall] = queue.Queue(maxsize=1)
    windows: list[RecordingWindow] = []
    errors: list[str] = []

    def current_run(run: RunRef) -> bool:
        task = tasks.get(run.task_id)
        return task is not None and owns(task, run)

    def window(agent: WorkAgent) -> RecordingWindow:
        result = RecordingWindow(agent.stop, on_submit=agent.start, on_reset=agent.new_conversation)
        windows.append(result)
        return result

    def request(operation: AppOperation, cancel: threading.Event, complete: Callable[[AppResult], None]) -> None:
        received.put_nowait(AppCall(operation, cancel, complete))

    server = BridgeServer(folder, epoch="1" * 32, wake=lambda: None, owns=current_run)
    agent = WorkAgent(
        tasks, RoutineStore(tmp_path / "routines.sqlite3"), ContextStore(tmp_path / "context.sqlite3"),
        BridgeClient(folder), window, on_snapshot=lambda _: None, on_done=lambda: None, report_error=errors.append,
        browser_app=request, assistant_pid=1234,
    )
    server.start()
    agent.start_worker()
    try:
        agent.start("Inspect my app")
        assert agent.refresh().wait(5)
        claim = tasks.claim("worker-one", "real-app-run", at=time.time())
        assert claim is not None
        with ThreadPoolExecutor(max_workers=1) as pool:
            response = pool.submit(server.browser_app, claim.run, ListApps(), threading.Event(), timeout_s=10)
            delivered = received.get(timeout=5)
            assert delivered.operation == ListApps() and not delivered.cancel.is_set() and not response.done()
            agent.start("Save another task while Qt prepares the app response")
            assert agent.refresh().wait(5)
            assert len(tasks.list_tasks()) == 2
            delivered.complete(AppSucceeded("Authenticated app observation reached the worker."))
            assert response.result(timeout=5) == AppSucceeded("Authenticated app observation reached the worker.")
        assert not errors
    finally:
        agent.close()
        assert agent.wait_closed(5)
        server.close()
        assert server.wait(5)
        for panel in windows:
            panel.paint()


def test_stop_conversation_fences_a_browser_request_before_the_first_task_snapshot_is_published(tmp_path: Path) -> None:
    runs: list[RunRef] = []
    cancelled_at_dispatch: list[bool] = []

    def wake() -> None:
        if not runs:
            claim = rig.tasks.claim("worker-one", "immediate-app-run", at=rig.clock())
            if claim is not None:
                runs.append(claim.run)
                rig.bridge.publish(BrowserAppRequest(claim.run, "immediate-request", ListApps(), rig.clock() + 120))

    def request(operation: AppOperation, cancel: threading.Event, complete: Callable[[AppResult], None]) -> None:
        rig.agent.stop()
        cancelled_at_dispatch.append(cancel.is_set())

    rig = WorkRig(tmp_path, bridge=RecordingBridge(on_wake=wake), browser_app=request, start=False)
    try:
        rig.agent.start("Inspect my app immediately")
        observed = rig.agent.refresh()
        rig.agent.start_worker()
        assert observed.wait(5)
        rig.refresh()
        assert cancelled_at_dispatch == [True], "Cancellation cannot depend on a UI snapshot that has not been published yet."
        current = rig.tasks.get(runs[0].task_id)
        assert current is not None and current.state is TaskStatus.CANCELLED
        assert not rig.bridge.browser_responses and not rig.errors
    finally:
        rig.close()


def test_browser_request_admission_ends_at_the_exact_expiry() -> None:
    run = RunRef("task", "run")
    task = Task(
        id="task", title="Inspect my app", prompt="Inspect my app", source=RequestSource("conversation"),
        priority=0, created_at=10, updated_at=20, revision=1, state=Running(run, "worker-one", 20), checkpoint=None,
    )
    request = BrowserAppRequest(run, "request", ListApps(), expires_at=100)
    assert browser_app_source(task, request, at=99.5) == RequestSource("conversation")
    assert browser_app_source(task, request, at=100) is None
    assert browser_app_source(task, request, at=100.5) is None


def test_browser_request_history_is_bounded_without_eviction_and_allows_a_new_owned_run(tmp_path: Path) -> None:
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    tasks.submit("task", "Inspect my app", "Inspect my app", RequestSource("conversation"), at=10)
    first = tasks.claim("worker-one", "first-run", at=20)
    assert first is not None
    identities = tuple(f"request-{number}" for number in range(512))
    history: BrowserAppHistory | None = None
    for identity in identities:
        history = remember_browser_app(history, first.run, identity)
        assert history is not None, "All 512 request identities must fit before the run reaches its replay-history limit."
    assert history is not None and history.requests == frozenset(identities)

    assert remember_browser_app(history, first.run, "request-512") is None
    assert remember_browser_app(history, first.run, "request-513") is None
    assert remember_browser_app(history, first.run, identities[0]) is None
    assert remember_browser_app(history, first.run, identities[-1]) is None
    assert history.requests == frozenset(identities), "Refusing an extra request must retain every previously seen identity."

    tasks.control(first.task.id, Control.PAUSE, at=30)
    tasks.control(first.task.id, Control.RESUME, at=40)
    resumed = tasks.claim("worker-one", "second-run", at=50)
    assert resumed is not None and resumed.run != first.run
    retired = BrowserAppRequest(first.run, identities[0], ListApps(), expires_at=100)
    current = BrowserAppRequest(resumed.run, identities[0], ListApps(), expires_at=100)
    assert browser_app_source(resumed.task, retired, at=50) is None
    assert browser_app_source(resumed.task, current, at=50) == RequestSource("conversation")
    assert remember_browser_app(history, resumed.run, identities[0]) == BrowserAppHistory(resumed.run, frozenset({"request-0"}))
    assert history.requests == frozenset(identities), "Starting a new run must not rewrite the previous immutable history."
