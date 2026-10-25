"""Observe meeting publications and own atomic follow-through packages and edits."""

import contextlib
import csv
import hashlib
import io
import json
import ntpath
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import EmailPolicy
from email.utils import formataddr
from enum import Enum, auto
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Protocol, assert_never, cast
from urllib.parse import quote

from automation.files import filesystem_path
from automation.task import FileDigest, digest, identifier, integer, text, timestamp, variant

from .llm import RATE, Json
from .meeting_followup import (
    MAX_MODEL_BYTES,
    MAX_TRANSCRIPT_BYTES,
    RECIPE_VERSION,
    ActionContent,
    ActionStatus,
    AssignedDue,
    AssignedOwner,
    CaptureNotice,
    Citation,
    DraftContent,
    EmailAddress,
    Followup,
    NamedOwner,
    NoCaptureNotice,
    StatedDue,
    Transcript,
    TranscriptSource,
    Unspecified,
    action_data,
    action_of,
    bundle_data,
    bundle_of,
    draft_data,
    draft_of,
    followup_data,
    parse_followup,
)
from .meeting_store import MeetingStore
from .recording_state import source_name

MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_VERSIONS = 128
MAX_EDIT_REVISIONS = 10_000


class TranscriptNotReadyError(ValueError):
    """The selected recording has no complete current transcript publication."""


class SourceChangedError(ValueError):
    """A selected transcript changed after the generation input was observed."""


class ArtifactChangedError(ValueError):
    """A previously published artifact differs; preserve it instead of replacing it."""


class EditConflictError(ValueError):
    """An edit began from a revision older than the currently saved user changes."""


def _read_file(path: Path, maximum: int) -> bytes:
    native = filesystem_path(path)
    if not stat.S_ISREG(native.lstat().st_mode):
        raise ValueError("An artifact must be a regular file, without a symbolic link.")
    with native.open("rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("The file is not regular or exceeds its supported size.")
        content = source.read(maximum + 1)
        after = os.fstat(source.fileno())
    if len(content) > maximum:
        raise ValueError("The file exceeds its supported size.")
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise SourceChangedError("The file changed while its contents were being observed.")
    return content


def observe_transcript(store: MeetingStore) -> Transcript:
    """Read published bytes, including manual corrections, without refreshing export."""
    before = store.state()
    if not before.finished or before.pending or before.revision != before.exported_revision:
        raise TranscriptNotReadyError("This recording's transcription and current Markdown export must finish first.")
    try:
        if not stat.S_ISREG(filesystem_path(store.transcript).lstat().st_mode):
            raise TranscriptNotReadyError("The completed transcript must be a regular published file.")
        content = _read_file(store.transcript, MAX_TRANSCRIPT_BYTES)
    except FileNotFoundError as error:
        raise TranscriptNotReadyError("The published transcript file is missing.") from error
    if store.state() != before:
        raise SourceChangedError("The recording changed while its transcript was being observed.")
    source = TranscriptSource(
        store.folder.name, before.title, str(store.transcript.absolute()), hashlib.sha256(content).hexdigest(),
        before.started, before.frames / RATE, before.source,
        CaptureNotice(before.problem) if before.problem else NoCaptureNotice.NONE,
    )
    return Transcript(source, content)


def _component(value: str, field: str) -> str:
    identifier(value, field)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None or value.endswith("."):
        raise ValueError(f"Invalid {field} for an artifact directory.")
    if ntpath.isreserved(value):
        raise ValueError(f"Reserved {field} for an artifact directory.")
    return value


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or value == "." or "\\" in value or path.is_absolute() or str(path) != value
            or any(part in (".", "..") or ntpath.isreserved(part) for part in path.parts)):
        raise ValueError("Artifact paths must be relative and cannot traverse directories.")
    return value


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>()#!|])", r"\\\1", value)


def _source_name(index: int) -> str:
    return f"sources/{index:03d}.md"


def _draft_name(index: int, extension: str) -> str:
    return f"drafts/{index:03d}.{extension}"


def _link(source: TranscriptSource) -> str:
    if PureWindowsPath(source.path).is_absolute():
        path = PureWindowsPath(source.path).as_posix()
        return ("file:" if path.startswith("//") else "file:///") + quote(path, safe="/:")
    return Path(source.path).as_uri()


def _citations(followup: Followup, citations: tuple[Citation, ...]) -> str:
    sources = {item.source.id: index for index, item in enumerate(followup.sources.transcripts, 1)}
    return "\n".join(
        f"> [Recording {sources[item.source_id]}, line {item.line}](<{_source_name(sources[item.source_id])}>): {_markdown(item.quote)}"
        for item in citations
    )


def _owner(value: NamedOwner | AssignedOwner | Unspecified) -> str:
    match value:
        case NamedOwner(name=name) | AssignedOwner(name=name):
            return name
        case Unspecified.NOT_STATED:
            return "Not stated"
        case _:
            assert_never(value)


def _due(value: StatedDue | AssignedDue | Unspecified) -> str:
    match value:
        case StatedDue(text=due) | AssignedDue(text=due):
            return due
        case Unspecified.NOT_STATED:
            return "Not stated"
        case _:
            assert_never(value)


def _csv(rows: tuple[tuple[str, ...], ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for row in rows:
        # Spreadsheet consumers must treat observed/model/user text as text.
        values = tuple("'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in row)
        writer.writerow(values)
    return output.getvalue().encode("utf-8-sig")


def _email(content: DraftContent) -> bytes:
    policy: EmailPolicy[EmailMessage[object, str]] = EmailPolicy(linesep="\r\n")
    message: EmailMessage[object, str] = EmailMessage(policy=policy)
    message["X-Unsent"] = "1"
    message["Subject"] = content.subject
    addresses = [formataddr((recipient.name, recipient.email.value), charset="utf-8")
                 for recipient in content.recipients if isinstance(recipient.email, EmailAddress)]
    if addresses:
        message["To"] = ", ".join(addresses)
    message.set_content(content.body)
    return message.as_bytes()


def _draft_markdown(content: DraftContent) -> bytes:
    recipients = "\n".join(
        "- " + _markdown(recipient.name) + " — " +
        (_markdown(recipient.email.value) if isinstance(recipient.email, EmailAddress) else "Email not stated")
        for recipient in content.recipients
    ) or "Recipients not stated."
    return (f"# {_markdown(content.subject)}\n\n## Recipients\n\n{recipients}\n\n## Message\n\n{content.body}\n").encode()


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    content: bytes

    def __post_init__(self) -> None:
        _relative(self.name)
        variant(self.content, bytes, "artifact bytes")
        if len(self.content) > MAX_PACKAGE_BYTES:
            raise ValueError("Invalid bounded artifact content.")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _artifact_name(artifact: Artifact) -> str:
    return artifact.name


def _render(task_id: str, followup: Followup) -> tuple[Artifact, ...]:
    notes = [f"# {_markdown(followup.title)}", "## Source recordings"]
    artifacts: list[Artifact] = []
    for index, transcript in enumerate(followup.sources.transcripts, 1):
        source = transcript.source
        captured = datetime.fromtimestamp(source.started_at, UTC).isoformat(timespec="seconds")
        notes.append(
            f"### {index}. {_markdown(source.title)}\n\n{captured} · {source_name(source.capture)} · {source.duration_seconds:g} seconds\n\n"
            f"[Observed transcript](<{_source_name(index)}>) · [Original file](<{_link(source)}>)"
        )
        if isinstance(source.notice, CaptureNotice):
            notes.append("Capture notice: " + _markdown(source.notice.text))
        artifacts.append(Artifact(_source_name(index), transcript.content))
    for heading, statements in (("Notes", followup.notes), ("Decisions", followup.decisions)):
        notes.append("## " + heading)
        if not statements:
            notes.append("None stated in the selected source material.")
        for statement in statements:
            notes.append(_markdown(statement.text) + "\n\n" + _citations(followup, statement.citations))
    notes.append("## Actions")
    rows: list[tuple[str, ...]] = [("id", "action", "owner", "due", "status", "source evidence")]
    if not followup.actions:
        notes.append("No action was identified in the selected source material.")
    for action in followup.actions:
        notes.append(
            f"- **{_markdown(action.id)}:** {_markdown(action.text)}\n\n"
            f"  Owner: {_markdown(_owner(action.owner))} · Due: {_markdown(_due(action.due))} · Open\n\n"
            + _citations(followup, action.citations)
        )
        citations = "\n".join(f"{item.source_id}, line {item.line}: {item.quote}" for item in action.citations)
        rows.append((action.id, action.text, _owner(action.owner), _due(action.due), "open", citations))
    notes.append("## Follow-up drafts")
    if not followup.drafts:
        notes.append("No follow-up draft was proposed.")
    for index, draft in enumerate(followup.drafts, 1):
        notes.append(
            f"- [{_markdown(draft.subject)}](<{_draft_name(index, 'md')}>) · [Email draft](<{_draft_name(index, 'eml')}>)\n\n"
            + _citations(followup, draft.citations)
        )
        artifacts.append(Artifact(_draft_name(index, "eml"), _email(draft.content)))
        artifacts.append(Artifact(_draft_name(index, "md"), _draft_markdown(draft.content)))
    artifacts.extend((Artifact("notes.md", ("\n\n".join(notes) + "\n").encode("utf-8")), Artifact("actions.csv", _csv(tuple(rows)))))
    manifest: dict[str, Json] = {"format": 1, "recipe": RECIPE_VERSION, "task_id": task_id,
                                 "sources": bundle_data(followup.sources), "followup": followup_data(followup)}
    artifacts.append(Artifact("manifest.json", _json(manifest)))
    ordered = sorted(artifacts, key=_artifact_name)
    return tuple(ordered)


def _archive(artifacts: tuple[Artifact, ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for artifact in artifacts:
            entry = zipfile.ZipInfo(artifact.name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 0
            entry.external_attr = 0o600 << 16
            archive.writestr(entry, artifact.content)
    result = output.getvalue()
    if len(result) > MAX_PACKAGE_BYTES:
        raise ValueError("The complete follow-through package exceeds its size limit.")
    return result


@dataclass(frozen=True, slots=True)
class Publication:
    task_id: str
    version: str
    directory: Path
    followup: Followup
    artifacts: tuple[Artifact, ...]

    @property
    def verification(self) -> FileDigest:
        return FileDigest(str(self.directory / "package.zip"), self.version)


@dataclass(frozen=True, slots=True)
class DraftFiles:
    id: str
    eml: Path
    markdown: Path


@dataclass(frozen=True, slots=True)
class PublishedFollowup:
    task_id: str
    version: str
    directory: Path
    followup: Followup
    drafts: tuple[DraftFiles, ...]

    @property
    def notes_path(self) -> Path:
        return self.directory / "notes.md"

    @property
    def actions_path(self) -> Path:
        return self.directory / "actions.csv"

    @property
    def verification(self) -> FileDigest:
        return FileDigest(str(self.directory / "package.zip"), self.version)


class Original(Enum):
    GENERATED = auto()


@dataclass(frozen=True, slots=True)
class Edited:
    revision: int
    at: float

    def __post_init__(self) -> None:
        integer(self.revision, "edit revision", minimum=1, maximum=MAX_EDIT_REVISIONS)
        timestamp(self.at, "edit time")


@dataclass(frozen=True, slots=True)
class DraftRevision:
    id: str
    content: DraftContent
    origin: Original | Edited
    files: DraftFiles

    @property
    def revision(self) -> int:
        return self.origin.revision if isinstance(self.origin, Edited) else 0


@dataclass(frozen=True, slots=True)
class ActionRevision:
    id: str
    content: ActionContent
    origin: Original | Edited
    path: Path

    @property
    def revision(self) -> int:
        return self.origin.revision if isinstance(self.origin, Edited) else 0


class _Cursor(Protocol):
    def fetchone(self) -> tuple[object, ...] | None: ...


class _Connection(Protocol):
    def execute(self, sql: str, parameters: Sequence[object] = ()) -> _Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


def _install(source: Path, destination: Path) -> None:
    filesystem_path(source).rename(filesystem_path(destination))


class FollowupStore:
    __slots__ = ("install", "root")

    def __init__(self, root: Path, *, install: Callable[[Path, Path], None] = _install) -> None:
        self.root = root.absolute()
        self.install = install

    def _task(self, task_id: str) -> Path:
        return self.root / _component(task_id, "task identity")

    def _directory(self, task_id: str, version: str) -> Path:
        return self._task(task_id) / "versions" / digest(version, "package version")

    def plan(self, task_id: str, followup: Followup) -> Publication:
        """Pure publication bytes and digest, prepared before the durable action gate."""
        self._task(task_id)
        artifacts = _render(task_id, followup)
        package = Artifact("package.zip", _archive(artifacts))
        return Publication(task_id, package.sha256, self._directory(task_id, package.sha256), followup, (*artifacts, package))

    def _safe(self, destination: Path) -> None:
        if not destination.is_relative_to(self.root):
            raise ValueError("The artifact destination is outside its configured store.")
        path = self.root
        if filesystem_path(path).is_symlink():
            raise ValueError("The artifact root cannot be a symbolic link.")
        for part in destination.relative_to(self.root).parts:
            path /= part
            if filesystem_path(path).is_symlink():
                raise ValueError("Artifact directories cannot contain symbolic links.")

    def _verify(self, directory: Path, artifacts: tuple[Artifact, ...]) -> None:
        for artifact in artifacts:
            path = directory / artifact.name
            self._safe(path)
            try:
                observed = _read_file(path, len(artifact.content))
            except (OSError, ValueError) as error:
                raise ArtifactChangedError("A saved artifact is missing or changed; its existing content was preserved.") from error
            if observed != artifact.content:
                raise ArtifactChangedError("A saved artifact differs from its generated version; its changes were preserved.")

    def _publish(self, destination: Path, artifacts: tuple[Artifact, ...]) -> None:
        self._safe(destination)
        if filesystem_path(destination).exists():
            self._verify(destination, artifacts)
            return
        parent = filesystem_path(destination.parent)
        parent.mkdir(parents=True, exist_ok=True)
        # Keep the public/injected directory identity canonical after native creation.
        temporary = destination.parent / Path(tempfile.mkdtemp(prefix=".followup-", dir=parent)).name
        try:
            for artifact in artifacts:
                target = temporary / artifact.name
                filesystem_path(target.parent).mkdir(parents=True, exist_ok=True)
                with filesystem_path(target).open("xb") as output:
                    output.write(artifact.content)
                    output.flush()
                    os.fsync(output.fileno())
            self._verify(temporary, artifacts)
            # Native readers can briefly deny directory renames. Retain the same
            # complete staging directory; never replace a competing publication.
            delays = (.01, .03, .06, .12, .24)
            for attempt in range(len(delays) + 1):
                try:
                    self.install(temporary, destination)
                    break
                except OSError as error:
                    if filesystem_path(destination).is_dir():
                        self._verify(destination, artifacts)
                        break
                    windows_error: object = getattr(error, "winerror", None)
                    if os.name != "nt" or windows_error not in (5, 32, 33) or attempt == len(delays):
                        raise
                    time.sleep(delays[attempt])
            self._verify(destination, artifacts)
        finally:
            native_temporary = filesystem_path(temporary)
            if native_temporary.exists():
                shutil.rmtree(native_temporary)

    def publish(self, publication: Publication) -> PublishedFollowup:
        """Atomically publish complete verified files, retaining all existing versions."""
        expected = self.plan(publication.task_id, publication.followup)
        if publication != expected:
            raise ValueError("The publication differs from this store's deterministic plan.")
        for selected in publication.followup.sources.transcripts:
            current = observe_transcript(MeetingStore(Path(selected.source.path).parent))
            if current != selected:
                raise SourceChangedError("A selected transcript changed; prepare a new follow-through package from its current contents.")
        self._publish(publication.directory, publication.artifacts)
        return self.read(publication.task_id, publication.version)

    def read(self, task_id: str, version: str) -> PublishedFollowup:
        directory = self._directory(task_id, version)
        self._safe(directory / "package.zip")
        content = _read_file(directory / "package.zip", MAX_PACKAGE_BYTES)
        if hashlib.sha256(content).hexdigest() != version:
            raise ArtifactChangedError("The saved package no longer matches its content identity.")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if len(names) > 100 or len(set(names)) != len(names):
                raise ValueError("The package has duplicated entries or too many artifacts.")
            size = 0
            for entry in archive.infolist():
                _relative(entry.filename)
                if entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 1:
                    raise ValueError("The package has unsupported encoded artifacts.")
                size += entry.file_size
            if size > MAX_PACKAGE_BYTES:
                raise ValueError("The package exceeds its supported artifact size.")
            manifest: object = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("Invalid follow-through package manifest.")
        data = cast(dict[str, object], manifest)
        if set(data) != {"format", "recipe", "task_id", "sources", "followup"} or data["format"] != 1 or data["recipe"] != RECIPE_VERSION:
            raise ValueError("Unsupported follow-through package format.")
        if data["task_id"] != task_id:
            raise ValueError("The package belongs to another task.")
        bundle = bundle_of(data["sources"])
        encoded = _json(data["followup"])
        if len(encoded) > MAX_MODEL_BYTES:
            raise ValueError("The saved follow-through data exceeds its supported size.")
        followup = parse_followup(bundle, encoded.decode("utf-8"))
        expected = self.plan(task_id, followup)
        if expected.version != version:
            raise ArtifactChangedError("The package contents differ from its validated artifacts.")
        self._verify(directory, expected.artifacts)
        drafts = tuple(DraftFiles(draft.id, directory / _draft_name(index, "eml"), directory / _draft_name(index, "md"))
                       for index, draft in enumerate(followup.drafts, 1))
        return PublishedFollowup(task_id, version, directory, followup, drafts)

    def versions(self, task_id: str) -> tuple[PublishedFollowup, ...]:
        directory = self._task(task_id) / "versions"
        self._safe(directory)
        native = filesystem_path(directory)
        if not native.exists():
            return ()
        paths: list[Path] = []
        for path in native.iterdir():
            if path.name.startswith("."):
                continue
            if len(paths) >= MAX_VERSIONS:
                raise ValueError("The task has too many follow-through versions.")
            digest(path.name, "saved package version")
            paths.append(path)
        return tuple(self.read(task_id, path.name) for path in sorted(paths))

    @contextlib.contextmanager
    def _connection(self, task_id: str, *, writing: bool = False) -> Generator[_Connection]:
        task = self._task(task_id)
        self._safe(task / "edits.sqlite3")
        filesystem_path(task).mkdir(parents=True, exist_ok=True)
        connection = cast(_Connection, sqlite3.connect(filesystem_path(task / "edits.sqlite3"), timeout=5, isolation_level=None))
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS edits (version TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, "
                               "revision INTEGER NOT NULL, at REAL NOT NULL, content TEXT NOT NULL, digest TEXT NOT NULL, "
                               "PRIMARY KEY (version,kind,id))")
            connection.execute("CREATE TABLE IF NOT EXISTS edit_history (version TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, "
                               "revision INTEGER NOT NULL, at REAL NOT NULL, content TEXT NOT NULL, digest TEXT NOT NULL, "
                               "PRIMARY KEY (version,kind,id,revision))")
            if writing:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if writing:
                connection.commit()
        except BaseException:
            if writing:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _edit_directory(self, task_id: str, version: str, kind: str, item_id: str, *, revision: int, fingerprint: str) -> Path:
        # One content-derived directory keeps native Windows draft paths short.
        key = _json({"version": version, "kind": kind, "id": item_id, "revision": revision, "digest": fingerprint})
        return self._task(task_id) / "edits" / hashlib.sha256(key).hexdigest()

    def _edit(
        self, task_id: str, version: str, kind: Literal["draft", "action"], item_id: str, *, revision: int | None = None,
    ) -> tuple[Edited, object, Path] | None:
        with self._connection(task_id) as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT revision,at,content,digest FROM edits WHERE version=? AND kind=? AND id=?", (version, kind, item_id),
                ).fetchone()
                if row is not None:
                    committed = connection.execute(
                        "SELECT revision,at,content,digest FROM edit_history WHERE version=? AND kind=? AND id=? AND revision=?",
                        (version, kind, item_id, row[0]),
                    ).fetchone()
                    if row != committed:
                        raise ValueError("The current edit differs from its committed history.")
            else:
                row = connection.execute(
                    "SELECT revision,at,content,digest FROM edit_history WHERE version=? AND kind=? AND id=? AND revision=?",
                    (version, kind, item_id, revision),
                ).fetchone()
        if row is None:
            return None
        origin = Edited(integer(row[0], "edit revision", minimum=1, maximum=MAX_EDIT_REVISIONS), timestamp(row[1], "edit time"))
        encoded = text(row[2], "saved user edit", maximum=MAX_MODEL_BYTES).encode("utf-8")
        fingerprint = digest(row[3], "saved user edit digest")
        if hashlib.sha256(encoded).hexdigest() != fingerprint:
            raise ValueError("The saved user edit does not match its recorded digest.")
        value: object = json.loads(encoded)
        return origin, value, self._edit_directory(task_id, version, kind, item_id, revision=origin.revision, fingerprint=fingerprint)

    def draft_revision(self, task_id: str, version: str, draft_id: str, revision: int) -> DraftRevision:
        integer(revision, "requested draft revision", maximum=MAX_EDIT_REVISIONS)
        published = self.read(task_id, version)
        draft = next((item for item in published.followup.drafts if item.id == draft_id), None)
        if draft is None:
            raise ValueError("The draft does not belong to this generated package.")
        if revision == 0:
            files = next(item for item in published.drafts if item.id == draft_id)
            return DraftRevision(draft_id, draft.content, Original.GENERATED, files)
        saved = self._edit(task_id, version, "draft", draft_id, revision=revision)
        if saved is None:
            raise ValueError("That draft revision has no committed history record.")
        origin, data, directory = saved
        content = draft_of(data)
        self._verify(directory, (Artifact("draft.eml", _email(content)), Artifact("draft.md", _draft_markdown(content)),
                                 Artifact("content.json", _json(draft_data(content)))))
        return DraftRevision(draft_id, content, origin, DraftFiles(draft_id, directory / "draft.eml", directory / "draft.md"))

    def action_revision(self, task_id: str, version: str, action_id: str, revision: int) -> ActionRevision:
        integer(revision, "requested action revision", maximum=MAX_EDIT_REVISIONS)
        published = self.read(task_id, version)
        action = next((item for item in published.followup.actions if item.id == action_id), None)
        if action is None:
            raise ValueError("The action does not belong to this generated package.")
        if revision == 0:
            return ActionRevision(action_id, action.content, Original.GENERATED, published.actions_path)
        saved = self._edit(task_id, version, "action", action_id, revision=revision)
        if saved is None:
            raise ValueError("That action revision has no committed history record.")
        origin, data, directory = saved
        content = action_of(data)
        self._verify(directory, self._action_artifacts(action_id, content))
        return ActionRevision(action_id, content, origin, directory / "action.csv")

    def drafts(self, task_id: str, version: str) -> tuple[DraftRevision, ...]:
        published = self.read(task_id, version)
        revisions: list[DraftRevision] = []
        for draft, files in zip(published.followup.drafts, published.drafts, strict=True):
            saved = self._edit(task_id, version, "draft", draft.id)
            if saved is None:
                revisions.append(DraftRevision(draft.id, draft.content, Original.GENERATED, files))
            else:
                origin, data, directory = saved
                content = draft_of(data)
                self._verify(directory, (Artifact("draft.eml", _email(content)), Artifact("draft.md", _draft_markdown(content)),
                                         Artifact("content.json", _json(draft_data(content)))))
                revisions.append(DraftRevision(draft.id, content, origin, DraftFiles(draft.id, directory / "draft.eml", directory / "draft.md")))
        return tuple(revisions)

    def actions(self, task_id: str, version: str) -> tuple[ActionRevision, ...]:
        published = self.read(task_id, version)
        revisions: list[ActionRevision] = []
        for action in published.followup.actions:
            saved = self._edit(task_id, version, "action", action.id)
            if saved is None:
                revisions.append(ActionRevision(action.id, action.content, Original.GENERATED, published.actions_path))
            else:
                origin, data, directory = saved
                content = action_of(data)
                self._verify(directory, self._action_artifacts(action.id, content))
                revisions.append(ActionRevision(action.id, content, origin, directory / "action.csv"))
        return tuple(revisions)

    def _action_artifacts(self, action_id: str, content: ActionContent) -> tuple[Artifact, ...]:
        rows = (("id", "action", "owner", "due", "status"),
                (action_id, content.text, _owner(content.owner), _due(content.due), "done" if content.status is ActionStatus.DONE else "open"))
        return (Artifact("action.csv", _csv(rows)), Artifact("content.json", _json(action_data(content))))

    def _save(
        self, task_id: str, version: str, kind: Literal["draft", "action"], item_id: str, data: dict[str, Json],
        *, artifacts: tuple[Artifact, ...], expected_revision: int, at: float,
    ) -> int:
        integer(expected_revision, "expected edit revision", maximum=MAX_EDIT_REVISIONS)
        timestamp(at, "edit time")
        encoded = _json(data)
        fingerprint = hashlib.sha256(encoded).hexdigest()
        with self._connection(task_id, writing=True) as connection:
            row = connection.execute(
                "SELECT revision,at,content,digest FROM edits WHERE version=? AND kind=? AND id=?", (version, kind, item_id),
            ).fetchone()
            current = integer(row[0], "edit revision", minimum=1, maximum=MAX_EDIT_REVISIONS) if row is not None else 0
            if row is not None:
                committed = connection.execute(
                    "SELECT revision,at,content,digest FROM edit_history WHERE version=? AND kind=? AND id=? AND revision=?",
                    (version, kind, item_id, current),
                ).fetchone()
                if committed != row:
                    raise ValueError("The current edit differs from its committed history.")
                if row[3] == fingerprint:
                    return current  # a retry acknowledges this exact committed revision
            if current != expected_revision:
                raise EditConflictError("This item changed after editing began. Reload its current revision before saving.")
            if row is not None and at < timestamp(row[1], "prior edit time"):
                raise EditConflictError("This edit predates the currently saved changes.")
            revision = integer(current + 1, "edit revision", minimum=1, maximum=MAX_EDIT_REVISIONS)
            directory = self._edit_directory(task_id, version, kind, item_id, revision=revision, fingerprint=fingerprint)
            self._publish(directory, artifacts)
            connection.execute("INSERT INTO edit_history VALUES (?,?,?,?,?,?,?)",
                               (version, kind, item_id, revision, at, encoded.decode("utf-8"), fingerprint))
            connection.execute(
                "INSERT INTO edits VALUES (?,?,?,?,?,?,?) ON CONFLICT(version,kind,id) DO UPDATE SET "
                "revision=excluded.revision,at=excluded.at,content=excluded.content,digest=excluded.digest",
                (version, kind, item_id, revision, at, encoded.decode("utf-8"), fingerprint),
            )
        return revision

    def save_draft(
        self, task_id: str, version: str, draft_id: str, content: DraftContent, *, expected_revision: int, at: float,
    ) -> DraftRevision:
        published = self.read(task_id, version)
        if not any(draft.id == draft_id for draft in published.followup.drafts):
            raise ValueError("The draft does not belong to this generated package.")
        data = draft_data(content)
        artifacts = (Artifact("draft.eml", _email(content)), Artifact("draft.md", _draft_markdown(content)), Artifact("content.json", _json(data)))
        revision = self._save(task_id, version, "draft", draft_id, data, artifacts=artifacts, expected_revision=expected_revision, at=at)
        return self.draft_revision(task_id, version, draft_id, revision)

    def save_action(
        self, task_id: str, version: str, action_id: str, content: ActionContent, *, expected_revision: int, at: float,
    ) -> ActionRevision:
        published = self.read(task_id, version)
        if not any(action.id == action_id for action in published.followup.actions):
            raise ValueError("The action does not belong to this generated package.")
        revision = self._save(task_id, version, "action", action_id, action_data(content), artifacts=self._action_artifacts(action_id, content),
                              expected_revision=expected_revision, at=at)
        return self.action_revision(task_id, version, action_id, revision)
