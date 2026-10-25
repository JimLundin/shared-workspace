"""Durable SDK turns, source checks and installation through application seams."""

import json
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import cast

from assistant.agent import Agent
from assistant.agent_execution import AgentExecution
from assistant.llm import Settings, ToolCall
from assistant.workspace import CheckInterrupted, Workspace
from automation.actions import Resources, plan
from automation.execution import WorkExecution
from automation.store import TaskStore
from automation.task import (
    ActionReturned,
    ActionSpec,
    ActionVerified,
    Blocked,
    Claim,
    Completed,
    Control,
    Question,
    RequestSource,
    RoutineSource,
    Source,
    TextAnswer,
    Waiting,
)
from keeper import copy_source, source_digest

from .fake_endpoint import Reply, fake_endpoint
from .test_agent import FakeMachine, FakeWindow, calls, says
from .test_work_actions import claimed
from .test_workspace import live_tree


def request(store: TaskStore, identity: str, prompt: str, source: Source | None = None) -> Claim:
    store.submit(identity, "Requested work", prompt, source or RequestSource("chat"), at=time.time())
    claim = store.claim("worker", identity + "-run", at=time.time())
    assert claim is not None
    return claim


def execution(settings: Settings, workspace: Workspace, errors: list[str]) -> AgentExecution:
    machine = FakeMachine()
    return AgentExecution(
        settings, machine, journal=lambda record: None, record=lambda record: None,
        report_error=errors.append, close_background=machine.close_background, workspace=workspace,
    )


def test_question_suspends_and_recorded_answer_survives_a_fresh_sdk_process_owner(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "work.sqlite3")
    errors: list[str] = []
    source = Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")
    with fake_endpoint() as endpoint:
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "gpt-6-astra", "", "")
        endpoint.replies = [calls(("ask_user", {"question": "Which report folder?"}))]
        first = request(store, "task", "Prepare the report.")
        outcome = execution(settings, source, errors).run_work(first, store, FakeWindow(), threading.Event())
        assert isinstance(outcome, Waiting) and isinstance(outcome.reason, Question)
        assert len(endpoint.received) == 1
        assert store.finish(first.run, outcome, at=time.time()) is not None
        assert store.answer("task", outcome.reason.id, TextAnswer("Use the Finance reports folder."), at=time.time()) is not None
        reopened = TaskStore(tmp_path / "work.sqlite3")
        resumed = reopened.claim("restarted-worker", "resumed-run", at=time.time())
        assert resumed is not None
        endpoint.replies = [calls(("ask_user", {"question": "Which report folder?"})), says("The report folder is resolved.")]
        result = execution(settings, source, errors).run_work(
            resumed, reopened, FakeWindow(), threading.Event(), context_text="The latest project context is Finance.",
        )
        assert isinstance(result, Completed), (result, errors)
        body = endpoint.received[-1].body.decode("utf-8")
        assert "Use the Finance reports folder." in body and "latest project context is Finance" in body
        assert not errors


def test_sdk_history_and_source_drafts_are_scoped_to_the_conversation(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "work.sqlite3")
    errors: list[str] = []
    source = Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")
    with fake_endpoint() as endpoint:
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "gpt-6-astra", "", "")
        adapter = execution(settings, source, errors)
        first = request(store, "one", "Inspect the first project.")
        draft = adapter.conversation(first).workspace
        draft.ensure()
        draft.write("draft.py", "DRAFT = 'saved for this conversation'\n")
        endpoint.replies = [says("Remember project reference ORCHID-471.")]
        done = adapter.run_work(first, store, FakeWindow(), threading.Event())
        assert isinstance(done, Completed) and store.finish(first.run, done, at=time.time()) is not None
        second = request(store, "two", "Continue that project.")
        endpoint.replies = [says("Continued.")]
        done = adapter.run_work(second, store, FakeWindow(), threading.Event())
        assert isinstance(done, Completed)
        assert "ORCHID-471" in endpoint.received[-1].body.decode("utf-8")
        assert adapter.conversation(second).workspace.read("draft.py") == "DRAFT = 'saved for this conversation'\n"
        assert store.finish(second.run, done, at=time.time()) is not None
        for identity, origin in (
            ("routine", RoutineSource("meeting-notes", 1, "meeting-one")),
            ("new-chat", RequestSource("different-chat")),
        ):
            other = request(store, identity, "Inspect a separate project.", origin)
            endpoint.replies = [says("Separate project inspected.")]
            done = adapter.run_work(other, store, FakeWindow(), threading.Event())
            assert isinstance(done, Completed)
            assert "ORCHID-471" not in endpoint.received[-1].body.decode("utf-8")
            separate = adapter.conversation(other).workspace
            separate.ensure()
            assert "draft.py" not in separate.files()
            assert store.finish(other.run, done, at=time.time()) is not None
        later = request(store, "after-restart", "Reopen the draft.")
        restarted = execution(settings, source, errors).conversation(later).workspace
        restarted.ensure()
        assert restarted.read("draft.py") == "DRAFT = 'saved for this conversation'\n"
        assert not errors


def test_endpoint_failure_and_step_limit_never_report_completion(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "work.sqlite3")
    errors: list[str] = []
    source = Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")
    with fake_endpoint() as endpoint:
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "gpt-6-astra", "", "")
        first = request(store, "unavailable", "Inspect the computer.")
        endpoint.replies = [Reply(b'{"error":{"message":"model unavailable"}}', status=503)]
        outcome = execution(settings, source, errors).run_work(first, store, FakeWindow(), threading.Event())
        assert isinstance(outcome, Waiting) and isinstance(outcome.reason, Blocked)
        assert "Endpoint request failed" in outcome.reason.reason and len(endpoint.received) == 1
        assert store.finish(first.run, outcome, at=time.time()) is not None
        second = request(store, "limited", "Inspect the computer.")
        work = WorkExecution(second, store, Resources())
        endpoint.replies = [calls(("get_clipboard", {}))]
        stop = threading.Event()
        agent = Agent(settings, FakeMachine(), FakeWindow(), stop, journal=lambda entry: None, work=work, max_steps=1)
        assert not agent.run(second.task.prompt)
        result = work.outcome(agent.report, stop)
        assert isinstance(result, Waiting) and isinstance(result.reason, Blocked)
        assert "not finished" in result.reason.reason and len(endpoint.received) == 2


def test_runtime_checks_are_recorded_before_candidate_import_and_failures_remain_fixable(tmp_path: Path) -> None:
    store, claim, _ = claimed(tmp_path)
    workspace = Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")
    workspace.ensure()
    marker = tmp_path / "candidate-imported"
    workspace.write("probe.py", (
        "import sqlite3\nfrom contextlib import closing\nfrom pathlib import Path\n"
        f"with closing(sqlite3.connect({str(tmp_path / 'work.sqlite3')!r})) as database, database:\n"
        "    assert database.execute(\"SELECT count(*) FROM actions WHERE outcome_kind='running'\").fetchone()[0] == 1\n"
        f"Path({str(marker)!r}).touch()\n"
    ))
    work = WorkExecution(claim, store, Resources())
    agent = Agent(Settings("", "", "", "", "", "", ""), FakeMachine(), FakeWindow(), threading.Event(),
                  journal=lambda entry: None, work=work, workspace=workspace)
    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("CREATE TRIGGER refuse_execution BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT,'cannot record'); END")
    result = agent.call(ToolCall("refused", "check_changes", "{}"))
    assert not result.ok and not marker.exists() and not store.actions("task")
    with closing(sqlite3.connect(tmp_path / "work.sqlite3")) as database, database:
        database.execute("DROP TRIGGER refuse_execution")
    result = agent.call(ToolCall("checked", "check_changes", "{}"))
    assert result.ok, result.text
    assert marker.exists() and isinstance(store.actions("task")[0].outcome, ActionVerified)
    workspace.write("config.py", "GREETING = ''\n")
    result = agent.call(ToolCall("failed", "check_changes", "{}"))
    assert not result.ok and "FAILED" in result.text
    assert not store.unresolved_actions("task"), "A completed check with failing assertions is a known result, not an uncertain mutation."
    repair_args: dict[str, str] = {"path": "config.py", "content": "GREETING = 'fixed'\n"}
    repair = agent.call(ToolCall("repair", "write_source", json.dumps(repair_args)))
    assert repair.ok and workspace.read("config.py") == "GREETING = 'fixed'\n"


def test_cancelling_a_check_terminates_its_candidate_process(tmp_path: Path) -> None:
    workspace = Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")
    workspace.ensure()
    stopped = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        port = cast("tuple[str, int]", listener.getsockname())[1]
        workspace.write("waiting_candidate.py", (
            "import socket\n"
            f"with socket.create_connection(('127.0.0.1', {port})) as connection:\n"
            "    connection.recv(1)\n"
        ))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(workspace.inspect_check, fast=True, cancel=stopped)
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(5)
                    stopped.set()
                    result = pending.result(5)
                    assert isinstance(result, CheckInterrupted) and "Stopped" in result.reason
                    try:
                        closed = connection.recv(1) == b""
                    except ConnectionResetError:
                        closed = True  # Windows may reset sockets when taskkill terminates their owner.
                    assert closed
            finally:
                stopped.set()


def test_nonfinite_tool_arguments_never_enter_effect_storage(tmp_path: Path) -> None:
    store, claim, _ = claimed(tmp_path)
    work = WorkExecution(claim, store, Resources())
    destination = tmp_path / "forbidden.txt"
    payload: dict[str, object] = {"path": str(destination), "content": "not written", "unexpected": float("nan")}
    args = json.dumps(payload)
    agent = Agent(Settings("", "", "", "", "", "", ""), FakeMachine(), FakeWindow(), threading.Event(),
                  journal=lambda entry: None, work=work)
    result = agent.call(ToolCall("invalid", "write_file", args))
    assert not result.ok and not destination.exists() and not store.actions("task")


def test_installation_resumes_only_after_running_source_matches_the_requested_digest(tmp_path: Path) -> None:
    store, claim, clock = claimed(tmp_path)
    workspace = Workspace(live_tree(tmp_path / "live"), tmp_path / "workspace")
    workspace.ensure()
    workspace.write("config.py", "GREETING = 'installed update'\n")
    specification = plan("apply_changes", {
        "summary": "Updated greeting", "source_digest": source_digest(workspace.root), "destination": str(workspace.live.absolute()),
    })
    assert isinstance(specification, ActionSpec)
    assert store.begin_action(claim.run, "install", specification, at=clock()) is not None
    store.finish_action("install", ActionReturned("Keeper installation requested."), at=clock())
    store.finish(claim.run, Waiting(Blocked("Waiting for keeper.")), at=clock())
    store.control("task", Control.RESUME, at=clock())
    pending = store.claim("worker", "uninstalled-run", at=clock())
    assert pending is not None
    errors: list[str] = []
    with fake_endpoint() as endpoint:
        settings = Settings(endpoint.url, "fixture", "transcribe", "chat", "gpt-6-astra", "", "")
        adapter = execution(settings, workspace, errors)
        outcome = adapter.run_work(pending, store, FakeWindow(), threading.Event())
        assert isinstance(outcome, Waiting) and not endpoint.received
        store.finish(pending.run, outcome, at=time.time())
        copy_source(workspace.root, workspace.live)
        store.control("task", Control.RESUME, at=time.time())
        installed = store.claim("restarted-worker", "installed-run", at=time.time())
        assert installed is not None
        endpoint.replies = [says("Installed source matches the requested update.")]
        result = execution(settings, workspace, errors).run_work(installed, store, FakeWindow(), threading.Event())
        assert isinstance(result, Completed), (result, errors)
        assert isinstance(store.actions("task")[0].outcome, ActionVerified) and not errors
