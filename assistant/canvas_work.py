"""Work observations and deliberate pinning of confirmed results onto the canvas."""

import hashlib
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import assert_never

from automation import task as work

from . import canvas_model as m
from .canvas_requests import matches_task
from .work_window import WorkSnapshot


class PinStatus(Enum):
    AVAILABLE = auto()


@dataclass(frozen=True, slots=True)
class CannotPin:
    reason: str


type PinAvailability = PinStatus | CannotPin


@dataclass(frozen=True, slots=True)
class WorkEntry:
    task_id: str
    title: str
    phase: work.Phase
    preview: str
    updated_at: float
    pin: PinAvailability


@dataclass(frozen=True, slots=True)
class WorkCatalog:
    entries: tuple[WorkEntry, ...]
    status: str


def _pin_availability(task: work.Task, snapshot: WorkSnapshot) -> PinAvailability:
    problem = next((problem for problem in snapshot.submission_problems if problem.task_id == task.id), None)
    if problem is not None:
        return CannotPin("This task's result could not be confirmed. " + problem.reason)
    if not isinstance(task.state, work.Completed):
        return CannotPin("Only completed work can be pinned.")
    try:
        m.bounded(task.state.result, "Pinned result")
    except ValueError as error:
        return CannotPin(str(error))
    return PinStatus.AVAILABLE


def catalog(snapshot: WorkSnapshot, query: str = "") -> WorkCatalog:
    terms = m.bounded(query, "Work search", 1_000, empty=True).casefold().split()
    entries: list[WorkEntry] = []
    for task in snapshot.tasks:
        preview = task.state.result if isinstance(task.state, work.Completed) else task.prompt
        searchable = f"{task.title}\n{task.prompt}\n{preview}".casefold()
        if all(term in searchable for term in terms):
            entries.append(WorkEntry(
                task.id, task.title, work.phase(task.state), preview[:320], task.updated_at,
                _pin_availability(task, snapshot),
            ))
    return WorkCatalog(tuple(entries), snapshot.status)


def source_notice(result: m.Result) -> str:
    return ("Original source snapshots are attached to this result." if result.sources
            else "No original source snapshots are attached to this result.")


def _belongs(result: m.Result, task_id: str) -> bool:
    if isinstance(result.state, m.Running):
        return result.state.request.id == task_id
    return result.state is m.Settled.COMPLETE and result.reference == m.TaskReference(task_id)


def _same_result(result: m.Result, task: work.Task) -> bool:
    if result.state is not m.Settled.COMPLETE or not isinstance(task.state, work.Completed) or result.text != task.state.result:
        return False
    match task.source:
        case work.CanvasSource():
            # This immutable preparation was confirmed before completion; the
            # card retains its original prompt while the task holds its compilation.
            return True
        case work.RequestSource() | work.RoutineSource():
            return result.prompt == task.prompt
        case _:
            assert_never(task.source)


def pin_completed(canvas: m.Canvas, snapshot: WorkSnapshot, task_id: str) -> m.Canvas:
    """Pin a saved result; a later task completion cannot rewrite a pinned snapshot."""
    m.bounded(task_id, "Task identity", 128)
    task = next((task for task in snapshot.tasks if task.id == task_id), None)
    if task is None:
        raise ValueError("This task is not available in the saved work snapshot.")
    availability = _pin_availability(task, snapshot)
    if isinstance(availability, CannotPin):
        raise ValueError(availability.reason)
    assert isinstance(task.state, work.Completed)
    text = m.bounded(task.state.result, "Pinned result")
    placed = {placement.item_id for placement in canvas.space.placements}
    placed_anywhere = {placement.item_id for space in canvas.spaces for placement in space.placements}
    originals = tuple(item for item in canvas.items if isinstance(item.content, m.Result) and _belongs(item.content, task_id))
    original = next((item for item in originals if item.id in placed), None)
    if original is None:
        original = next((item for item in originals if item.id not in placed_anywhere), None)
    if original is not None and isinstance(original.content, m.Result):
        previous = original.content
        if previous.state is m.Settled.COMPLETE:
            if not _same_result(previous, task):
                raise ValueError("An earlier result for this task is already pinned and has been kept. "
                                 "Use another workspace or delete the saved item before pinning a different result.")
            return m.add(canvas, original)
        run = previous.state if isinstance(previous.state, m.Running) else None
        if run is not None and not matches_task(run.request, task):
            raise ValueError("The saved task does not match this canvas request. Its original card has been kept.")
        result = m.Result(
            run.request.prompt if run is not None else previous.prompt, text,
            run.request.sources if run is not None else previous.sources, m.Settled.COMPLETE,
            run.steps if run is not None else previous.steps, reference=m.TaskReference(task.id),
        )
        return m.add(canvas, replace(original, content=result, revision=original.revision + int(result != previous)))
    identity = "work-" + hashlib.sha256(f"{canvas.current}\0{task.id}".encode()).hexdigest()
    if canvas.item(identity) is not None:
        raise ValueError("This task's canvas identity is already used by another item.")
    donor = next((item for item in originals if isinstance(item.content, m.Result) and _same_result(item.content, task)), None)
    if donor is not None:
        return m.add(canvas, m.Item(identity, donor.title, donor.content))
    result = m.Result(task.prompt, text, (), m.Settled.COMPLETE, reference=m.TaskReference(task.id))
    return m.add(canvas, m.Item(identity, task.title, result))
