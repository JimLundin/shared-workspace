"""Selected-source execution through the real observer, journal and local SDK."""

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import TestCase

from assistant import canvas_model as m
from assistant.activity import ContextSnapshot
from assistant.agent import Agent
from assistant.agent_execution import AgentExecution
from assistant.canvas_agent import WorkBackend
from assistant.canvas_execution import CanvasWork
from assistant.canvas_requests import CanvasRequests
from assistant.llm import Json, Settings, ToolCall
from assistant.work_service import CanvasSubmission, Issued, WorkService
from assistant.work_window import AnswerTask, ControlTask, FollowUp, Redirect, task_detail
from assistant.workspace import Workspace
from automation.actions import Resources
from automation.context import ProjectNote, select_context
from automation.execution import WorkExecution
from automation.task import Blocked, CanvasSource, Claim, Completed, Control, Question, RequestSource, TaskStatus, TextAnswer, Waiting

from .fake_endpoint import FakeEndpoint, fake_endpoint
from .test_agent import FakeMachine, FakeWindow, calls, says
from .test_canvas_requests import selected_request
from .test_work_agent import RecordingBridge, WorkRig, invocation
from .test_workspace import live_tree


class CanvasRig:
    def __init__(self, folder: Path) -> None:
        self.ambient = invocation("UNSELECTED-WINDOW-COPPER-42")
        self.frontend = WorkRig(folder / "frontend", context=lambda: self.ambient)
        self.requests = CanvasRequests(self.frontend.tasks.path.parent / "canvas-requests")
        self.machine = FakeMachine()
        self.workspace = Workspace(live_tree(folder / "source"), folder / "workspace")
        self.errors: list[str] = []
        self.backend = WorkBackend(
            self.frontend.agent.submit_canvas, self.frontend.agent.command, self.frontend.agent.snapshot, lambda: None,
            watch=lambda identities: self.frontend.agent.watch(identities, owner="canvas-test"),
        )

    def execution(self, endpoint: FakeEndpoint, *, canvas: CanvasWork | None) -> AgentExecution:
        return AgentExecution(
            Settings(endpoint.url, "fixture", "transcribe", "chat", "gpt-6-astra", "", ""), self.machine,
            journal=lambda entry: None, record=lambda details: None, report_error=self.errors.append,
            close_background=self.machine.close_background, context=lambda: self.ambient, workspace=self.workspace, canvas=canvas,
        )

    def submit(self, request: m.Request) -> Claim:
        self.backend.submit(request)
        self.frontend.refresh()
        claim = self.frontend.tasks.claim("worker", request.id + "-run", at=self.frontend.clock())
        assert claim is not None and claim.task.id == request.id, self.frontend.errors
        return claim

    def conversation(self, prompt: str, run_id: str) -> Claim:
        self.frontend.agent.start(prompt)
        self.frontend.refresh()
        claim = self.frontend.tasks.claim("worker", run_id, at=self.frontend.clock())
        assert claim is not None
        return claim

    def close(self) -> None:
        self.frontend.close()


def test_canvas_excludes_ambient_history_and_project_context_across_resume_without_breaking_conversation(tmp_path: Path) -> None:
    rig = CanvasRig(tmp_path)
    try:
        with fake_endpoint() as endpoint:
            adapter = rig.execution(endpoint, canvas=CanvasWork(rig.requests))
            previous = rig.conversation("Discuss my unrelated travel plans.", "previous-run")
            endpoint.replies = [says("UNSELECTED-HISTORY-MARIGOLD-88")]
            done = adapter.run_work(previous, rig.frontend.tasks, FakeWindow(), threading.Event())
            assert isinstance(done, Completed), (done, rig.errors)
            assert rig.frontend.tasks.finish(previous.run, done, at=rig.frontend.clock()) is not None

            rig.frontend.saved_context.save(ProjectNote("atlas", "Atlas", "UNSELECTED-PROJECT-COBALT-59", "private-project.md", 1, 10_000))
            request = m.Request("canvas-probe", "space", "result", "Summarize the selected Atlas evidence.", (
                m.Evidence("selected", 1, "Atlas note", "SELECTED-EVIDENCE-JADE-73", m.LocalSource.NOTE),
            ))
            claim = rig.submit(request)
            assert isinstance(claim.task.source, CanvasSource) and claim.task.source != previous.task.source
            registered = next(context for task_id, context, _ in rig.frontend.bridge.registrations if task_id == request.id)
            assert registered is None
            saved = select_context(rig.frontend.saved_context.list(), claim.task.prompt, rig.frontend.clock()).describe()
            assert "UNSELECTED-PROJECT-COBALT-59" in saved
            endpoint.replies = [calls(("ask_user", {"question": "Which format should this summary use?"}))]
            waiting = adapter.run_work(claim, rig.frontend.tasks, FakeWindow(), threading.Event(), context=rig.ambient, context_text=saved)
            assert isinstance(waiting, Waiting) and isinstance(waiting.reason, Question), (waiting, rig.errors)
            initial = endpoint.received[-1].body.decode("utf-8")
            assert "SELECTED-EVIDENCE-JADE-73" in initial
            excluded = ("UNSELECTED-WINDOW-COPPER-42", "UNSELECTED-HISTORY-MARIGOLD-88", "UNSELECTED-PROJECT-COBALT-59")
            assert all(marker not in initial for marker in excluded)
            assert rig.frontend.tasks.finish(claim.run, waiting, at=rig.frontend.clock()) is not None
            assert rig.frontend.tasks.answer(request.id, waiting.reason.id, TextAnswer("Use one paragraph."), at=rig.frontend.clock()) is not None
            resumed = rig.frontend.tasks.claim("fresh-owner", "resumed-run", at=rig.frontend.clock())
            assert resumed is not None
            fresh = rig.execution(endpoint, canvas=CanvasWork(CanvasRequests(rig.requests.path.parent)))
            endpoint.replies = [says("The selected evidence was summarized.")]
            done = fresh.run_work(resumed, rig.frontend.tasks, FakeWindow(), threading.Event(), context=rig.ambient, context_text=saved)
            assert isinstance(done, Completed), (done, rig.errors)
            restarted = endpoint.received[-1].body.decode("utf-8")
            assert "SELECTED-EVIDENCE-JADE-73" in restarted and "Use one paragraph." in restarted
            assert all(marker not in restarted for marker in excluded)
            assert "screenshot" not in rig.machine.log
            assert rig.frontend.tasks.finish(resumed.run, done, at=rig.frontend.clock()) is not None

            ordinary = rig.conversation("Continue our earlier travel conversation.", "continued-run")
            endpoint.replies = [says("The earlier conversation was continued.")]
            continued = adapter.run_work(ordinary, rig.frontend.tasks, FakeWindow(), threading.Event(), context=rig.ambient)
            assert isinstance(continued, Completed), (continued, rig.errors)
            normal = endpoint.received[-1].body.decode("utf-8")
            assert "UNSELECTED-HISTORY-MARIGOLD-88" in normal and "UNSELECTED-WINDOW-COPPER-42" in normal
            assert "SELECTED-EVIDENCE-JADE-73" not in normal and rig.machine.log.count("screenshot") == 1
            assert not rig.errors and not rig.frontend.errors
    finally:
        rig.close()


def test_preparation_precedes_task_eligibility_and_retries_keep_the_same_scope(tmp_path: Path) -> None:
    requests = CanvasRequests(tmp_path / "frontend" / "canvas-requests")
    boundaries: list[bool] = []
    reads: list[bool] = []

    def on_register(identity: str) -> None:
        boundaries.append(requests.get(identity) is not None and rig.tasks.get(identity) is None)

    def ambient() -> ContextSnapshot:
        reads.append(True)
        return invocation("UNSELECTED-CALLER-CONTEXT")

    rig = WorkRig(tmp_path / "frontend", bridge=RecordingBridge(on_register=on_register), start=False,
                  context=ambient)
    try:
        request = selected_request()
        rig.agent.submit_canvas(request)
        assert not reads and not requests.path.exists() and not rig.agent.running()
        rig.agent.start_worker()
        rig.refresh()
        original = rig.tasks.get(request.id)
        saved = requests.get(request.id)
        assert original is not None and saved is not None and original.source == saved.source
        assert boundaries == [True] and rig.bridge.registrations[0][1] is None
        assert rig.agent.new_conversation()
        rig.agent.submit_canvas(request)
        rig.refresh()
        assert rig.tasks.get(request.id) == original and requests.get(request.id) == saved
        assert boundaries == [True]
        rig.agent.submit_canvas(replace(request, prompt="Use different evidence."))
        collision = rig.refresh()
        assert any(problem.task_id == request.id for problem in collision.submission_problems)
        assert rig.tasks.get(request.id) == original and requests.get(request.id) == saved
        rig.agent.submit_canvas(request)
        assert not rig.refresh().submission_problems
    finally:
        rig.close()


def test_failed_preparation_never_registers_or_submits_general_work(tmp_path: Path) -> None:
    rig = WorkRig(tmp_path / "frontend", start=False)
    blocked = tmp_path / "blocked"
    blocked.write_text("A file occupies the requested store folder.", encoding="utf-8")
    service = WorkService(rig.tasks, rig.routines, rig.saved_context, rig.bridge,
                          assistant_pid=1234, canvas_requests=CanvasRequests(blocked))
    try:
        try:
            service.apply(CanvasSubmission(m.Request("task", "space", "result", "Draft a greeting.", ())), at=1)
        except OSError:
            pass
        else:
            raise AssertionError("An unavailable preparation store accepted the request.")
        assert not rig.tasks.list_tasks() and not rig.bridge.registrations
    finally:
        rig.close()


def test_canvas_edits_are_rejected_before_context_capture_while_task_controls_and_answers_still_work(tmp_path: Path) -> None:
    reads: list[int] = []
    caller = threading.get_ident()

    def ambient() -> ContextSnapshot:
        reads.append(threading.get_ident())
        return invocation("UNSELECTED-EDIT-CONTEXT")

    rig = WorkRig(tmp_path / "frontend", context=ambient, start=False)
    requests = CanvasRequests(tmp_path / "requests")
    prepared = requests.prepare(selected_request(), at=1)
    original = rig.tasks.submit(prepared.task_id, "Canvas", prepared.prompt, prepared.source, at=2)
    service = WorkService(rig.tasks, rig.routines, rig.saved_context, rig.bridge, assistant_pid=1234, canvas_requests=requests)
    try:
        rig.agent.start_worker()
        rig.refresh()
        edits: tuple[FollowUp | Redirect, ...] = (FollowUp(original.id, "Add unrelated context."), Redirect(original.id, "Change the goal."))
        for index, command in enumerate(edits):
            with TestCase().assertRaisesRegex(ValueError, "canvas composer"):
                rig.agent.command(command)
            with TestCase().assertRaisesRegex(ValueError, "canvas composer"):
                service.apply(Issued(command, f"edit-{index}", RequestSource("chat"), invocation("UNSELECTED-EDIT-CONTEXT")), at=10)
        with TestCase().assertRaisesRegex(ValueError, "canvas composer"):
            rig.tasks.revise(original.id, "Change the saved prompt.", at=10)
        assert caller not in reads and not rig.bridge.registrations
        assert rig.tasks.get(original.id) == original and len(rig.tasks.list_tasks()) == 1
        assert "canvas composer" in task_detail(original)
        rig.agent.command(ControlTask(original.id, Control.PAUSE))
        rig.refresh()
        paused = rig.tasks.get(original.id)
        assert paused is not None and paused.state is TaskStatus.PAUSED
        rig.agent.command(ControlTask(original.id, Control.RESUME))
        rig.refresh()
        claim = rig.tasks.claim("worker", "canvas-run", at=rig.clock())
        assert claim is not None
        assert rig.tasks.finish(claim.run, Waiting(Question("format", "Which format?", "")), at=rig.clock()) is not None
        rig.refresh()
        rig.agent.command(AnswerTask(original.id, "format", TextAnswer("Markdown.")))
        rig.refresh()
        answered = rig.tasks.get(original.id)
        assert answered is not None and answered.state is TaskStatus.READY and answered.source == original.source
        rig.agent.command(ControlTask(original.id, Control.CANCEL))
        rig.refresh()
        cancelled = rig.tasks.get(original.id)
        assert cancelled is not None and cancelled.state is TaskStatus.CANCELLED and caller not in reads
    finally:
        rig.close()


def test_uncached_canvas_edits_defer_without_ambient_capture_and_are_rejected_by_the_observer(tmp_path: Path) -> None:
    reads: list[int] = []
    caller = threading.get_ident()

    def ambient() -> ContextSnapshot:
        reads.append(threading.get_ident())
        return invocation("UNSELECTED-UNCACHED-CONTEXT")

    rig = WorkRig(tmp_path / "frontend", context=ambient, start=False)
    prepared = CanvasRequests(tmp_path / "requests").prepare(selected_request(), at=1)
    original = rig.tasks.submit(prepared.task_id, "Canvas", prepared.prompt, prepared.source, at=2)
    try:
        rig.agent.command(FollowUp(original.id, "Add unrelated context."))
        rig.agent.command(Redirect(original.id, "Replace the captured request."))
        assert not reads
        rig.agent.start_worker()
        rig.refresh()
        assert caller not in reads and not rig.bridge.registrations
        assert len(rig.errors) == 2 and all("canvas composer" in error for error in rig.errors)
        assert rig.tasks.get(original.id) == original and len(rig.tasks.list_tasks()) == 1
    finally:
        rig.close()


def test_resume_revalidates_missing_changed_or_corrupt_preparations_before_model_contact(tmp_path: Path) -> None:
    for problem in ("missing", "fingerprint", "prompt", "snapshot"):
        rig = CanvasRig(tmp_path / problem)
        try:
            with fake_endpoint() as endpoint:
                claim = rig.submit(selected_request(problem))
                adapter = rig.execution(endpoint, canvas=CanvasWork(rig.requests))
                endpoint.replies = [calls(("ask_user", {"question": "Which format?"}))]
                waiting = adapter.run_work(claim, rig.frontend.tasks, FakeWindow(), threading.Event())
                assert isinstance(waiting, Waiting) and isinstance(waiting.reason, Question)
                assert rig.frontend.tasks.finish(claim.run, waiting, at=rig.frontend.clock()) is not None
                assert rig.frontend.tasks.answer(problem, waiting.reason.id, TextAnswer("Markdown."), at=rig.frontend.clock()) is not None
                if problem == "missing":
                    rig.requests.path.unlink()
                elif problem == "fingerprint":
                    source_data: dict[str, str] = {"kind": "canvas", "fingerprint": "a" * 64}
                    with closing(sqlite3.connect(rig.frontend.tasks.path)) as database, database:
                        database.execute("UPDATE tasks SET source=? WHERE id=?", (json.dumps(source_data), problem))
                elif problem == "prompt":
                    with closing(sqlite3.connect(rig.frontend.tasks.path)) as database, database:
                        database.execute("UPDATE tasks SET prompt=? WHERE id=?", ("Read unrelated material instead.", problem))
                else:
                    with closing(sqlite3.connect(rig.requests.path)) as database, database:
                        database.execute("UPDATE canvas_requests SET prompt=?", ("Substituted saved instruction.",))
                resumed = rig.frontend.tasks.claim("fresh-owner", "retry", at=rig.frontend.clock())
                assert resumed is not None
                endpoint.replies = [says("This should never run.")]
                outcome = rig.execution(endpoint, canvas=CanvasWork(CanvasRequests(rig.requests.path.parent))).run_work(
                    resumed, rig.frontend.tasks, FakeWindow(), threading.Event(), context=rig.ambient, context_text="UNSELECTED PROJECT",
                )
                assert isinstance(outcome, Waiting) and isinstance(outcome.reason, Blocked)
                assert "preparation could not be opened" in outcome.reason.reason
                assert len(endpoint.received) == 1 and not rig.machine.log
        finally:
            rig.close()


def test_scoped_tools_cannot_read_or_operate_the_computer_even_when_called_directly(tmp_path: Path) -> None:
    rig = CanvasRig(tmp_path)
    try:
        claim = rig.submit(selected_request())
        run = CanvasWork(rig.requests).session(claim.task)
        assert run is not None
        agent = Agent(Settings("", "", "", "", "", "", ""), rig.machine, FakeWindow(), threading.Event(),
                      journal=lambda entry: None, context=rig.ambient, context_reader=lambda: rig.ambient,
                      work=WorkExecution(claim, rig.frontend.tasks, Resources()), canvas=run)
        target = tmp_path / "unselected.txt"
        target.write_text("UNSELECTED-FILE-TEXT", encoding="utf-8")
        for name, arguments in (
            ("get_context", {}), ("get_clipboard", {}), ("list_windows", {}),
            ("read_file", {"path": str(target)}), ("run_powershell", {"command": "Get-Content private.txt"}),
            ("browser_open", {"url": "https://unselected.invalid"}), ("screenshot", {}),
            ("browser_app_list", {}), ("browser_app_open", {"url": "https://unselected.invalid"}),
            ("browser_app_navigate", {"target_id": "unselected", "url": "https://unselected.invalid"}),
            ("browser_app_snapshot", {"target_id": "unselected"}),
            ("browser_app_action", {"target_id": "unselected", "id": "send", "action": "click"}),
        ):
            output = agent.call(ToolCall(name, name, json.dumps(arguments)))
            assert not output.ok and "captured sources" in output.text
            assert "UNSELECTED-FILE-TEXT" not in output.text
        assert not rig.machine.log and not rig.frontend.tasks.actions(claim.task.id)
        with fake_endpoint() as endpoint:
            endpoint.replies = [says("The captured observations are summarized.")]
            result = rig.execution(endpoint, canvas=CanvasWork(rig.requests)).run_work(claim, rig.frontend.tasks, FakeWindow(), threading.Event())
            assert isinstance(result, Completed), (result, rig.errors)
            payload = cast(dict[str, Json], json.loads(endpoint.received[-1].body))
            advertised = payload["tools"]
            assert isinstance(advertised, list)
            names: set[str] = set()
            for spec in advertised:
                assert isinstance(spec, dict)
                tool_name = spec["name"]
                assert isinstance(tool_name, str)
                names.add(tool_name)
            assert names == {"ask_user", "work_actions", "reconcile_action"}
    finally:
        rig.close()


def test_explicit_canvas_source_blocks_without_configuration_and_ignores_meeting_name_heuristics(tmp_path: Path) -> None:
    rig = CanvasRig(tmp_path)
    try:
        claim = rig.submit(m.Request("meeting-followup-canvas", "space", "result", "Draft a greeting from this request alone.", ()))
        with fake_endpoint() as endpoint:
            unavailable = rig.execution(endpoint, canvas=None).run_work(claim, rig.frontend.tasks, FakeWindow(), threading.Event())
            assert isinstance(unavailable, Waiting) and isinstance(unavailable.reason, Blocked) and not endpoint.received
            endpoint.replies = [says("Hello from the canvas.")]
            done = rig.execution(endpoint, canvas=CanvasWork(rig.requests)).run_work(
                claim, rig.frontend.tasks, FakeWindow(), threading.Event(), context=rig.ambient, context_text="UNSELECTED-PROJECT",
            )
            assert isinstance(done, Completed), (done, rig.errors)
            body = endpoint.received[-1].body.decode("utf-8")
            assert "No canvas sources were selected" in body and "UNSELECTED" not in body
            assert not rig.machine.log
    finally:
        rig.close()
