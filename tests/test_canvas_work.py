"""Saved work becomes a canvas result without resubmission or invented sources."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from assistant import canvas_model as m
from assistant.canvas_agent import WorkBackend, context_prompt
from assistant.canvas_requests import PreparedCanvas, captured_request
from assistant.canvas_store import CanvasStore
from assistant.canvas_work import CannotPin, PinStatus, catalog, pin_completed, source_notice
from assistant.llm import Json
from assistant.work_window import SubmissionProblem, WorkCommand, WorkSnapshot
from automation import task as work


def task(identity: str, title: str, state: work.State, *, at: float = 10) -> work.Task:
    return work.Task(identity, title, "Prepare the requested report.", work.RequestSource("conversation"), 0, 1, at, 1, state, None)


def test_work_search_projects_the_saved_result_and_connection_status() -> None:
    saved = task("budget", "Budget report", work.Completed("The quarterly surplus is 40000.", ("Saved report verified.",)))
    other = task("travel", "Travel request", work.Waiting(work.Question("destination", "Which city?", "")))
    view = catalog(WorkSnapshot((other, saved), status="Showing saved work."), "BuDgEt quarter")
    assert len(view.entries) == 1
    entry = view.entries[0]
    assert (entry.task_id, entry.title, entry.phase, entry.preview) == (
        "budget", "Budget report", "completed", "The quarterly surplus is 40000.",
    )
    assert entry.updated_at == 10 and entry.pin is PinStatus.AVAILABLE
    assert view.status == "Showing saved work."


def test_completed_canvas_request_keeps_its_task_and_exact_sources_after_reopen(tmp_path: Path) -> None:
    source = m.Item("budget-source", "Budget", m.Source(m.FileSource("C:\\work\\budget.csv"), "240000", 1))
    canvas, request = m.request(m.select(m.add(m.Canvas(), source), source.id), "task-budget", "result", "Compare the budget")
    completed = m.accept(canvas, m.Completed(request.id, "The surplus is 40000."))
    store = CanvasStore(tmp_path / "canvas.json")
    store.save(completed)
    item = store.load().item("result")
    assert item is not None and isinstance(item.content, m.Result)
    assert item.content.reference == m.TaskReference("task-budget")
    assert item.content.sources == request.sources
    assert item.content.text == "The surplus is 40000." and item.content.state is m.Settled.COMPLETE


def test_pin_saves_the_full_confirmed_result_without_using_current_selection_as_sources(tmp_path: Path) -> None:
    selected = m.Item("private", "My unfinished note", m.Note("Personal wording that the task never saw."))
    original = m.select(m.add(m.Canvas(), selected), selected.id)
    original = m.with_space(original, replace(original.space, composer="Keep this unfinished question"))
    full_result = "語" * 63_990 + "0123456789"
    saved = task("completed-report", "Verified report", work.Completed(full_result, ("Read report output and verified it.",)))
    pinned = pin_completed(original, WorkSnapshot((saved,)), saved.id)
    assert len(pinned.items) == 2
    item = next(item for item in pinned.items if isinstance(item.content, m.Result))
    assert isinstance(item.content, m.Result)
    assert item.content.text == full_result and item.content.reference == m.TaskReference(saved.id)
    assert item.content.sources == ()
    assert source_notice(item.content) == "No original source snapshots are attached to this result."
    assert pinned.item(selected.id) == selected
    assert pinned.space.selection == original.space.selection and pinned.space.composer == original.space.composer
    store = CanvasStore(tmp_path / "canvas.json")
    store.save(pinned)
    assert store.load() == pinned


def test_pin_restores_the_original_card_and_keeps_its_sources_and_edited_draft() -> None:
    original_source = m.Item("source", "Budget", m.Source(m.FileSource("C:\\budget.csv"), "240000", 1))
    canvas, request = m.request(m.select(m.add(m.Canvas(), original_source), "source"), "task", "answer", "Compare")
    canvas = m.accept(canvas, m.Completed("task", "Surplus: 40000"))
    canvas = m.place(canvas, m.Placement("answer", 1234, 876, 510, 420))
    canvas = m.draft(canvas, "answer", "draft")
    canvas = m.edit(canvas, "draft", "Keep my rewritten answer")
    canvas = m.observe(canvas, replace(original_source, content=m.Source(m.FileSource("C:\\budget.csv"), "260000", 2)))
    canvas = m.select(m.park(canvas, "answer"), "draft")
    canvas = m.with_space(canvas, replace(canvas.space, composer="My unfinished next request", camera=m.Camera(22, 44, .6)))
    prepared = PreparedCanvas(captured_request(request), context_prompt(request), 0)
    saved = replace(task("task", "Budget comparison", work.Completed("Surplus: 40000", ("Verified current output",))),
                    prompt=prepared.prompt, source=prepared.source)
    snapshot = WorkSnapshot((saved,))
    pinned = pin_completed(canvas, snapshot, saved.id)
    assert len(pinned.items) == len(canvas.items)
    item = pinned.item("answer")
    assert item is not None and isinstance(item.content, m.Result)
    assert item.content.sources == request.sources and item.content.sources[0].text == "240000"
    assert next(placement for placement in pinned.space.placements if placement.item_id == "answer") == m.Placement("answer", 1234, 876, 510, 420)
    assert pinned.item("draft") == canvas.item("draft")
    assert (pinned.space.selection, pinned.space.composer, pinned.space.camera) == (
        canvas.space.selection, canvas.space.composer, canvas.space.camera,
    )
    assert m.outdated(pinned, "answer")
    assert pin_completed(pinned, snapshot, saved.id) == pinned


def test_unconfirmed_completion_cannot_be_pinned_as_this_requests_result() -> None:
    completed = task("same-id", "Different work", work.Completed("A result from another submission.", ()))
    snapshot = WorkSnapshot((completed,), submission_problems=(SubmissionProblem("same-id", "The saved request could not be confirmed."),))
    entry = catalog(snapshot).entries[0]
    assert isinstance(entry.pin, CannotPin) and "confirmed" in entry.pin.reason
    with unittest.TestCase().assertRaisesRegex(ValueError, "confirmed"):
        pin_completed(m.Canvas(), snapshot, completed.id)
    for state in (work.TaskStatus.CANCELLED, work.TaskStatus.READY, work.Waiting(work.Blocked("Resume after inspection."))):
        pending = WorkSnapshot((task("pending", "Not completed", state),))
        assert isinstance(catalog(pending).entries[0].pin, CannotPin)
        with unittest.TestCase().assertRaisesRegex(ValueError, "completed"):
            pin_completed(m.Canvas(), pending, "pending")


def test_catalog_reads_the_cached_work_projection_without_submitting_or_controlling_tasks() -> None:
    completed = task("saved", "Saved report", work.Completed("Ready to use.", ()))
    observations = [WorkSnapshot((completed,), status="Saved work is available.")]
    submitted: list[m.Request] = []
    commands: list[WorkCommand] = []
    backend = WorkBackend(submitted.append, commands.append, lambda: observations[0], lambda: None)
    view = catalog(backend.work_snapshot())
    assert [entry.task_id for entry in view.entries] == ["saved"]
    pinned = pin_completed(m.Canvas(), backend.work_snapshot(), "saved")
    assert len(pinned.items) == 1
    observations[0] = WorkSnapshot((completed,), status="Showing the last saved observation.")
    assert catalog(backend.work_snapshot()).status == "Showing the last saved observation."
    assert submitted == [] and commands == []


def test_a_later_goal_completion_cannot_silently_replace_an_earlier_pinned_result() -> None:
    earlier = task("task", "Original report", work.Completed("Original conclusion.", ()))
    pinned = pin_completed(m.Canvas(), WorkSnapshot((earlier,)), earlier.id)
    later = replace(earlier, prompt="Answer a different question.", state=work.Completed("Different conclusion.", ()),
                    updated_at=20, revision=3, goal_revision=1)
    with unittest.TestCase().assertRaisesRegex(ValueError, "earlier.*kept"):
        pin_completed(pinned, WorkSnapshot((later,)), later.id)
    original = pinned.items[0].content
    assert isinstance(original, m.Result) and original.text == "Original conclusion."
    assert original.prompt == earlier.prompt and original.reference == m.TaskReference(earlier.id)


def test_newer_completion_can_be_pinned_in_another_workspace_without_changing_the_first() -> None:
    earlier = task("task", "Original report", work.Completed("Original conclusion.", ()))
    first = pin_completed(m.Canvas(), WorkSnapshot((earlier,)), earlier.id)
    other = replace(first, spaces=(*first.spaces, m.Space("other", "Other workspace")), current="other")
    later = replace(earlier, prompt="Answer a different question.", state=work.Completed("Different conclusion.", ()),
                    updated_at=20, revision=3, goal_revision=1)
    pinned = pin_completed(other, WorkSnapshot((later,)), later.id)
    assert pinned.spaces[0] == first.space and pinned.item(first.items[0].id) == first.items[0]
    assert len(pinned.space.placements) == 1
    item = pinned.item(pinned.space.placements[0].item_id)
    assert item is not None and item.id != first.items[0].id and isinstance(item.content, m.Result)
    assert item.content.text == "Different conclusion." and item.content.prompt == later.prompt
    assert item.content.sources == () and item.content.reference == m.TaskReference(later.id)
    assert pin_completed(pinned, WorkSnapshot((later,)), later.id) == pinned


def test_identical_output_from_a_new_goal_keeps_each_workspaces_own_prompt() -> None:
    for source in (work.RequestSource("conversation"), work.RoutineSource("routine", 0, "occurrence")):
        earlier = replace(task("same-task", "Spring plan", work.Completed("Ready for launch.", ())),
                          prompt="Summarize the spring plan.", source=source)
        first = pin_completed(m.Canvas(), WorkSnapshot((earlier,)), earlier.id)
        later = replace(earlier, title="Autumn plan", prompt="Summarize the autumn plan.", updated_at=20, revision=3, goal_revision=1)
        with unittest.TestCase().assertRaisesRegex(ValueError, "earlier.*kept"):
            pin_completed(first, WorkSnapshot((later,)), later.id)
        other = replace(first, spaces=(*first.spaces, m.Space("other", "Other workspace")), current="other")
        pinned = pin_completed(other, WorkSnapshot((later,)), later.id)
        assert pinned.spaces[0] == first.space and pinned.item(first.items[0].id) == first.items[0]
        current = pinned.item(pinned.space.placements[0].item_id)
        assert current is not None and isinstance(current.content, m.Result)
        assert current.title == "Autumn plan" and current.content.prompt == "Summarize the autumn plan."
        assert current.content.text == "Ready for launch." and current.content.sources == ()
        assert pin_completed(pinned, WorkSnapshot((later,)), later.id) == pinned


def test_pin_preserves_a_long_task_prompt_through_the_real_canvas_store(tmp_path: Path) -> None:
    saved = replace(task("long-request", "Detailed request", work.Completed("Finished result.", ())), prompt="Detailed requirement.\n" * 500)
    pinned = pin_completed(m.Canvas(), WorkSnapshot((saved,)), saved.id)
    store = CanvasStore(tmp_path / "canvas.json")
    store.save(pinned)
    content = store.load().items[0].content
    assert isinstance(content, m.Result) and content.prompt == saved.prompt
    assert content.text == "Finished result."


def test_pin_copies_available_original_evidence_between_workspaces_without_moving_the_first_card() -> None:
    source = m.Item("source", "Budget", m.Source(m.FileSource("C:\\budget.csv"), "240000", 1))
    canvas, request = m.request(m.select(m.add(m.Canvas(), source), "source"), "task", "answer", "Compare")
    canvas = m.accept(canvas, m.Completed("task", "Surplus: 40000"))
    canvas = m.place(canvas, m.Placement("answer", 1234, 876, 510, 420))
    original = canvas.item("answer")
    other = replace(canvas, spaces=(*canvas.spaces, m.Space("other", "Other workspace")), current="other")
    prepared = PreparedCanvas(captured_request(request), context_prompt(request), 0)
    saved = replace(task("task", "Budget comparison", work.Completed("Surplus: 40000", ("Output verified",))),
                    prompt=prepared.prompt, source=prepared.source)
    pinned = pin_completed(other, WorkSnapshot((saved,)), saved.id)
    assert len(pinned.space.placements) == 1
    copy = pinned.item(pinned.space.placements[0].item_id)
    assert copy is not None and copy.id != "answer" and isinstance(copy.content, m.Result)
    assert copy.content.sources == request.sources
    assert original is not None and copy.content == original.content
    assert pinned.item("answer") == original and pinned.spaces[0] == canvas.space
    assert pin_completed(pinned, WorkSnapshot((saved,)), saved.id) == pinned


def test_pin_confirms_the_exact_running_request_but_rejects_a_same_id_with_different_sources() -> None:
    source = m.Item("source", "Budget", m.Source(m.FileSource("C:\\budget.csv"), "240000", 1))
    selected = m.select(m.add(m.Canvas(), source), "source")
    original, request = m.request(selected, "task", "answer", "Compare")
    prepared = PreparedCanvas(captured_request(request), context_prompt(request), 0)
    saved = replace(task("task", "Budget comparison", work.Completed("Surplus: 40000", ())),
                    prompt=prepared.prompt, source=prepared.source)
    changed = m.observe(selected, replace(source, content=m.Source(m.FileSource("C:\\budget.csv"), "Different private material", 2)))
    changed = m.observe(changed, replace(source, content=m.Source(m.FileSource("C:\\budget.csv"), "240000", 3)))
    conflicting, conflicting_request = m.request(changed, "task", "answer", "Compare")
    assert context_prompt(conflicting_request) == prepared.prompt and conflicting_request.sources != request.sources
    with unittest.TestCase().assertRaisesRegex(ValueError, "does not match"):
        pin_completed(conflicting, WorkSnapshot((saved,)), saved.id)
    unchanged = conflicting.item("answer")
    assert unchanged is not None and isinstance(unchanged.content, m.Result) and isinstance(unchanged.content.state, m.Running)
    pinned = pin_completed(original, WorkSnapshot((saved,)), saved.id)
    confirmed = pinned.item("answer")
    assert confirmed is not None and isinstance(confirmed.content, m.Result)
    assert confirmed.content.state is m.Settled.COMPLETE and confirmed.content.sources == request.sources
    assert confirmed.content.reference == m.TaskReference(saved.id)


def test_legacy_results_remain_readable_and_unknown_reference_tags_are_rejected(tmp_path: Path) -> None:
    legacy_content: dict[str, Json] = {
        "kind": "result", "prompt": "Original question", "text": "Original answer", "sources": [],
        "status": "complete", "reason": "", "steps": [], "request": None,
    }
    legacy: dict[str, Json] = {
        "version": 1, "current": "main", "replace_taskbar": False,
        "items": [{"id": "old-result", "title": "Legacy report", "revision": 1, "content": legacy_content}],
        "spaces": [{"id": "main", "title": "Workspace", "selection": [], "composer": "Unfinished text",
                    "camera": {"x": 0, "y": 0, "zoom": 1}, "placements": []}],
    }
    path = tmp_path / "canvas.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    store = CanvasStore(path)
    restored = store.load()
    content = restored.items[0].content
    assert isinstance(content, m.Result) and content.reference is m.NoTaskReference.NONE
    assert content.text == "Original answer" and restored.space.composer == "Unfinished text"
    store.save(restored)
    assert store.load() == restored
    legacy_content["reference"] = {"kind": "future-task"}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    with unittest.TestCase().assertRaisesRegex(ValueError, "task reference"):
        store.load()
