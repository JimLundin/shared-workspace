"""Explicit meeting JSON and validated enqueue callbacks for the local web view."""

from collections.abc import Callable
from typing import assert_never

from automation import task as work

from . import canvas_model as m
from . import meeting_canvas as c
from .llm import Json
from .meeting_canvas import MeetingCanvas
from .meeting_followup import CaptureNotice, NoCaptureNotice, TranscriptSource, action_data, draft_data
from .meeting_followup_store import ActionRevision, DraftRevision, Edited, Original
from .meeting_workflow import (
    ActionChange,
    ActionSaved,
    ActionSaveProblem,
    AvailablePackages,
    ChangeResult,
    DraftChange,
    DraftSaved,
    DraftSaveProblem,
    MeetingSnapshot,
    ObservedWork,
    PackagesUnavailable,
    WorkObservation,
    WorkPending,
    WorkUnavailable,
    package_matches,
    package_values,
)
from .work_window import task_label


def _reference(reference: c.Reference) -> dict[str, Json]:
    match reference:
        case c.TaskRef(task_id):
            return {"kind": "task", "task_id": task_id}
        case c.PackageRef(task_id, version):
            return {"kind": "notes", "task_id": task_id, "version": version}
        case c.SourceRef(package, source_id, source_sha256):
            return {"kind": "source", "task_id": package.task_id, "version": package.version,
                    "source_id": source_id, "source_sha256": source_sha256}
        case c.ActionRef(package, action_id):
            return {"kind": "action", "task_id": package.task_id, "version": package.version, "action_id": action_id}
        case c.DraftRef(package, draft_id):
            return {"kind": "draft", "task_id": package.task_id, "version": package.version, "draft_id": draft_id}
        case _:
            assert_never(reference)


def _readiness(readiness: c.Readiness | c.Unconfirmed) -> dict[str, Json]:
    match readiness:
        case c.Readiness.VERIFIED:
            return {"kind": "verified"}
        case c.Unconfirmed(reason):
            return {"kind": "unconfirmed", "reason": reason}
        case _:
            assert_never(readiness)


def _task(task: work.Task) -> dict[str, Json]:
    return {
        "id": task.id, "title": task.title, "state": work.phase(task.state), "label": task_label(task), "updated_at": task.updated_at,
        "checkpoint": task.checkpoint.summary if task.checkpoint is not None else None,
        "result": task.state.result if isinstance(task.state, work.Completed) else None,
    }


def _work(observation: WorkObservation) -> dict[str, Json]:
    match observation:
        case WorkPending.OBSERVATION:
            return {"kind": "pending"}
        case ObservedWork(task):
            return {"kind": "observed", "task": _task(task)}
        case WorkUnavailable(reason, previous):
            return {"kind": "unavailable", "reason": reason, "previous": _task(previous) if previous is not None else None}
        case _:
            assert_never(observation)


def _source(source: TranscriptSource) -> dict[str, Json]:
    notice: str | None
    match source.notice:
        case CaptureNotice(text):
            notice = text
        case NoCaptureNotice.NONE:
            notice = None
        case _:
            assert_never(source.notice)
    return {"id": source.id, "title": source.title, "path": source.path, "sha256": source.sha256,
            "started_at": source.started_at, "duration_seconds": source.duration_seconds, "capture": source.capture, "notice": notice}


def _citation(link: c.SourceLink) -> dict[str, Json]:
    citation = link.citation
    return {"card_id": link.card_id, "reference": _reference(link.reference), "source_id": citation.source_id,
            "source_sha256": citation.source_sha256, "line": citation.line, "quote": citation.quote,
            "start": citation.start, "end": citation.end, "offset_unit": "unicode_codepoints"}


def _statement(statement: c.Statement) -> dict[str, Json]:
    return {"text": statement.text, "citations": [_citation(link) for link in statement.citations]}


def _origin(origin: Original | Edited) -> dict[str, Json]:
    match origin:
        case Original.GENERATED:
            return {"kind": "generated"}
        case Edited(revision, at):
            return {"kind": "edited", "revision": revision, "at": at}
        case _:
            assert_never(origin)


def _draft(value: DraftRevision) -> dict[str, Json]:
    return {"draft_id": value.id, "revision": value.revision, "content": draft_data(value.content), "origin": _origin(value.origin),
            "files": {"eml": str(value.files.eml), "markdown": str(value.files.markdown)}}


def _action(value: ActionRevision) -> dict[str, Json]:
    return {"action_id": value.id, "revision": value.revision, "content": action_data(value.content), "origin": _origin(value.origin),
            "path": str(value.path)}


def _card(card: c.Card) -> dict[str, Json]:
    result: dict[str, Json] = {"id": card.item.id, "title": card.item.title, "text": m.body(card.item.content),
                              "canvas_revision": card.item.revision, "reference": _reference(card.reference)}
    match card:
        case c.TaskCard(work=observation):
            result.update(kind="task", work=_work(observation))
        case c.SourceCard(transcript=transcript, state=state):
            source = _source(transcript.source)
            source["text"] = transcript.text
            current: dict[str, Json]
            match state:
                case c.SourceState.CURRENT:
                    current = {"kind": "current"}
                case c.SourceState.UNCONFIRMED:
                    current = {"kind": "unconfirmed"}
                case c.ChangedSource(observed):
                    current = {"kind": "changed", "observed": _source(observed)}
                case _:
                    assert_never(state)
            result.update(kind="source", source=source, state=current)
        case c.NotesCard(notes=notes, decisions=decisions):
            result.update(kind="notes", notes=[_statement(value) for value in notes], decisions=[_statement(value) for value in decisions])
        case c.ActionCard(value=action, citations=citations):
            result.update(_action(action))
            result.update(kind="action", citations=[_citation(link) for link in citations])
        case c.DraftCard(value=draft, citations=citations, actions=actions):
            result.update(_draft(draft))
            result.update(kind="draft", citations=[_citation(link) for link in citations], actions=list(actions))
        case _:
            assert_never(card)
    return result


def _change(change: DraftChange | ActionChange) -> dict[str, Json]:
    result: dict[str, Json] = {"task_id": change.task_id, "version": change.version,
                              "expected_revision": change.expected_revision, "attempt_id": change.attempt_id}
    match change:
        case DraftChange(draft_id=draft_id, content=draft):
            result.update(kind="draft", draft_id=draft_id, content=draft_data(draft))
        case ActionChange(action_id=action_id, content=action):
            result.update(kind="action", action_id=action_id, content=action_data(action))
        case _:
            assert_never(change)
    return result


def _receipt(result: ChangeResult) -> dict[str, Json]:
    receipt: dict[str, Json] = {"change": _change(result.change)}
    match result:
        case DraftSaved(revision=draft):
            receipt.update(kind="draft_saved", saved=_draft(draft))
        case ActionSaved(revision=action):
            receipt.update(kind="action_saved", saved=_action(action))
        case DraftSaveProblem(reason=reason):
            receipt.update(kind="draft_error", reason=reason)
        case ActionSaveProblem(reason=reason):
            receipt.update(kind="action_error", reason=reason)
        case _:
            assert_never(result)
    return receipt


def catalog(snapshot: MeetingSnapshot) -> dict[str, Json]:
    """List exact observed package keys without selecting a latest version."""
    work.variant(snapshot, MeetingSnapshot, "meeting catalog observation")
    entries: list[Json] = []
    problems = [problem.reason for problem in snapshot.problems]
    for progress in snapshot.preparations:
        readiness: c.Readiness | c.Unconfirmed
        match progress.packages:
            case AvailablePackages():
                readiness = c.Readiness.VERIFIED
            case PackagesUnavailable(reason=reason):
                readiness = c.Unconfirmed(reason or "The saved meeting package could not be confirmed.")
                problems.append(readiness.reason)
            case _:
                assert_never(progress.packages)
        for package in package_values(progress.packages):
            publication = package.publication
            if not package_matches(progress.request, publication):
                raise ValueError("The observed meeting package belongs to different source evidence.")
            followup = publication.followup
            entries.append({"task_id": publication.task_id, "version": publication.version, "title": followup.title,
                            "source_digest": followup.sources.fingerprint, "readiness": _readiness(readiness), "work": _work(progress.work),
                            "counts": {"sources": len(followup.sources.transcripts), "notes": len(followup.notes),
                                       "decisions": len(followup.decisions), "actions": len(package.actions), "drafts": len(package.drafts)}})
    distinct_problems: dict[str, None] = dict.fromkeys(problems)
    return {"packages": entries, "problems": list(distinct_problems)}


def payload(canvas: MeetingCanvas) -> dict[str, Json]:
    """Serialize the immutable projection; text remains data, never HTML markup."""
    work.variant(canvas, MeetingCanvas, "meeting canvas projection")
    return {"task_id": canvas.reference.task_id, "version": canvas.reference.version, "title": canvas.title,
            "source_digest": canvas.source_digest, "readiness": _readiness(canvas.readiness), "problems": list(canvas.problems),
            "cards": [_card(card) for card in canvas.cards], "receipts": [_receipt(result) for result in canvas.receipts]}


def dispatch(
    snapshot: MeetingSnapshot, command: object, *,
    save_draft: Callable[[DraftChange], None], save_action: Callable[[ActionChange], None],
) -> dict[str, Json]:
    """Validate before enqueueing; the acknowledgment does not establish a save."""
    change = c.change_of(snapshot, command)
    match change:
        case DraftChange():
            save_draft(change)
        case ActionChange():
            save_action(change)
        case _:
            assert_never(change)
    return {"kind": "queued", "change": _change(change)}
