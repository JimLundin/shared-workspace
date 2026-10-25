"""Evidence-bound meeting follow-through values and model-response validation.

This module performs no file or network operations. A quote resolves against an
observed transcript's exact text; generation never supplies its own evidence
offsets. Publication and editable draft revisions belong to meeting_followup_store.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path, PureWindowsPath
from typing import Literal, assert_never, cast

from automation.task import digest, identifier, integer, text, timestamp, variant

from .llm import Json

RECIPE_VERSION = 1
MAX_TRANSCRIPT_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_SOURCES = 8
MAX_MODEL_BYTES = 512 * 1024
MAX_CITATIONS = 8
MAX_QUOTE_OCCURRENCES = 1024
MAX_ACTIONS = 100
MAX_DRAFTS = 24
MAX_BODY = 32_000
_EMAIL_TOKEN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


def _text(value: object, field: str, maximum: int, *, empty: bool = False) -> str:
    result = text(value, field, maximum=maximum, empty=empty)
    if any(ord(char) < 32 and char not in "\t\n\r" for char in result):
        raise ValueError(f"Invalid control character in {field}.")
    result.encode("utf-8")
    return result


def _header(value: object, field: str, maximum: int, *, empty: bool = False) -> str:
    result = _text(value, field, maximum, empty=empty)
    if any(char in result for char in "\r\n\t"):
        raise ValueError(f"{field} must occupy one line.")
    return result


def _literal(value: str, quote: str) -> bool:
    before = r"(?<!\w)" if value[0].isalnum() or value[0] == "_" else ""
    after = r"(?!\w)" if value[-1].isalnum() or value[-1] == "_" else ""
    return re.search(before + re.escape(value) + after, quote) is not None


def _email_literal(value: str, quote: str) -> bool:
    return any(value == match.group().strip("'\"`") for match in _EMAIL_TOKEN.finditer(quote))


def _tuple(value: object, kind: type[object], field: str, maximum: int, *, empty: bool = True) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"Invalid {field}.")
    items = cast(tuple[object, ...], value)
    if len(items) > maximum or (not empty and not items) or any(not isinstance(item, kind) for item in items):
        raise ValueError(f"Invalid {field}.")


class NoCaptureNotice(Enum):
    NONE = auto()


@dataclass(frozen=True, slots=True)
class CaptureNotice:
    text: str

    def __post_init__(self) -> None:
        _text(self.text, "capture notice", 4000)


@dataclass(frozen=True, slots=True)
class TranscriptSource:
    id: str
    title: str
    path: str
    sha256: str
    started_at: float
    duration_seconds: float
    capture: Literal["both", "microphone"]
    notice: CaptureNotice | NoCaptureNotice

    def __post_init__(self) -> None:
        identifier(self.id, "transcript identity")
        _header(self.title, "transcript title", 240)
        _text(self.path, "transcript path", 4096)
        if not (Path(self.path).is_absolute() or PureWindowsPath(self.path).is_absolute()):
            raise ValueError("A transcript source path must be absolute.")
        digest(self.sha256, "transcript digest")
        observed_at = timestamp(self.started_at, "recording start")
        if observed_at > 253_402_300_799:
            raise ValueError("Recording timestamps must be before year 10000.")
        object.__setattr__(self, "started_at", observed_at)
        object.__setattr__(self, "duration_seconds", timestamp(self.duration_seconds, "recording duration"))
        if self.capture not in ("both", "microphone"):
            raise ValueError("Invalid transcript capture observation.")
        variant(self.notice, (CaptureNotice, NoCaptureNotice), "capture notice")


@dataclass(frozen=True, slots=True)
class Transcript:
    source: TranscriptSource
    content: bytes
    text: str = field(init=False)

    def __post_init__(self) -> None:
        variant(self.source, TranscriptSource, "transcript source")
        variant(self.content, bytes, "transcript bytes")
        if not self.content or len(self.content) > MAX_TRANSCRIPT_BYTES:
            raise ValueError("The transcript exceeds its supported size or is empty.")
        observed = _text(self.content.decode("utf-8"), "transcript text", MAX_TRANSCRIPT_BYTES)
        object.__setattr__(self, "text", observed)
        if hashlib.sha256(self.content).hexdigest() != self.source.sha256:
            raise ValueError("The transcript bytes do not match their source digest.")


@dataclass(frozen=True, slots=True)
class TranscriptBundle:
    transcripts: tuple[Transcript, ...]

    def __post_init__(self) -> None:
        _tuple(self.transcripts, Transcript, "selected transcripts", MAX_SOURCES, empty=False)
        identities = tuple(item.source.id for item in self.transcripts)
        if len(set(identities)) != len(identities):
            raise ValueError("A selected transcript identity occurs more than once.")
        if sum(len(item.content) for item in self.transcripts) > MAX_BUNDLE_BYTES:
            raise ValueError("The selected transcripts exceed their combined size limit.")

    @property
    def fingerprint(self) -> str:
        # Selection order is intentional: successive recordings retain their order.
        selected = tuple((item.source.id, item.source.sha256) for item in self.transcripts)
        encoded = json.dumps(selected, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Citation:
    source_id: str
    source_sha256: str
    start: int
    end: int
    line: int
    quote: str

    def __post_init__(self) -> None:
        identifier(self.source_id, "citation source")
        digest(self.source_sha256, "citation source digest")
        integer(self.start, "citation start", maximum=MAX_TRANSCRIPT_BYTES)
        integer(self.end, "citation end", minimum=self.start + 1, maximum=MAX_TRANSCRIPT_BYTES)
        integer(self.line, "citation line", minimum=1, maximum=MAX_TRANSCRIPT_BYTES)
        _text(self.quote, "source quote", 4000)
        if self.end - self.start != len(self.quote):
            raise ValueError("Citation offsets must span the quoted text.")


def resolve_citation(bundle: TranscriptBundle, source_id: str, quote: str, *, occurrence: int | None = None) -> Citation:
    """Resolve a literal source quote; repeated text needs an explicit occurrence."""
    identifier(source_id, "citation source")
    _text(quote, "source quote", 4000)
    if occurrence is not None:
        integer(occurrence, "quote occurrence", maximum=MAX_QUOTE_OCCURRENCES - 1)
    transcript = next((item for item in bundle.transcripts if item.source.id == source_id), None)
    if transcript is None:
        raise ValueError("A citation refers to a transcript outside the selected sources.")
    content = transcript.text
    start = content.find(quote)
    if start < 0:
        raise ValueError("A citation quote does not occur in its observed transcript.")
    if occurrence is None:
        if content.find(quote, start + 1) >= 0:
            raise ValueError("An ambiguous source quote requires its zero-based occurrence.")
    else:
        for _ in range(occurrence):
            start = content.find(quote, start + 1)
            if start < 0:
                raise ValueError("The requested quote occurrence does not exist.")
    return Citation(source_id, transcript.source.sha256, start, start + len(quote), content.count("\n", 0, start) + 1, quote)


def _citations(value: object, *, empty: bool = False) -> None:
    _tuple(value, Citation, "source citations", MAX_CITATIONS, empty=empty)


@dataclass(frozen=True, slots=True)
class CitedText:
    text: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        _text(self.text, "meeting statement", 4000)
        _citations(self.citations)


class Unspecified(Enum):
    NOT_STATED = auto()


@dataclass(frozen=True, slots=True)
class NamedOwner:
    name: str
    evidence: Citation

    def __post_init__(self) -> None:
        _header(self.name, "action owner", 240)
        variant(self.evidence, Citation, "owner evidence")
        if not _literal(self.name, self.evidence.quote):
            raise ValueError("A named owner must occur literally in its source evidence.")


@dataclass(frozen=True, slots=True)
class StatedDue:
    text: str
    evidence: Citation

    def __post_init__(self) -> None:
        _header(self.text, "action due text", 240)
        variant(self.evidence, Citation, "due-date evidence")
        if not _literal(self.text, self.evidence.quote):
            raise ValueError("A due date must preserve the literal source wording.")


type Owner = NamedOwner | Unspecified
type Due = StatedDue | Unspecified


@dataclass(frozen=True, slots=True)
class ActionItem:
    id: str
    text: str
    citations: tuple[Citation, ...]
    owner: Owner
    due: Due

    def __post_init__(self) -> None:
        identifier(self.id, "action identity")
        _text(self.text, "action text", 4000)
        _citations(self.citations)
        variant(self.owner, (NamedOwner, Unspecified), "action owner")
        variant(self.due, (StatedDue, Unspecified), "action due date")
        if isinstance(self.owner, NamedOwner) and self.owner.evidence not in self.citations:
            raise ValueError("An owner must cite evidence attached to that action.")
        if isinstance(self.due, StatedDue) and self.due.evidence not in self.citations:
            raise ValueError("A due date must cite evidence attached to that action.")

    @property
    def content(self) -> ActionContent:
        owner = AssignedOwner(self.owner.name) if isinstance(self.owner, NamedOwner) else Unspecified.NOT_STATED
        due = AssignedDue(self.due.text) if isinstance(self.due, StatedDue) else Unspecified.NOT_STATED
        return ActionContent(self.text, owner, due, ActionStatus.OPEN)


class ActionStatus(Enum):
    OPEN = auto()
    DONE = auto()


@dataclass(frozen=True, slots=True)
class AssignedOwner:
    name: str

    def __post_init__(self) -> None:
        _header(self.name, "assigned owner", 240)


@dataclass(frozen=True, slots=True)
class AssignedDue:
    text: str

    def __post_init__(self) -> None:
        _header(self.text, "assigned due text", 240)


@dataclass(frozen=True, slots=True)
class ActionContent:
    text: str
    owner: AssignedOwner | Unspecified
    due: AssignedDue | Unspecified
    status: ActionStatus

    def __post_init__(self) -> None:
        _text(self.text, "edited action text", 4000)
        variant(self.owner, (AssignedOwner, Unspecified), "assigned owner")
        variant(self.due, (AssignedDue, Unspecified), "assigned due date")
        variant(self.status, ActionStatus, "action completion state")


@dataclass(frozen=True, slots=True)
class EmailAddress:
    value: str

    def __post_init__(self) -> None:
        _header(self.value, "email address", 320)
        if re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", self.value) is None:
            raise ValueError("The email address is incomplete or unsupported.")
        local, domain = self.value.split("@")
        if local.startswith(".") or local.endswith(".") or ".." in local or ".." in domain or len(local) > 64:
            raise ValueError("The email address is invalid.")


@dataclass(frozen=True, slots=True)
class Addressee:
    name: str
    email: EmailAddress | Unspecified

    def __post_init__(self) -> None:
        _header(self.name, "recipient name", 240)
        variant(self.email, (EmailAddress, Unspecified), "recipient email")


@dataclass(frozen=True, slots=True)
class SourceRecipient:
    addressee: Addressee
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        variant(self.addressee, Addressee, "generated recipient")
        _citations(self.citations)
        if not any(_literal(self.addressee.name, citation.quote) for citation in self.citations):
            raise ValueError("A generated recipient name must occur in its source evidence.")
        email = self.addressee.email
        if isinstance(email, EmailAddress) and not any(_email_literal(email.value, citation.quote) for citation in self.citations):
            raise ValueError("A generated email address must occur literally in its source evidence.")


@dataclass(frozen=True, slots=True)
class DraftContent:
    subject: str
    body: str
    recipients: tuple[Addressee, ...]

    def __post_init__(self) -> None:
        _header(self.subject, "draft subject", 500)
        _text(self.body, "draft body", MAX_BODY)
        _tuple(self.recipients, Addressee, "draft recipients", 30)


@dataclass(frozen=True, slots=True)
class EmailDraft:
    id: str
    subject: str
    body: str
    recipients: tuple[SourceRecipient, ...]
    citations: tuple[Citation, ...]
    action_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        identifier(self.id, "draft identity")
        _header(self.subject, "draft subject", 500)
        _text(self.body, "draft body", MAX_BODY)
        _tuple(self.recipients, SourceRecipient, "source recipients", 30)
        _citations(self.citations)
        _tuple(self.action_ids, str, "draft action references", MAX_ACTIONS)
        if len(set(self.action_ids)) != len(self.action_ids):
            raise ValueError("A draft repeats an action reference.")
        if any(citation not in self.citations for recipient in self.recipients for citation in recipient.citations):
            raise ValueError("Generated recipients must cite evidence attached to that draft.")

    @property
    def content(self) -> DraftContent:
        return DraftContent(self.subject, self.body, tuple(item.addressee for item in self.recipients))


@dataclass(frozen=True, slots=True)
class Followup:
    sources: TranscriptBundle
    title: str
    notes: tuple[CitedText, ...]
    decisions: tuple[CitedText, ...]
    actions: tuple[ActionItem, ...]
    drafts: tuple[EmailDraft, ...]

    def __post_init__(self) -> None:
        variant(self.sources, TranscriptBundle, "follow-through sources")
        _header(self.title, "follow-through title", 240)
        _tuple(self.notes, CitedText, "meeting notes", 64, empty=False)
        _tuple(self.decisions, CitedText, "meeting decisions", 64)
        _tuple(self.actions, ActionItem, "meeting actions", MAX_ACTIONS)
        _tuple(self.drafts, EmailDraft, "follow-up drafts", MAX_DRAFTS)
        actions = {item.id for item in self.actions}
        if len(actions) != len(self.actions) or len({item.id for item in self.drafts}) != len(self.drafts):
            raise ValueError("Action and draft identities must be unique within their collection.")
        citations: list[Citation] = []
        for statement in (*self.notes, *self.decisions):
            citations.extend(statement.citations)
        for action in self.actions:
            citations.extend(action.citations)
            if isinstance(action.owner, NamedOwner):
                citations.append(action.owner.evidence)
            if isinstance(action.due, StatedDue):
                citations.append(action.due.evidence)
        for draft in self.drafts:
            if not set(draft.action_ids) <= actions:
                raise ValueError("A draft refers to an action outside this follow-through package.")
            citations.extend(draft.citations)
            for recipient in draft.recipients:
                citations.extend(recipient.citations)
        sources = {item.source.id: item for item in self.sources.transcripts}
        for citation in citations:
            transcript = sources.get(citation.source_id)
            if transcript is None or transcript.source.sha256 != citation.source_sha256:
                raise ValueError("A citation is bound to a different transcript observation.")
            content = transcript.text
            if content[citation.start:citation.end] != citation.quote or content.count("\n", 0, citation.start) + 1 != citation.line:
                raise ValueError("A citation does not resolve against the observed source text.")


def _object(value: object, required: set[str], optional: frozenset[str] = frozenset()) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("The follow-through response must use structured JSON objects.")
    result = cast(dict[str, object], value)
    keys = set(result)
    if not required <= keys or keys - required - optional:
        raise ValueError("The follow-through response contains missing or unknown fields.")
    return result


def _array(value: object, field: str, maximum: int, *, empty: bool = True) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"Invalid {field}.")
    result = cast(list[object], value)
    if len(result) > maximum or (not empty and not result):
        raise ValueError(f"Invalid {field} count.")
    return result


def _citation(bundle: TranscriptBundle, value: object) -> Citation:
    data = _object(value, {"source_id", "quote"}, frozenset({"occurrence"}))
    return resolve_citation(
        bundle, identifier(data["source_id"], "citation source"), _text(data["quote"], "source quote", 4000),
        occurrence=integer(data["occurrence"], "quote occurrence", maximum=MAX_QUOTE_OCCURRENCES - 1) if "occurrence" in data else None,
    )


def _evidence(bundle: TranscriptBundle, value: object) -> tuple[Citation, ...]:
    return tuple(_citation(bundle, item) for item in _array(value, "source citations", MAX_CITATIONS, empty=False))


def _statement(bundle: TranscriptBundle, value: object) -> CitedText:
    data = _object(value, {"text", "citations"})
    return CitedText(_text(data["text"], "meeting statement", 4000), _evidence(bundle, data["citations"]))


def _owner(bundle: TranscriptBundle, value: object) -> Owner:
    data = _object(value, {"kind"}, frozenset({"name", "evidence"}))
    if data["kind"] == "unspecified" and len(data) == 1:
        return Unspecified.NOT_STATED
    if data["kind"] == "named" and set(data) == {"kind", "name", "evidence"}:
        return NamedOwner(_header(data["name"], "action owner", 240), _citation(bundle, data["evidence"]))
    raise ValueError("An action owner must be named with evidence or explicitly unspecified.")


def _due(bundle: TranscriptBundle, value: object) -> Due:
    data = _object(value, {"kind"}, frozenset({"text", "evidence"}))
    if data["kind"] == "unspecified" and len(data) == 1:
        return Unspecified.NOT_STATED
    if data["kind"] == "stated" and set(data) == {"kind", "text", "evidence"}:
        return StatedDue(_header(data["text"], "action due text", 240), _citation(bundle, data["evidence"]))
    raise ValueError("A due date must retain source wording with evidence or be explicitly unspecified.")


def _action(bundle: TranscriptBundle, value: object) -> ActionItem:
    data = _object(value, {"id", "text", "citations", "owner", "due"})
    return ActionItem(
        identifier(data["id"], "action identity"), _text(data["text"], "action text", 4000), _evidence(bundle, data["citations"]),
        _owner(bundle, data["owner"]), _due(bundle, data["due"]),
    )


def _recipient(bundle: TranscriptBundle, value: object) -> SourceRecipient:
    data = _object(value, {"name", "email", "citations"})
    email = Unspecified.NOT_STATED if data["email"] is None else EmailAddress(_header(data["email"], "email address", 320))
    return SourceRecipient(Addressee(_header(data["name"], "recipient name", 240), email), _evidence(bundle, data["citations"]))


def _draft(bundle: TranscriptBundle, value: object) -> EmailDraft:
    data = _object(value, {"id", "subject", "body", "recipients", "citations", "action_ids"})
    return EmailDraft(
        identifier(data["id"], "draft identity"), _header(data["subject"], "draft subject", 500), _text(data["body"], "draft body", MAX_BODY),
        tuple(_recipient(bundle, item) for item in _array(data["recipients"], "draft recipients", 30)),
        _evidence(bundle, data["citations"]),
        tuple(identifier(item, "draft action reference") for item in _array(data["action_ids"], "draft action references", MAX_ACTIONS)),
    )


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON fields are not valid follow-through data.")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("Non-finite JSON values are not valid follow-through data.")


def parse_followup(bundle: TranscriptBundle, model_json: str) -> Followup:
    """Validate a complete response without silently repairing claims or evidence."""
    _text(model_json, "model response", MAX_MODEL_BYTES)
    if len(model_json.encode("utf-8")) > MAX_MODEL_BYTES:
        raise ValueError("The model response exceeds its byte limit.")
    try:
        raw: object = json.loads(model_json, object_pairs_hook=_pairs, parse_constant=_constant)
    except (RecursionError, json.JSONDecodeError) as error:
        raise ValueError("The model response is not bounded valid JSON.") from error
    data = _object(raw, {"source_digest", "title", "notes", "decisions", "actions", "drafts"})
    if digest(data["source_digest"], "selected source digest") != bundle.fingerprint:
        raise ValueError("The follow-through response belongs to a different source selection.")
    return Followup(
        bundle, _header(data["title"], "follow-through title", 240),
        tuple(_statement(bundle, item) for item in _array(data["notes"], "meeting notes", 64, empty=False)),
        tuple(_statement(bundle, item) for item in _array(data["decisions"], "meeting decisions", 64)),
        tuple(_action(bundle, item) for item in _array(data["actions"], "meeting actions", MAX_ACTIONS)),
        tuple(_draft(bundle, item) for item in _array(data["drafts"], "follow-up drafts", MAX_DRAFTS)),
    )


def bundle_data(bundle: TranscriptBundle) -> dict[str, Json]:
    """A complete versioned snapshot suitable for the immutable preparation binding."""
    values: list[Json] = []
    for transcript in bundle.transcripts:
        source = transcript.source
        match source.notice:
            case CaptureNotice(text=notice):
                capture_notice: str | None = notice
            case NoCaptureNotice.NONE:
                capture_notice = None
            case _:
                assert_never(source.notice)
        values.append({
            "id": source.id, "title": source.title, "path": source.path, "sha256": source.sha256,
            "started_at": source.started_at, "duration_seconds": source.duration_seconds, "capture": source.capture,
            "notice": capture_notice, "text": transcript.text,
        })
    return {"version": 1, "sources": values}


def bundle_of(value: object) -> TranscriptBundle:
    """Validate a persisted source snapshot, including its exact UTF-8 byte digests."""
    data = _object(value, {"version", "sources"})
    if integer(data["version"], "source snapshot version") != 1:
        raise ValueError("Unsupported source snapshot version.")
    sources: list[Transcript] = []
    for item in _array(data["sources"], "selected transcripts", MAX_SOURCES, empty=False):
        row = _object(item, {"id", "title", "path", "sha256", "started_at", "duration_seconds", "capture", "notice", "text"})
        capture = row["capture"]
        if capture not in ("both", "microphone"):
            raise ValueError("Invalid recording source.")
        notice = NoCaptureNotice.NONE if row["notice"] is None else CaptureNotice(_text(row["notice"], "capture notice", 4000))
        source = TranscriptSource(
            identifier(row["id"], "transcript identity"), _header(row["title"], "transcript title", 240),
            _text(row["path"], "transcript path", 4096), digest(row["sha256"], "transcript digest"),
            timestamp(row["started_at"], "recording start"), timestamp(row["duration_seconds"], "recording duration"),
            "both" if capture == "both" else "microphone", notice,
        )
        content = _text(row["text"], "source snapshot text", MAX_TRANSCRIPT_BYTES).encode("utf-8")
        sources.append(Transcript(source, content))
    return TranscriptBundle(tuple(sources))


def _citation_data(bundle: TranscriptBundle, citation: Citation) -> dict[str, Json]:
    data: dict[str, Json] = {"source_id": citation.source_id, "quote": citation.quote}
    content = next(item.text for item in bundle.transcripts if item.source.id == citation.source_id)
    start = content.find(citation.quote)
    if content.find(citation.quote, start + 1) >= 0:
        occurrence = 0
        while start != citation.start:
            if occurrence >= MAX_QUOTE_OCCURRENCES - 1:
                raise ValueError("A highly repeated citation needs a longer source quote.")
            start = content.find(citation.quote, start + 1)
            if start < 0:
                raise ValueError("A citation does not resolve against its source.")
            occurrence += 1
        data["occurrence"] = occurrence
    return data


def followup_data(followup: Followup) -> dict[str, Json]:
    """Canonical validated model data; source bytes are stored separately."""
    bundle = followup.sources

    def evidence(citations: tuple[Citation, ...]) -> list[Json]:
        return [_citation_data(bundle, item) for item in citations]

    def statement(item: CitedText) -> Json:
        return {"text": item.text, "citations": evidence(item.citations)}

    actions: list[Json] = []
    for action in followup.actions:
        owner: Json
        match action.owner:
            case NamedOwner(name=name, evidence=citation):
                owner = {"kind": "named", "name": name, "evidence": _citation_data(bundle, citation)}
            case Unspecified.NOT_STATED:
                owner = {"kind": "unspecified"}
            case _:
                assert_never(action.owner)
        due: Json
        match action.due:
            case StatedDue(text=due_text, evidence=citation):
                due = {"kind": "stated", "text": due_text, "evidence": _citation_data(bundle, citation)}
            case Unspecified.NOT_STATED:
                due = {"kind": "unspecified"}
            case _:
                assert_never(action.due)
        actions.append({"id": action.id, "text": action.text, "citations": evidence(action.citations), "owner": owner, "due": due})
    drafts: list[Json] = []
    for draft in followup.drafts:
        recipients: list[Json] = []
        for recipient in draft.recipients:
            email = recipient.addressee.email
            recipients.append({
                "name": recipient.addressee.name, "email": email.value if isinstance(email, EmailAddress) else None,
                "citations": evidence(recipient.citations),
            })
        drafts.append({
            "id": draft.id, "subject": draft.subject, "body": draft.body, "recipients": recipients,
            "citations": evidence(draft.citations), "action_ids": list(draft.action_ids),
        })
    return {
        "source_digest": bundle.fingerprint, "title": followup.title, "notes": [statement(item) for item in followup.notes],
        "decisions": [statement(item) for item in followup.decisions], "actions": actions, "drafts": drafts,
    }


def draft_data(content: DraftContent) -> dict[str, Json]:
    recipients: list[Json] = [
        {"name": recipient.name, "email": recipient.email.value if isinstance(recipient.email, EmailAddress) else None}
        for recipient in content.recipients
    ]
    return {"subject": content.subject, "body": content.body, "recipients": recipients}


def draft_of(value: object) -> DraftContent:
    data = _object(value, {"subject", "body", "recipients"})
    recipients: list[Addressee] = []
    for item in _array(data["recipients"], "draft recipients", 30):
        row = _object(item, {"name", "email"})
        email = Unspecified.NOT_STATED if row["email"] is None else EmailAddress(_header(row["email"], "email address", 320))
        recipients.append(Addressee(_header(row["name"], "recipient name", 240), email))
    return DraftContent(_header(data["subject"], "draft subject", 500), _text(data["body"], "draft body", MAX_BODY), tuple(recipients))


def action_data(content: ActionContent) -> dict[str, Json]:
    return {
        "text": content.text, "owner": content.owner.name if isinstance(content.owner, AssignedOwner) else None,
        "due": content.due.text if isinstance(content.due, AssignedDue) else None,
        "status": "done" if content.status is ActionStatus.DONE else "open",
    }


def action_of(value: object) -> ActionContent:
    data = _object(value, {"text", "owner", "due", "status"})
    owner = Unspecified.NOT_STATED if data["owner"] is None else AssignedOwner(_header(data["owner"], "assigned owner", 240))
    due = Unspecified.NOT_STATED if data["due"] is None else AssignedDue(_header(data["due"], "assigned due text", 240))
    if data["status"] not in ("open", "done"):
        raise ValueError("Invalid action completion state.")
    return ActionContent(_text(data["text"], "edited action text", 4000), owner, due,
                         ActionStatus.DONE if data["status"] == "done" else ActionStatus.OPEN)
