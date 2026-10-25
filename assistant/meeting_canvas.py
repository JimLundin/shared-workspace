"""Project verified meeting work onto the canvas without owning storage or editors.

Managed items are read-only Source proxies for arrangement and selection. Their
typed cards retain the package, source and edit identities used by the meeting
workflow. Canvas item revisions never stand in for saved meeting revisions.
"""

import hashlib
import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import assert_never, cast

from automation.task import digest, identifier, integer, variant

from . import canvas_model as m
from .meeting_followup import AssignedDue, AssignedOwner, Citation, CitedText, EmailAddress, Transcript, TranscriptSource, action_of, draft_of
from .meeting_followup_store import MAX_EDIT_REVISIONS, ActionRevision, DraftRevision, Edited, PublishedFollowup
from .meeting_workflow import (
    ActionChange,
    AvailablePackages,
    CatalogProblem,
    ChangeResult,
    DraftChange,
    FollowupPackage,
    FollowupProgress,
    MeetingSnapshot,
    PackagesUnavailable,
    WorkObservation,
    WorkUnavailable,
    package_matches,
    package_values,
    previous_task,
)
from .work_window import task_label


@dataclass(frozen=True, slots=True)
class PackageRef:
    task_id: str
    version: str

    def __post_init__(self) -> None:
        identifier(self.task_id, "meeting canvas task")
        digest(self.version, "meeting canvas package version")


@dataclass(frozen=True, slots=True)
class TaskRef:
    task_id: str

    def __post_init__(self) -> None:
        identifier(self.task_id, "meeting canvas task")


@dataclass(frozen=True, slots=True)
class SourceRef:
    package: PackageRef
    source_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        variant(self.package, PackageRef, "meeting canvas source package")
        identifier(self.source_id, "meeting canvas source")
        digest(self.source_sha256, "meeting canvas source digest")


@dataclass(frozen=True, slots=True)
class ActionRef:
    package: PackageRef
    action_id: str

    def __post_init__(self) -> None:
        variant(self.package, PackageRef, "meeting canvas action package")
        identifier(self.action_id, "meeting canvas action")


@dataclass(frozen=True, slots=True)
class DraftRef:
    package: PackageRef
    draft_id: str

    def __post_init__(self) -> None:
        variant(self.package, PackageRef, "meeting canvas draft package")
        identifier(self.draft_id, "meeting canvas draft")


type Reference = TaskRef | PackageRef | SourceRef | ActionRef | DraftRef


class Readiness(Enum):
    VERIFIED = auto()


@dataclass(frozen=True, slots=True)
class Unconfirmed:
    reason: str


class SourceState(Enum):
    CURRENT = auto()
    UNCONFIRMED = auto()


@dataclass(frozen=True, slots=True)
class ChangedSource:
    observed: TranscriptSource


@dataclass(frozen=True, slots=True)
class SourceLink:
    card_id: str
    reference: SourceRef
    citation: Citation


@dataclass(frozen=True, slots=True)
class Statement:
    text: str
    citations: tuple[SourceLink, ...]


@dataclass(frozen=True, slots=True)
class TaskCard:
    item: m.Item
    reference: TaskRef
    work: WorkObservation


@dataclass(frozen=True, slots=True)
class SourceCard:
    item: m.Item
    reference: SourceRef
    transcript: Transcript
    state: SourceState | ChangedSource


@dataclass(frozen=True, slots=True)
class NotesCard:
    item: m.Item
    reference: PackageRef
    notes: tuple[Statement, ...]
    decisions: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class ActionCard:
    item: m.Item
    reference: ActionRef
    value: ActionRevision
    citations: tuple[SourceLink, ...]


@dataclass(frozen=True, slots=True)
class DraftCard:
    item: m.Item
    reference: DraftRef
    value: DraftRevision
    citations: tuple[SourceLink, ...]
    actions: tuple[str, ...]


type Card = TaskCard | SourceCard | NotesCard | ActionCard | DraftCard


@dataclass(frozen=True, slots=True)
class MeetingCanvas:
    reference: PackageRef
    title: str
    source_digest: str
    cards: tuple[Card, ...]
    readiness: Readiness | Unconfirmed
    problems: tuple[str, ...]
    receipts: tuple[ChangeResult, ...]

    @property
    def items(self) -> tuple[m.Item, ...]:
        return tuple(card.item for card in self.cards)


def _identity(reference: Reference) -> str:
    parts: tuple[str, ...]
    match reference:
        case TaskRef(task_id):
            parts = ("task", task_id)
        case PackageRef(task_id, version):
            parts = ("notes", task_id, version)
        case SourceRef(package, source_id, source_sha256):
            parts = ("source", package.task_id, package.version, source_id, source_sha256)
        case ActionRef(package, action_id):
            parts = ("action", package.task_id, package.version, action_id)
        case DraftRef(package, draft_id):
            parts = ("draft", package.task_id, package.version, draft_id)
        case _:
            assert_never(reference)
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "meeting-" + hashlib.sha256(encoded).hexdigest()


def _item(reference: Reference, title: str, text: str, origin: m.Origin, at: float) -> m.Item:
    if len(text) > m.MAX_TEXT:
        notice = "\n\n[Preview shortened; the meeting retains the complete content.]"
        text = text[:m.MAX_TEXT - len(notice)] + notice
    return m.Item(_identity(reference), title[:240], m.Source(origin, text, at))


def _selected(snapshot: MeetingSnapshot, reference: PackageRef) -> tuple[FollowupProgress, FollowupPackage]:
    variant(snapshot, MeetingSnapshot, "meeting canvas observation")
    progress = next((value for value in snapshot.preparations if value.request.task_id == reference.task_id), None)
    if progress is None:
        raise ValueError("That meeting task is not in the observed preparations.")
    package = next((value for value in package_values(progress.packages) if value.publication.version == reference.version), None)
    if package is None:
        raise ValueError("Select an exact observed meeting package version.")
    if not package_matches(progress.request, package.publication):
        raise ValueError("The meeting package belongs to different source evidence.")
    return progress, package


def _links(reference: PackageRef, citations: tuple[Citation, ...]) -> tuple[SourceLink, ...]:
    result: list[SourceLink] = []
    for citation in citations:
        source = SourceRef(reference, citation.source_id, citation.source_sha256)
        result.append(SourceLink(_identity(source), source, citation))
    return tuple(result)


def _statements(reference: PackageRef, values: tuple[CitedText, ...]) -> tuple[Statement, ...]:
    return tuple(Statement(value.text, _links(reference, value.citations)) for value in values)


def _change_reference(change: DraftChange | ActionChange) -> DraftRef | ActionRef:
    package = PackageRef(change.task_id, change.version)
    match change:
        case DraftChange(draft_id=draft_id):
            return DraftRef(package, draft_id)
        case ActionChange(action_id=action_id):
            return ActionRef(package, action_id)
        case _:
            assert_never(change)


def _source_cards(snapshot: MeetingSnapshot, reference: PackageRef, publication: PublishedFollowup, at: float) -> tuple[SourceCard, ...]:
    current_sources = {(source.id, source.path): source for source in snapshot.transcripts}
    catalog_confirmed = not any(isinstance(problem, CatalogProblem) for problem in snapshot.problems)
    cards: list[SourceCard] = []
    for index, transcript in enumerate(publication.followup.sources.transcripts, 1):
        source = transcript.source
        source_ref = SourceRef(reference, source.id, source.sha256)
        retained = publication.directory / "sources" / f"{index:03d}.md"
        observed = current_sources.get((source.id, source.path))
        state: SourceState | ChangedSource
        if not catalog_confirmed or observed is None:
            state = SourceState.UNCONFIRMED
        else:
            state = SourceState.CURRENT if observed == source else ChangedSource(observed)
        cards.append(SourceCard(
            _item(source_ref, source.title, transcript.text, m.FileSource(str(retained)), at), source_ref, transcript, state,
        ))
    return tuple(cards)


def project(snapshot: MeetingSnapshot, task_id: str, version: str) -> MeetingCanvas:
    """Project one explicit package; version hashes have no chronological order."""
    reference = PackageRef(task_id, version)
    progress, package = _selected(snapshot, reference)
    publication = package.publication
    followup = publication.followup
    task_ref = TaskRef(task_id)
    task = previous_task(progress.work)
    task_text = task_label(task) if task is not None else "Waiting for a work observation."
    if isinstance(progress.work, WorkUnavailable):
        task_text += "\n" + progress.work.reason
    cards: list[Card] = [TaskCard(
        _item(task_ref, "Task · " + followup.title, task_text, m.LocalSource.NOTE, progress.request.at), task_ref, progress.work,
    )]
    cards.extend(_source_cards(snapshot, reference, publication, progress.request.at))
    notes, decisions = _statements(reference, followup.notes), _statements(reference, followup.decisions)
    notes_text = "Notes\n\n" + "\n\n".join(value.text for value in notes)
    if decisions:
        notes_text += "\n\nDecisions\n\n" + "\n\n".join(value.text for value in decisions)
    cards.append(NotesCard(
        _item(reference, followup.title, notes_text, m.FileSource(str(publication.notes_path)), progress.request.at),
        reference, notes, decisions,
    ))
    generated_actions = {value.id: value for value in followup.actions}
    generated_drafts = {value.id: value for value in followup.drafts}
    if len(package.actions) != len(generated_actions) or {value.id for value in package.actions} != set(generated_actions):
        raise ValueError("The observed action revisions do not belong to this meeting package.")
    if len(package.drafts) != len(generated_drafts) or {value.id for value in package.drafts} != set(generated_drafts):
        raise ValueError("The observed draft revisions do not belong to this meeting package.")
    for action in package.actions:
        action_ref = ActionRef(reference, action.id)
        content = action.content
        owner = content.owner.name if isinstance(content.owner, AssignedOwner) else "Not stated"
        due = content.due.text if isinstance(content.due, AssignedDue) else "Not stated"
        text = f"{content.text}\n\nOwner: {owner}\nDue: {due}\nStatus: {content.status.name.lower()}"
        at = action.origin.at if isinstance(action.origin, Edited) else progress.request.at
        cards.append(ActionCard(
            _item(action_ref, "Action · " + content.text, text, m.FileSource(str(action.path)), at),
            action_ref, action, _links(reference, generated_actions[action.id].citations),
        ))
    for saved in package.drafts:
        draft_ref = DraftRef(reference, saved.id)
        draft = generated_drafts[saved.id]
        recipients = ", ".join(person.name + (f" <{person.email.value}>" if isinstance(person.email, EmailAddress) else " (email not stated)")
                               for person in saved.content.recipients) or "Not stated"
        text = f"Subject: {saved.content.subject}\nRecipients: {recipients}\n\n{saved.content.body}"
        at = saved.origin.at if isinstance(saved.origin, Edited) else progress.request.at
        cards.append(DraftCard(
            _item(draft_ref, "Draft · " + saved.content.subject, text, m.FileSource(str(saved.files.eml)), at),
            draft_ref, saved, _links(reference, draft.citations), tuple(_identity(ActionRef(reference, identity)) for identity in draft.action_ids),
        ))
    readiness: Readiness | Unconfirmed
    problems = [problem.reason for problem in snapshot.problems]
    match progress.packages:
        case AvailablePackages():
            readiness = Readiness.VERIFIED
        case PackagesUnavailable(reason=reason):
            readiness = Unconfirmed(reason or "The saved meeting package could not be confirmed.")
            problems.append(readiness.reason)
        case _:
            assert_never(progress.packages)
    if isinstance(progress.work, WorkUnavailable):
        problems.append(progress.work.reason)
    receipts = tuple(result for result in snapshot.changes if result.change.task_id == task_id and result.change.version == version)
    distinct_problems: dict[str, None] = dict.fromkeys(problems)
    return MeetingCanvas(
        reference, followup.title, followup.sources.fingerprint, tuple(cards), readiness, tuple(distinct_problems), receipts,
    )


def change_of(snapshot: MeetingSnapshot, payload: object) -> DraftChange | ActionChange:
    """Decode one identified save; only the workflow may apply the returned change.

    JSON fields: kind (draft/action), task_id, version, draft_id/action_id,
    expected_revision, attempt_id, content. Content uses draft_data/action_data.
    """
    if not isinstance(payload, dict):
        raise ValueError("A meeting canvas save must be an object.")
    data = cast(dict[str, object], payload)
    kind = data.get("kind")
    if kind not in ("draft", "action"):
        raise ValueError("A meeting canvas save must identify a draft or action.")
    item_field = "draft_id" if kind == "draft" else "action_id"
    if set(data) != {"kind", "task_id", "version", item_field, "expected_revision", "attempt_id", "content"}:
        raise ValueError("The meeting canvas save has missing or unexpected fields.")
    reference = PackageRef(identifier(data["task_id"], "meeting canvas task"), digest(data["version"], "meeting canvas version"))
    progress, package = _selected(snapshot, reference)
    if isinstance(progress.packages, PackagesUnavailable):
        raise ValueError("The meeting package could not be confirmed. Refresh it before saving edits.")
    item_id = identifier(data[item_field], "meeting canvas artifact")
    revision = integer(data["expected_revision"], "expected meeting revision", maximum=MAX_EDIT_REVISIONS)
    attempt = identifier(data["attempt_id"], "meeting canvas save attempt")
    current: DraftRevision | ActionRevision | None
    change: DraftChange | ActionChange
    if kind == "draft":
        current = next((value for value in package.drafts if value.id == item_id), None)
        if current is None:
            raise ValueError("That draft does not belong to this meeting package.")
        change = DraftChange(reference.task_id, reference.version, item_id, draft_of(data["content"]), revision, attempt)
    else:
        current = next((value for value in package.actions if value.id == item_id), None)
        if current is None:
            raise ValueError("That action does not belong to this meeting package.")
        change = ActionChange(reference.task_id, reference.version, item_id, action_of(data["content"]), revision, attempt)
    if revision > current.revision:
        raise ValueError("The expected meeting revision is newer than the observed saved item.")
    if revision < current.revision and change.content != current.content:
        raise ValueError("This meeting item changed after editing began. Reload before saving different content.")
    key = _change_reference(change)
    if any(result.change.attempt_id == attempt and _change_reference(result.change) == key for result in snapshot.changes):
        raise ValueError("This save attempt already has an observed receipt. Use a new attempt identity.")
    return change
