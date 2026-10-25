"""Canvas contracts: exact context, independent arrangements, drafts and durable task IDs."""

import threading
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from assistant import canvas_model as m
from assistant.canvas_agent import WorkBackend, context_prompt
from assistant.canvas_controller import CanvasController
from assistant.canvas_requests import CanvasRequests, PreparedCanvas, captured_request
from assistant.canvas_store import CanvasStore, decode, encode
from assistant.work_window import AnswerTask, ControlTask, ReplyProblem, SubmissionProblem, WorkCommand, WorkSnapshot
from automation import task as work

from .test_work_window import approval_task


def selected_canvas() -> m.Canvas:
    canvas = m.add(m.Canvas(), m.Item("budget", "Budget", m.Source(m.FileSource("C:\\work\\budget.csv"), "240000", 1)))
    canvas = m.add(canvas, m.Item("brief", "Brief", m.Source(m.WebPage("https://example.test/brief"), "Limit: 200000", 1)))
    canvas = m.add(canvas, m.Item("private", "Unselected note", m.Note("Do not use this material")))
    return m.select(m.select(canvas, "budget"), "brief", multiple=True)


def result(canvas: m.Canvas, identity: str = "result") -> m.Result:
    item = canvas.item(identity)
    assert item is not None and isinstance(item.content, m.Result)
    return item.content


def confirmed_task(task: work.Task, request: m.Request) -> work.Task:
    prepared = PreparedCanvas(captured_request(request), context_prompt(request), 0)
    return replace(task, prompt=prepared.prompt, source=prepared.source)


def test_context_is_an_exact_snapshot_and_keeps_the_original_location() -> None:
    original = selected_canvas()
    canvas, request = m.request(original, "run-1", "result", "Compare these")
    assert tuple(source.item_id for source in request.sources) == ("budget", "brief")
    assert "Do not use this material" not in context_prompt(request)
    assert "C:\\work\\budget.csv" in context_prompt(request)
    assert "https://example.test/brief" in context_prompt(request)
    budget = m.Item("budget", "Budget", m.Source(m.FileSource("C:\\work\\budget.csv"), "260000", 2))
    canvas = m.observe(canvas, budget)
    assert request.sources[0].text == "240000"
    canvas = m.accept(canvas, m.Completed("run-1", "Difference: 40000"))
    assert m.outdated(canvas, "result")
    assert result(canvas).sources == request.sources
    assert original.item("result") is None


def test_refresh_preserves_draft_arrangement_selection_and_camera() -> None:
    canvas, _ = m.request(selected_canvas(), "run-1", "result", "Compare these")
    canvas = m.accept(canvas, m.Completed("run-1", "Difference: 40000"))
    canvas = m.draft(canvas, "result", "draft")
    canvas = m.edit(canvas, "draft", "My own wording")
    canvas = m.place(canvas, m.Placement("draft", 1234, 876, 560, 540))
    canvas = m.with_space(canvas, replace(canvas.space, camera=m.Camera(999, -233, .7), composer="A half-written request"))
    budget = m.Item("budget", "Budget", m.Source(m.FileSource("C:\\work\\budget.csv"), "260000", 2))
    canvas = m.observe(canvas, budget)
    assert m.outdated(canvas, "result") and m.outdated(canvas, "draft")
    before = canvas.space
    canvas, new_request = m.request(canvas, "run-2", "result", "Compare these", refresh=True)
    assert result(canvas).text == "Difference: 40000"
    assert new_request.sources[0].text == "260000"
    canvas = m.accept(canvas, m.Completed("run-2", "Difference: 60000"))
    assert result(canvas).text == "Difference: 60000" and not m.outdated(canvas, "result")
    draft = canvas.item("draft")
    assert draft is not None and isinstance(draft.content, m.Draft)
    assert draft.content.text == "My own wording" and draft.content.source.text == "Difference: 40000"
    assert m.outdated(canvas, "draft") and canvas.space == before


def test_late_results_cannot_overwrite_cancelled_or_newer_work() -> None:
    canvas, _ = m.request(selected_canvas(), "old", "result", "Compare these")
    stopped = m.accept(canvas, m.Stopped("old"))
    assert m.accept(stopped, m.Completed("old", "Too late")) is stopped
    canvas, _ = m.request(stopped, "new", "result", "Compare these", refresh=True)
    assert m.accept(canvas, m.Progress("old", "Also too late")) is canvas
    canvas = m.accept(canvas, m.Completed("new", "Current result"))
    assert m.accept(canvas, m.Failed("new", "Late transport failure")) is canvas
    assert result(canvas).text == "Current result"


def test_parking_keeps_work_but_excludes_it_from_new_context() -> None:
    canvas = selected_canvas()
    original = canvas.item("budget")
    position = next(p for p in canvas.space.placements if p.item_id == "budget")
    canvas = m.park(canvas, "budget")
    assert canvas.item("budget") == original and canvas.space.selection == ("brief",)
    assert m.select(canvas, "budget") is canvas
    canvas = m.park(canvas, "budget", parked=False)
    assert next(p for p in canvas.space.placements if p.item_id == "budget") == position
    assert canvas.space.selection == ("brief",)


def test_equal_observations_do_not_mark_results_outdated() -> None:
    canvas, _ = m.request(selected_canvas(), "run", "result", "Compare")
    canvas = m.accept(canvas, m.Completed("run", "Result"))
    before = canvas.item("budget")
    assert before is not None and isinstance(before.content, m.Source)
    canvas = m.observe(canvas, replace(before, content=replace(before.content, observed_at=99)))
    assert not m.outdated(canvas, "result")


def test_saved_canvas_restores_requests_and_drafts_without_reusing_handles(tmp_path: Path) -> None:
    canvas = selected_canvas()
    canvas = m.add(canvas, m.Item("window", "Editor", m.Source(m.LiveWindow(42, 99, "editor.exe", "Editor"), "", 1)))
    canvas, issued = m.request(canvas, "durable-task", "result", "Compare")
    canvas = m.with_space(canvas, replace(canvas.space, composer="Unsubmitted text", camera=m.Camera(400, 120, .65)))
    store = CanvasStore(tmp_path / "canvas.json")
    store.save(canvas)
    restored = store.load()
    assert restored.space == canvas.space
    assert isinstance(result(restored).state, m.Running)
    running = result(restored).state
    assert isinstance(running, m.Running) and running.request == issued
    window = restored.item("window")
    assert window is not None and isinstance(window.content, m.Source) and isinstance(window.content.origin, m.SavedWindow)
    assert "handle" not in store.path.read_text()


def test_saved_data_and_adapter_payloads_are_validated() -> None:
    with unittest.TestCase().assertRaises(ValueError):
        m.WebPage("javascript:alert(1)")
    with unittest.TestCase().assertRaises(ValueError):
        m.Camera(float("nan"), 0, 1)
    with unittest.TestCase().assertRaises(ValueError):
        m.Table(("A", "B"), (("missing B",),))
    with unittest.TestCase().assertRaises(ValueError):
        decode(encode(m.Canvas()).replace('"version":2', '"version":3'))
    with unittest.TestCase().assertRaises(ValueError):
        decode(encode(m.Canvas()).replace('"zoom":0.9', '"zoom":NaN'))
    with unittest.TestCase().assertRaises(ValueError):
        decode('{"version":1,"items":[],"spaces":[],"current":"missing","replace_taskbar":false}')


class RecordingBackend:
    __slots__ = ("answers", "attached", "cancelled", "previews", "submitted")

    def __init__(self) -> None:
        self.submitted: list[m.Request] = []
        self.attached: list[m.Request] = []
        self.cancelled: list[str] = []
        self.answers: list[tuple[str, str, str]] = []
        self.previews: list[tuple[str, m.Approval]] = []

    def submit(self, request: m.Request) -> None:
        self.submitted.append(request)

    def connect(self, publish: Callable[[m.Event], None]) -> None:
        pass

    def attach(self, request: m.Request) -> None:
        self.attached.append(request)

    def cancel(self, request_id: str) -> None:
        self.cancelled.append(request_id)

    def answer(self, request_id: str, question_id: str, text: str) -> bool:
        self.answers.append((request_id, question_id, text))
        return True

    def presented(self, request_id: str, preview: m.Approval) -> bool:
        self.previews.append((request_id, preview))
        return True


def test_controller_publication_questions_and_coalesced_persistence(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = CanvasStore(tmp_path / "canvas.json")
    controller = CanvasController(store, backend, changed=lambda: None)
    try:
        controller.commit(selected_canvas())
        assert controller.submit("Compare")
        request = backend.submitted[-1]
        producer = threading.Thread(target=lambda: controller.publish(m.NeedsInput(request.id, m.Question("question-1", "Which budget?", ""))))
        producer.start()
        producer.join()
        controller.drain()
        assert controller.answer(request.result_id, "Autumn")
        assert backend.answers == [(request.id, "question-1", "Autumn")]
        assert not controller.answer(request.result_id, "Duplicate")
        for index in range(100):
            controller.commit(m.edit(controller.state, "private", f"Edit {index}"))
        controller.cancel(request.result_id)
        controller.publish(m.Completed(request.id, "Late result"))
        controller.drain()
        assert result(controller.state, request.result_id).state is m.Settled.STOPPED
    finally:
        controller.close()
    assert store.load() == controller.state
    assert backend.cancelled == [request.id]


def test_invalid_saved_canvas_is_preserved_instead_of_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "canvas.json"
    path.write_text('{"version":99}')
    controller = CanvasController(CanvasStore(path), RecordingBackend(), changed=lambda: None)
    try:
        controller.commit(m.add(controller.state, m.Item("new", "New note", m.Note("Retained"))))
    finally:
        controller.close()
    assert path.read_text() == '{"version":99}'
    assert controller.store.path != path and controller.store.load().item("new") is not None


def test_durable_backend_routes_concurrent_outcomes_and_exact_replies() -> None:
    commands: list[WorkCommand] = []
    submissions: list[m.Request] = []
    snapshots = [WorkSnapshot()]
    events: list[m.Event] = []
    backend = WorkBackend(submissions.append, commands.append, lambda: snapshots[0], lambda: None)
    backend.publish = events.append
    first = m.Request("task-a", "main", "a", "First", (m.Evidence("source", 1, "Budget", "240000", m.LocalSource.NOTE),))
    second = m.Request("task-b", "main", "b", "Second", ())
    backend.submit(first)
    backend.submit(second)
    assert submissions == [first, second]
    waiting = work.Task("task-a", "First", "First", work.RequestSource("conversation"), 0, 1, 2, 1,
                        work.Waiting(work.Question("q-a", "Which file?", "")), None)
    completed = work.Task("task-b", "Second", "Second", work.RequestSource("another-conversation"), 0, 1, 2, 1,
                          work.Completed("The second result", ()), None)
    snapshots[0] = WorkSnapshot((confirmed_task(waiting, first), confirmed_task(completed, second)))
    backend.poll()
    assert m.Completed("task-b", "The second result", "Second") in events
    assert m.NeedsInput("task-a", m.Question("q-a", "Which file?", "")) in events
    assert not backend.answer("task-b", "q-a", "Wrong task")
    assert not backend.answer("task-a", "old-question", "Stale reply")
    assert backend.answer("task-a", "q-a", "budget.csv")
    assert not backend.answer("task-a", "q-a", "Repeated reply")
    assert commands[-1] == AnswerTask("task-a", "q-a", work.TextAnswer("budget.csv"))
    backend.cancel("task-a")
    assert commands[-1:] == [ControlTask("task-a", work.Control.CANCEL)]


def test_approval_requires_the_exact_rendered_preview_and_never_auto_refreshes() -> None:
    task, review = approval_task()
    request = m.Request(task.id, "main", "result", "Review this action", ())
    task = confirmed_task(task, request)
    snapshots = [WorkSnapshot((task,))]
    commands: list[WorkCommand] = []
    events: list[m.Event] = []
    backend = WorkBackend(lambda _request: None, commands.append, lambda: snapshots[0], lambda: None)
    backend.connect(events.append)
    backend.attach(request)
    backend.poll()
    assert not commands  # Missing volatile content never schedules another model run.
    snapshots[0] = WorkSnapshot((task,), reviews=(review,))
    backend.poll()
    assert not backend.answer(task.id, review.request.id, "approve")
    assert not backend.presented(task.id, m.Approval(review.request.id, review.title, "A different preview"))
    assert backend.presented(task.id, m.Approval(review.request.id, review.title, review.detail))
    snapshots[0] = WorkSnapshot((task,))  # Disconnect after rendering.
    assert not backend.answer(task.id, review.request.id, "approve")
    snapshots[0] = WorkSnapshot((task,), reviews=(review,))
    assert backend.presented(task.id, m.Approval(review.request.id, review.title, review.detail))
    assert backend.answer(task.id, review.request.id, "approve")
    assert commands == [AnswerTask(task.id, review.request.id, work.ApprovalDecision.APPROVED)]


def test_queued_voice_rechecks_new_questions_and_only_uses_active_canvas_context(tmp_path: Path) -> None:
    backend = RecordingBackend()
    controller = CanvasController(CanvasStore(tmp_path / "canvas.json"), backend, changed=lambda: None)
    spoken: list[str] = []
    pending: list[str] = []
    try:
        controller.commit(selected_canvas())
        def reply(text: str) -> bool:
            if pending:
                spoken.append(text)
                return True
            return False
        pending.append("Question arrived while voice was queued")
        controller.voice("Autumn", reply=reply, active=True, fallback=spoken.append)
        assert spoken == ["Autumn"] and not backend.submitted
        pending.clear()
        controller.voice("Use the foreground app", reply=reply, active=False, fallback=spoken.append)
        assert spoken[-1] == "Use the foreground app" and not backend.submitted
        controller.voice("Compare these", reply=reply, active=True, fallback=spoken.append)
        assert tuple(source.item_id for source in backend.submitted[-1].sources) == ("budget", "brief")
    finally:
        controller.close()


def test_reconnected_requests_keep_stable_prompts_and_watch_outside_recent_history(tmp_path: Path) -> None:
    canvas = m.add(m.Canvas(), m.Item("app", "Editor", m.Source(m.LiveWindow(44, 33, "editor.exe", "Notes"), "", 1)))
    canvas, issued = m.request(m.select(canvas, "app"), "old-task", "result", "Summarize")
    saved = CanvasStore(tmp_path / "canvas.json")
    saved.save(canvas)
    snapshots = [WorkSnapshot()]
    watched: list[tuple[str, ...]] = []
    backend = WorkBackend(lambda _request: None, lambda _: None, lambda: snapshots[0], lambda: None, watch=watched.append)
    controller = CanvasController(saved, backend, changed=lambda: None)
    try:
        restored = result(controller.state).state
        assert isinstance(restored, m.Running)
        assert context_prompt(restored.request) == context_prompt(issued)
        assert watched[-1] == ("old-task",)
        complete = work.Task("old-task", "Summarize", "Summarize", work.RequestSource("past-conversation"), 0, 1, 2, 1,
                             work.Completed("Recovered result", ()), None)
        snapshots[0] = WorkSnapshot((confirmed_task(complete, issued),))
        backend.poll()
        controller.drain()
        assert result(controller.state).text == "Recovered result"
        assert not watched[-1]
    finally:
        controller.close()


def test_uncertain_submission_retains_identity_and_recovers_without_duplicate_work(tmp_path: Path) -> None:
    snapshots = [WorkSnapshot()]
    submitted: list[m.Request] = []
    watched: list[tuple[str, ...]] = []
    backend = WorkBackend(submitted.append, lambda _: None, lambda: snapshots[0], lambda: None, watch=watched.append)
    controller = CanvasController(CanvasStore(tmp_path / "canvas.json"), backend, changed=lambda: None)
    try:
        controller.commit(selected_canvas())
        assert controller.submit("Compare")
        request = submitted[-1]
        request_id, prompt = request.id, context_prompt(request)
        result_id = controller.state.items[-1].id
        snapshots[0] = WorkSnapshot(submission_problems=(SubmissionProblem(request_id, "Could not confirm the journal write"),))
        backend.poll()
        controller.drain()
        running = result(controller.state, result_id).state
        assert isinstance(running, m.Running) and isinstance(running.stage, m.Attention)
        assert watched[-1] == (request_id,)
        controller.commit(m.observe(controller.state, m.Item("budget", "Budget", m.Source(m.FileSource("C:\\work\\budget.csv"), "999999", 2))))
        assert controller.retry(result_id)
        assert submitted == [request, request]
        assert not controller.retry(result_id)  # No duplicate click while reconnecting.
        completed = work.Task(request_id, "Recovered", prompt, work.RequestSource("original"), 0, 1, 2, 1,
                              work.Completed("Committed despite the lost acknowledgment", ()), None)
        snapshots[0] = WorkSnapshot((confirmed_task(completed, request),))
        backend.poll()
        controller.drain()
        assert result(controller.state, result_id).text == "Committed despite the lost acknowledgment"
        assert m.outdated(controller.state, result_id) and not watched[-1]
    finally:
        controller.close()


def test_submission_problem_blocks_an_unrelated_same_id_task_and_its_actions() -> None:
    task, review = approval_task()
    request = m.Request(task.id, "main", "result", "Canvas request", ())
    task = confirmed_task(task, request)
    snapshots = [WorkSnapshot((task,), reviews=(review,))]
    commands: list[WorkCommand] = []
    events: list[m.Event] = []
    backend = WorkBackend(lambda _request: None, commands.append, lambda: snapshots[0], lambda: None)
    backend.connect(events.append)
    backend.attach(request)
    preview = m.Approval(review.request.id, review.title, review.detail)
    assert backend.presented(task.id, preview)
    snapshots[0] = replace(snapshots[0], submission_problems=(SubmissionProblem(task.id, "Identity belongs to a different request"),))
    backend.poll()
    assert events == [m.NeedsAttention(task.id, "Identity belongs to a different request")]
    assert not backend.presented(task.id, preview)
    assert not backend.answer(task.id, review.request.id, "approve")
    assert not backend.answer(task.id, review.request.id, "decline")
    with unittest.TestCase().assertRaises(ValueError):
        backend.cancel(task.id)
    assert not commands
    snapshots[0] = WorkSnapshot((replace(task, state=work.Completed("Unrelated result", ())),),
                                submission_problems=snapshots[0].submission_problems)
    backend.poll()
    assert len(events) == 1
    snapshots[0] = WorkSnapshot((task,), reviews=(review,))
    backend.poll()
    assert events[-1] == m.NeedsInput(task.id, preview)
    assert not backend.answer(task.id, review.request.id, "approve")  # Must render again.


def test_saved_request_cannot_attach_one_identity_to_multiple_results() -> None:
    canvas, _ = m.request(selected_canvas(), "run", "result", "Compare")
    saved = encode(canvas)
    with unittest.TestCase().assertRaises(ValueError):
        decode(saved.replace('"result":"result"', '"result":"budget"'))
    other = m.Item("other-result", "Another", result(canvas))
    with unittest.TestCase().assertRaises(ValueError):
        decode(encode(m.add(canvas, other)))


def test_canvas_save_retains_previous_complete_file_during_windows_lock_retries(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "canvas.json")
    original = m.add(m.Canvas(), m.Item("note", "Note", m.Note("Saved text")))
    updated = m.edit(original, "note", "New text")
    store.save(original)
    replace_file = Path.replace
    attempts: list[m.Canvas] = []
    def locked(path: Path, target: Path) -> Path:
        attempts.append(store.load())
        if len(attempts) < 3:
            raise PermissionError("File is briefly open")
        return replace_file(path, target)
    with patch.object(Path, "replace", locked), patch("assistant.canvas_store.time.sleep"):
        store.save(updated)
    assert attempts == [original] * 3 and store.load() == updated
    with (patch.object(Path, "replace", side_effect=PermissionError("Permanent failure")), patch("assistant.canvas_store.time.sleep"),
          unittest.TestCase().assertRaises(PermissionError)):
        store.save(original)
    assert store.load() == updated


def test_failed_replies_restore_current_input_once_per_failed_attempt() -> None:
    task = work.Task("reply-task", "Question", "Question", work.RequestSource("conversation"), 0, 1, 2, 1,
                     work.Waiting(work.Question("q1", "Which file?", "")), None)
    request = m.Request(task.id, "main", "result", "Question", ())
    task = confirmed_task(task, request)
    snapshots = [WorkSnapshot((task,))]
    commands: list[WorkCommand] = []
    events: list[m.Event] = []
    backend = WorkBackend(lambda _request: None, commands.append, lambda: snapshots[0], lambda: None)
    backend.connect(events.append)
    backend.attach(request)
    backend.poll()
    assert backend.answer(task.id, "q1", "budget.csv")
    snapshots[0] = replace(snapshots[0], status="Another observer snapshot")
    backend.poll()
    assert len(events) == 1 and not backend.answer(task.id, "q1", "Duplicate")
    failure = ReplyProblem(task_id=task.id, request_id="q1", receipt_id="attempt-1", reason="Journal unavailable")
    snapshots[0] = replace(snapshots[0], reply_problems=(failure,))
    backend.poll()
    assert events[-1] == m.NeedsInput(task.id, m.Question("q1", "Which file?", ""), "Reply could not be saved · Journal unavailable")
    assert backend.answer(task.id, "q1", "budget.csv")
    backend.poll()  # The prior receipt can outlive the next queued answer.
    assert not backend.answer(task.id, "q1", "Duplicate") and len(commands) == 2
    snapshots[0] = replace(snapshots[0], reply_problems=(replace(failure, receipt_id="attempt-2"),))
    backend.poll()
    assert backend.answer(task.id, "q1", "budget.csv") and len(commands) == 3
    snapshots[0] = replace(snapshots[0], tasks=(replace(task, state=work.Waiting(work.Question("q2", "Which period?", ""))),))
    backend.poll()
    assert events[-1] == m.NeedsInput(task.id, m.Question("q2", "Which period?", ""))


def test_failed_approval_requires_fresh_display_before_each_retry() -> None:
    task, review = approval_task()
    request = m.Request(task.id, "main", "result", "Review this action", ())
    task = confirmed_task(task, request)
    snapshots = [WorkSnapshot((task,), reviews=(review,))]
    commands: list[WorkCommand] = []
    events: list[m.Event] = []
    backend = WorkBackend(lambda _request: None, commands.append, lambda: snapshots[0], lambda: None)
    backend.connect(events.append)
    backend.attach(request)
    preview = m.Approval(review.request.id, review.title, review.detail)
    backend.poll()
    assert backend.presented(task.id, preview)
    assert backend.answer(task.id, preview.id, "approve")
    snapshots[0] = replace(snapshots[0], reply_problems=(
        ReplyProblem(task_id=task.id, request_id=preview.id, receipt_id="failed-approval", reason="Write unavailable"),
    ))
    backend.poll()
    assert not backend.answer(task.id, preview.id, "approve")
    assert events[-1] == m.NeedsInput(task.id, preview, "Reply could not be saved · Write unavailable")
    assert backend.presented(task.id, preview)
    assert backend.answer(task.id, preview.id, "approve")
    backend.poll()
    assert not backend.answer(task.id, preview.id, "approve")
    assert commands == [AnswerTask(task.id, preview.id, work.ApprovalDecision.APPROVED)] * 2


def test_deleting_saved_items_reclaims_capacity_and_keeps_embedded_evidence() -> None:
    canvas = m.add(m.Canvas(), m.Item("source", "Source", m.Note("Original evidence")))
    canvas, _ = m.request(m.select(canvas, "source"), "run", "result", "Summarize")
    canvas = m.accept(canvas, m.Completed("run", "Saved result"))
    canvas = m.draft(canvas, "result", "draft")
    canvas = m.edit(canvas, "draft", "My own wording")
    other = replace(canvas.space, id="other", title="Another workspace")
    canvas = replace(canvas, spaces=(*canvas.spaces, other))
    deleted = m.delete(canvas, "source")
    assert deleted.item("source") is None
    assert all("source" not in space.selection and all(p.item_id != "source" for p in space.placements) for space in deleted.spaces)
    assert result(deleted).sources[0].text == "Original evidence" and m.outdated(deleted, "result")
    deleted = m.delete(deleted, "result")
    draft = deleted.item("draft")
    assert draft is not None and isinstance(draft.content, m.Draft)
    assert draft.content.text == "My own wording" and draft.content.source.text == "Saved result"
    assert decode(encode(deleted)) == deleted
    full = replace(deleted, items=(*deleted.items, *(m.Item(f"unused-{index}", "Unused", m.Note("")) for index in range(m.MAX_ITEMS - 1))))
    fresh = m.Item("fresh", "New work", m.Note("Can be saved"))
    with unittest.TestCase().assertRaises(ValueError):
        m.add(full, fresh)
    reclaimed = m.add(m.delete(full, "unused-0"), fresh)
    assert reclaimed.item("fresh") == fresh and len(reclaimed.items) == m.MAX_ITEMS
    running, _ = m.request(m.Canvas(), "active-request", "active", "Still working")
    with unittest.TestCase().assertRaises(ValueError):
        m.delete(running, "active")
    assert running.item("active") is not None


def test_rejected_fresh_submission_releases_watch_and_preserves_context_for_refresh(tmp_path: Path) -> None:
    attempts: list[m.Request] = []
    watched: list[tuple[str, ...]] = []
    snapshots = [WorkSnapshot()]
    def submit(request: m.Request) -> None:
        attempts.append(request)
        if len(attempts) in (1, 4):
            raise ValueError("The input queue is full")
    backend = WorkBackend(submit, lambda _: None, lambda: snapshots[0], lambda: None, watch=watched.append)
    controller = CanvasController(CanvasStore(tmp_path / "canvas.json"), backend, changed=lambda: None)
    try:
        controller.commit(selected_canvas())
        assert not controller.submit("Compare")
        identity = controller.state.items[-1].id
        failed = result(controller.state, identity)
        assert isinstance(failed.state, m.Failure) and not watched[-1]
        assert tuple(source.item_id for source in failed.sources) == ("budget", "brief")
        assert m.delete(controller.state, identity).item(identity) is None
        assert controller.submit("Compare", result_id=identity)
        assert attempts[1].id != attempts[0].id and attempts[1].prompt == attempts[0].prompt
        assert attempts[1].sources == attempts[0].sources
        admitted = work.Task(attempts[1].id, "Compare", "Compare", work.RequestSource("fixture"), 0, 1, 2, 1, work.TaskStatus.READY, None)
        snapshots[0] = WorkSnapshot((confirmed_task(admitted, attempts[1]),))
        controller.cancel(identity)
        stopped = result(controller.state, identity)
        assert stopped.state is m.Settled.STOPPED and stopped.sources == failed.sources
        assert controller.submit("Compare", result_id=identity)
        assert attempts[2].prompt == attempts[0].prompt and attempts[2].sources == attempts[0].sources
        issued = result(controller.state, identity).state
        assert isinstance(issued, m.Running)
        controller.publish(m.NeedsAttention(issued.request.id, "Could not confirm the saved task"))
        controller.drain()
        assert not controller.retry(identity)
        pending = result(controller.state, identity).state
        assert isinstance(pending, m.Running) and isinstance(pending.stage, m.Attention)
        assert pending.request == issued.request and list(watched[-1]) == [issued.request.id]
    finally:
        controller.close()


def test_oversized_context_fails_before_queueing_and_keeps_inspectable_sources(tmp_path: Path) -> None:
    submissions: list[m.Request] = []
    watched: list[tuple[str, ...]] = []
    backend = WorkBackend(submissions.append, lambda _: None, WorkSnapshot, lambda: None, watch=watched.append)
    controller = CanvasController(CanvasStore(tmp_path / "canvas.json"), backend, changed=lambda: None)
    try:
        canvas = m.add(m.Canvas(), m.Item("first", "First source", m.Note("A" * 40_000)))
        canvas = m.add(canvas, m.Item("second", "Second source", m.Note("B" * 40_000)))
        controller.commit(m.select(m.select(canvas, "first"), "second", multiple=True))
        assert not controller.submit("Compare")
        identity = controller.state.items[-1].id
        failed = result(controller.state, identity)
        assert isinstance(failed.state, m.Failure) and len(failed.sources) == 2
        assert not submissions and not watched
        assert m.delete(controller.state, identity).item(identity) is None
    finally:
        controller.close()


def test_reopened_canvas_requires_the_original_preparation_before_results_or_controls(tmp_path: Path) -> None:
    canvas, request = m.request(selected_canvas(), "saved-task", "result", "Compare")
    saved = CanvasStore(tmp_path / "canvas.json")
    saved.save(canvas)
    original = CanvasRequests(tmp_path / "original").prepare(request, at=1)
    other_request = replace(request, sources=(replace(request.sources[0], revision=2), *request.sources[1:]))
    other = CanvasRequests(tmp_path / "other").prepare(other_request, at=1)
    assert original.prompt == other.prompt and original.source != other.source
    row = work.Task(request.id, "Other preparation", other.prompt, other.source, 0, 1, 2, 1,
                    work.Completed("Do not attach this result", ()), None)
    snapshots = [WorkSnapshot((row,))]
    submissions: list[m.Request] = []
    commands: list[WorkCommand] = []
    backend = WorkBackend(submissions.append, commands.append, lambda: snapshots[0], lambda: None)
    controller = CanvasController(saved, backend, changed=lambda: None)
    try:
        for source in (other.source, work.RequestSource("legacy-unscoped")):
            snapshots[0] = WorkSnapshot((replace(row, source=source),))
            backend.poll()
            controller.drain()
            pending = result(controller.state)
            assert isinstance(pending.state, m.Running) and isinstance(pending.state.stage, m.Attention)
            assert pending.text == "" and pending.state.request == request
            snapshots[0] = WorkSnapshot((replace(row, source=source, state=work.Waiting(work.Question("question", "Which file?", ""))),))
            assert not backend.answer(request.id, "question", "budget.csv")
            with unittest.TestCase().assertRaisesRegex(ValueError, "confirmed"):
                backend.cancel(request.id)
        assert submissions == [] and commands == []
        snapshots[0] = WorkSnapshot((replace(row, source=original.source, prompt=original.prompt,
                                           state=work.Completed("Confirmed original result", ())),))
        backend.poll()
        controller.drain()
        finished = result(controller.state)
        assert finished.state is m.Settled.COMPLETE and finished.text == "Confirmed original result"
        assert finished.sources == request.sources and finished.reference == m.TaskReference(request.id)
    finally:
        controller.close()
