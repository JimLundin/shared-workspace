"""One background owner for selecting meetings and observing their follow-through.

UI callbacks enqueue immutable selections or revision-checked edits. The owner
keeps catalog, preparation, and package I/O off that thread. Generated packages
come only from the validated store; narrative task results are never artifacts.
"""

import queue
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import assert_never, cast

from automation.task import Completed, RequestSource, Task, digest, identifier, integer, variant

from .meeting_followup import MAX_SOURCES, ActionContent, DraftContent, Transcript, TranscriptBundle, TranscriptSource
from .meeting_followup_store import (
    ActionRevision,
    DraftRevision,
    FollowupStore,
    PublishedFollowup,
    TranscriptNotReadyError,
    observe_transcript,
)
from .meeting_requests import MAX_REQUESTS, MeetingRequests, PreparedFollowup, followup_prompt, task_identity
from .meeting_store import MeetingArchive
from .work_window import WorkSnapshot

MAX_MEETINGS = 512
MAX_COMMANDS = 64
MAX_CHANGES = 128


class WorkPending(Enum):
    OBSERVATION = auto()


@dataclass(frozen=True, slots=True)
class ObservedWork:
    task: Task


@dataclass(frozen=True, slots=True)
class WorkUnavailable:
    reason: str
    previous: Task | None


type WorkObservation = WorkPending | ObservedWork | WorkUnavailable


@dataclass(frozen=True, slots=True)
class FollowupPackage:
    publication: PublishedFollowup
    drafts: tuple[DraftRevision, ...]
    actions: tuple[ActionRevision, ...]


@dataclass(frozen=True, slots=True)
class AvailablePackages:
    values: tuple[FollowupPackage, ...] = ()


@dataclass(frozen=True, slots=True)
class PackagesUnavailable:
    reason: str
    previous: tuple[FollowupPackage, ...]


type PackageObservation = AvailablePackages | PackagesUnavailable


@dataclass(frozen=True, slots=True)
class FollowupProgress:
    request: PreparedFollowup
    work: WorkObservation
    packages: PackageObservation


@dataclass(frozen=True, slots=True)
class CatalogProblem:
    reason: str


@dataclass(frozen=True, slots=True)
class RecoveryProblem:
    reason: str


@dataclass(frozen=True, slots=True)
class PrepareProblem:
    selected: tuple[TranscriptSource, ...]
    reason: str


type WorkflowProblem = CatalogProblem | RecoveryProblem | PrepareProblem


@dataclass(frozen=True, slots=True)
class DraftChange:
    task_id: str
    version: str
    draft_id: str
    content: DraftContent
    expected_revision: int
    attempt_id: str

    def __post_init__(self) -> None:
        identifier(self.task_id, "draft task identity")
        digest(self.version, "draft package version")
        identifier(self.draft_id, "draft identity")
        variant(self.content, DraftContent, "draft content")
        integer(self.expected_revision, "expected draft revision")
        identifier(self.attempt_id, "draft save attempt identity")


@dataclass(frozen=True, slots=True)
class ActionChange:
    task_id: str
    version: str
    action_id: str
    content: ActionContent
    expected_revision: int
    attempt_id: str

    def __post_init__(self) -> None:
        identifier(self.task_id, "action task identity")
        digest(self.version, "action package version")
        identifier(self.action_id, "action identity")
        variant(self.content, ActionContent, "action content")
        integer(self.expected_revision, "expected action revision")
        identifier(self.attempt_id, "action save attempt identity")


@dataclass(frozen=True, slots=True)
class DraftSaved:
    change: DraftChange
    revision: DraftRevision


@dataclass(frozen=True, slots=True)
class DraftSaveProblem:
    change: DraftChange
    reason: str


@dataclass(frozen=True, slots=True)
class ActionSaved:
    change: ActionChange
    revision: ActionRevision


@dataclass(frozen=True, slots=True)
class ActionSaveProblem:
    change: ActionChange
    reason: str


type ChangeResult = DraftSaved | DraftSaveProblem | ActionSaved | ActionSaveProblem


@dataclass(frozen=True, slots=True)
class MeetingSnapshot:
    transcripts: tuple[TranscriptSource, ...] = ()
    preparations: tuple[FollowupProgress, ...] = ()
    problems: tuple[WorkflowProblem, ...] = ()
    changes: tuple[ChangeResult, ...] = ()


def previous_task(observation: WorkObservation) -> Task | None:
    match observation:
        case ObservedWork(task):
            return task
        case WorkUnavailable(previous=task):
            return task
        case WorkPending.OBSERVATION:
            return None
        case _:
            assert_never(observation)


def package_values(observation: PackageObservation) -> tuple[FollowupPackage, ...]:
    match observation:
        case AvailablePackages(values) | PackagesUnavailable(previous=values):
            return values
        case _:
            assert_never(observation)


def work_observation(
    request: PreparedFollowup, snapshot: WorkSnapshot, previous: WorkObservation, submission_problem: str | None = None,
) -> WorkObservation:
    """A missing cached row cannot establish that an accepted submission failed."""
    last = previous_task(previous)
    problem = next((item.reason for item in snapshot.submission_problems if item.task_id == request.task_id), None)
    if problem is not None:
        return WorkUnavailable(problem, last)
    task = next((item for item in snapshot.tasks if item.id == request.task_id), None)
    if task is not None:
        # WorkAgent preserves its established outer-whitespace normalization.
        # Retries still submit the exact prompt saved with this preparation.
        if not isinstance(task.source, RequestSource) or task.prompt != request.prompt.strip():
            return WorkUnavailable("The saved task identity belongs to different work.", last)
        if snapshot.status:
            return WorkUnavailable(snapshot.status, task)
        return ObservedWork(task)
    if reason := submission_problem or snapshot.status:
        return WorkUnavailable(reason, last)
    if last is not None:
        return WorkUnavailable("Waiting to confirm this saved task again.", last)
    return WorkPending.OBSERVATION


def package_matches(request: PreparedFollowup, publication: PublishedFollowup) -> bool:
    return publication.task_id == request.task_id and publication.followup.sources == request.bundle


def _chronological(source: TranscriptSource) -> tuple[float, str]:
    return source.started_at, source.id


def _selection(value: object) -> tuple[TranscriptSource, ...]:
    if not isinstance(value, tuple):
        raise ValueError("Select one or more completed recording segments.")
    selected = cast(tuple[object, ...], value)
    if not 1 <= len(selected) <= MAX_SOURCES or any(not isinstance(item, TranscriptSource) for item in selected):
        raise ValueError(f"Select between one and {MAX_SOURCES} completed recording segments.")
    sources = cast(tuple[TranscriptSource, ...], selected)
    if len({source.id for source in sources}) != len(sources):
        raise ValueError("A recording segment can only be selected once.")
    return sources


@dataclass(frozen=True, slots=True)
class _Prepare:
    selected: tuple[TranscriptSource, ...]


@dataclass(frozen=True, slots=True)
class _Observe:
    completed: threading.Event


type _Command = _Prepare | DraftChange | ActionChange | _Observe


class MeetingWorkflow:
    """Bounded background I/O behind immutable UI snapshots.

    ``publish`` runs on this owner's thread; the view schedules its own updates.
    ``retain_work`` reports this owner's complete interest set. ``refresh`` is
    an observation barrier for this queue only, never for the work process's
    queue or journal. The caller retains selection and dirty editor text using
    source identities and the task/version/artifact/revision/attempt in change
    results. Each save attempt must receive a new identity from the caller.
    """

    __slots__ = (
        "_attempted", "_changes", "_closed", "_closing", "_jobs", "_lock", "_prepared", "_problems", "_progress",
        "_retained", "_snapshot", "_sources", "_submission_problems", "_thread", "_wake", "archive", "clock", "followups",
        "publish", "read_work", "report_error", "requests", "retain_work", "submit_identified",
    )

    def __init__(
        self, archive: MeetingArchive, requests: MeetingRequests, followups: FollowupStore, *,
        submit_identified: Callable[[str, str], None], read_work: Callable[[], WorkSnapshot],
        retain_work: Callable[[tuple[str, ...]], None], publish: Callable[[MeetingSnapshot], None],
        report_error: Callable[[str], None], clock: Callable[[], float] = time.time,
    ) -> None:
        self.archive, self.requests, self.followups = archive, requests, followups
        self.submit_identified, self.read_work, self.retain_work = submit_identified, read_work, retain_work
        self.publish, self.report_error, self.clock = publish, report_error, clock
        self._lock = threading.Lock()
        self._jobs: queue.Queue[_Command] = queue.Queue(maxsize=MAX_COMMANDS)
        self._wake, self._closing, self._closed = threading.Event(), threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = MeetingSnapshot()
        self._sources: tuple[TranscriptSource, ...] = ()
        self._prepared: dict[str, PreparedFollowup] = {}
        self._progress: dict[str, FollowupProgress] = {}
        self._attempted: set[str] = set()
        self._submission_problems: dict[str, str] = {}
        self._problems: dict[str, WorkflowProblem] = {}
        self._changes: dict[tuple[str, str, str, str, str], ChangeResult] = {}
        self._retained: tuple[str, ...] = ()

    def start(self) -> None:
        with self._lock:
            if self._thread is None and not self._closing.is_set():
                self._start()

    def _start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="meeting follow-through", daemon=True)
        self._thread.start()

    def _enqueue(self, command: _Command) -> None:
        with self._lock:
            if self._closing.is_set():
                raise ValueError("The meeting workflow is closing.")
            try:
                self._jobs.put_nowait(command)
            except queue.Full:
                raise ValueError("The meeting workflow queue is full; wait for the pending change.") from None
            self._wake.set()

    def refresh(self) -> threading.Event:
        completed = threading.Event()
        self._enqueue(_Observe(completed))
        return completed

    def prepare(self, selected: tuple[TranscriptSource, ...]) -> None:
        self._enqueue(_Prepare(_selection(selected)))

    def save_draft(self, change: DraftChange) -> None:
        variant(change, DraftChange, "draft change")
        self._enqueue(change)

    def save_action(self, change: ActionChange) -> None:
        variant(change, ActionChange, "action change")
        self._enqueue(change)

    def snapshot(self) -> MeetingSnapshot:
        with self._lock:
            return self._snapshot

    def close(self) -> None:
        with self._lock:
            if self._closing.is_set():
                return
            self._closing.set()
            if self._thread is None:
                if self._jobs.empty():
                    self._closed.set()
                else:
                    self._start()
            self._wake.set()

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def _catalog(self) -> None:
        try:
            journals = self.archive.journals()
            retained = {source.path: source for source in self._sources}
            sources: list[TranscriptSource] = []
            errors: list[str] = []
            for path in reversed(journals[-MAX_MEETINGS:]):
                store = self.archive.open(path.parent)
                try:
                    sources.append(observe_transcript(store).source)
                except TranscriptNotReadyError:
                    continue
                except Exception as error:
                    errors.append(f"{path.parent.name}: {error}")
                    if previous := retained.get(str(store.transcript.absolute())):
                        sources.append(previous)
            self._sources = tuple(sorted(sources, key=_chronological, reverse=True))
            if len(journals) > MAX_MEETINGS:
                errors.append(f"Showing the most recent {MAX_MEETINGS} recording segments.")
            if errors:
                self._problems["catalog"] = CatalogProblem("\n".join(errors[:8])[:8000])
            else:
                self._problems.pop("catalog", None)
        except Exception as error:
            self._problems["catalog"] = CatalogProblem(str(error) or "The meeting archive could not be read.")
            self.report_error(traceback.format_exc())

    def _remember(self, request: PreparedFollowup) -> None:
        self._prepared.pop(request.task_id, None)
        self._prepared[request.task_id] = request
        while len(self._prepared) > MAX_REQUESTS:
            oldest = next(iter(self._prepared))
            self._prepared.pop(oldest)
            self._progress.pop(oldest, None)
            self._attempted.discard(oldest)
            self._submission_problems.pop(oldest, None)

    def _retain(self) -> bool:
        identities = tuple(self._prepared)
        try:
            if identities != self._retained:
                self.retain_work(identities)
                self._retained = identities
            self._problems.pop("watch", None)
            return True
        except Exception as error:
            self._problems["watch"] = RecoveryProblem(str(error) or "The saved meeting tasks could not be observed.")
            self.report_error(traceback.format_exc())
            return False

    def _submit(self, request: PreparedFollowup, *, retry: bool = False) -> None:
        if request.task_id in self._attempted and not retry:
            return
        self._attempted.add(request.task_id)
        try:
            self.submit_identified(request.task_id, request.prompt)
            self._submission_problems.pop(request.task_id, None)
        except Exception as error:
            self._submission_problems[request.task_id] = str(error) or "The saved preparation could not enter the work queue."
            self.report_error(traceback.format_exc())

    def _recover(self) -> None:
        try:
            recovered = self.requests.list()
            for request in reversed(recovered):
                if request.task_id not in self._prepared:
                    self._remember(request)
            self._problems.pop("recovery", None)
            if self._retain():
                for request in self._prepared.values():
                    self._submit(request)
        except Exception as error:
            self._problems["recovery"] = RecoveryProblem(str(error) or "Saved meeting preparations could not be recovered.")
            self.report_error(traceback.format_exc())

    def _prepare(self, selected: tuple[TranscriptSource, ...]) -> None:
        try:
            folders = {path.parent.name: path.parent for path in self.archive.journals()}
            observed: list[Transcript] = []
            for source in selected:
                if source.id not in folders:
                    raise ValueError("A selected recording is no longer in the meeting archive. Refresh the selection.")
                current = observe_transcript(self.archive.open(folders[source.id]))
                if current.source != source:
                    raise ValueError("A selected transcript changed. Refresh and select its current version before preparing follow-ups.")
                observed.append(current)
            bundle = TranscriptBundle(tuple(observed))
            identity = task_identity(bundle)
            request = self.requests.get(identity)
            if request is None:
                request = self.requests.prepare(identity, bundle, followup_prompt(bundle), at=self.clock())
            elif request.bundle != bundle:
                raise ValueError("The saved preparation belongs to different transcript observations.")
            # The exact persisted prompt is reused; rebuilding it here would
            # turn a recipe-text change into an identified submission conflict.
            self._remember(request)
            previous = self._progress.get(identity)
            if self._retain():
                self._submit(request, retry=previous is not None and isinstance(previous.work, WorkUnavailable))
            self._problems.pop("prepare", None)
        except Exception as error:
            self._problems["prepare"] = PrepareProblem(selected, str(error) or "The selected meeting could not be prepared.")
            self.report_error(traceback.format_exc())

    def _record_change(self, result: ChangeResult) -> None:
        change = result.change
        match change:
            case DraftChange(task_id, version, draft_id):
                key = "draft", task_id, version, draft_id, change.attempt_id
            case ActionChange(task_id, version, action_id):
                key = "action", task_id, version, action_id, change.attempt_id
            case _:
                assert_never(change)
        self._changes.pop(key, None)
        self._changes[key] = result
        while len(self._changes) > MAX_CHANGES:
            self._changes.pop(next(iter(self._changes)))

    def _edit(self, change: DraftChange | ActionChange) -> None:
        result: ChangeResult
        try:
            request = self.requests.get(change.task_id)
            publication = self.followups.read(change.task_id, change.version)
            if request is None or not package_matches(request, publication):
                raise ValueError("This package does not belong to the saved meeting preparation.")
            match change:
                case DraftChange(task_id, version, draft_id, content, expected):
                    draft = self.followups.save_draft(task_id, version, draft_id, content, expected_revision=expected, at=self.clock())
                    result = DraftSaved(change, draft)
                case ActionChange(task_id, version, action_id, content, expected):
                    action = self.followups.save_action(task_id, version, action_id, content, expected_revision=expected, at=self.clock())
                    result = ActionSaved(change, action)
                case _:
                    assert_never(change)
        except Exception as error:
            reason = str(error) or "The edit could not be saved. Your entered text has been retained."
            match change:
                case DraftChange():
                    result = DraftSaveProblem(change, reason)
                case ActionChange():
                    result = ActionSaveProblem(change, reason)
                case _:
                    assert_never(change)
            self.report_error(traceback.format_exc())
        self._record_change(result)

    def _packages(self, request: PreparedFollowup, work: WorkObservation, previous: PackageObservation) -> PackageObservation:
        try:
            values: list[FollowupPackage] = []
            for publication in self.followups.versions(request.task_id):
                if not package_matches(request, publication):
                    raise ValueError("A published package belongs to different selected transcript evidence.")
                values.append(FollowupPackage(
                    publication, self.followups.drafts(request.task_id, publication.version),
                    self.followups.actions(request.task_id, publication.version),
                ))
            if not values and isinstance(work, ObservedWork) and isinstance(work.task.state, Completed):
                raise ValueError("The task finished without a verified meeting package. Its written reply is not a published artifact.")
            if not values and package_values(previous):
                raise ValueError("The previously observed meeting package is no longer available.")
            return AvailablePackages(tuple(values))
        except Exception as error:
            return PackagesUnavailable(str(error) or "The meeting package could not be read.", package_values(previous))

    def _observe(self, *, refresh_packages: bool) -> None:
        snapshot: WorkSnapshot | None = None
        read_problem = ""
        try:
            snapshot = self.read_work()
            variant(snapshot, WorkSnapshot, "meeting work observation")
        except Exception as error:
            read_problem = str(error) or "The work process could not be observed."
            self.report_error(traceback.format_exc())
        progress: dict[str, FollowupProgress] = {}
        for request in self._prepared.values():
            previous = self._progress.get(request.task_id, FollowupProgress(request, WorkPending.OBSERVATION, AvailablePackages()))
            work = WorkUnavailable(read_problem, previous_task(previous.work)) if snapshot is None else work_observation(
                request, snapshot, previous.work, self._submission_problems.get(request.task_id),
            )
            if isinstance(work, ObservedWork):
                self._submission_problems.pop(request.task_id, None)
            packages = previous.packages
            if refresh_packages or work != previous.work or isinstance(packages, PackagesUnavailable) or not package_values(packages):
                packages = self._packages(request, work, packages)
            progress[request.task_id] = FollowupProgress(request, work, packages)
        self._progress = progress
        current = MeetingSnapshot(
            self._sources, tuple(reversed(tuple(progress.values()))), tuple(self._problems.values()), tuple(self._changes.values()),
        )
        with self._lock:
            changed = current != self._snapshot
            self._snapshot = current
        if changed:
            try:
                self.publish(current)
            except Exception:
                self.report_error(traceback.format_exc())

    def _run(self) -> None:
        try:
            self._catalog()
            self._recover()
            first = True
            while True:
                observations: list[threading.Event] = []
                refresh = first
                first = False
                for _ in range(MAX_COMMANDS):
                    try:
                        command = self._jobs.get_nowait()
                    except queue.Empty:
                        break
                    match command:
                        case _Prepare(selected):
                            self._prepare(selected)
                            refresh = True
                        case DraftChange() | ActionChange():
                            self._edit(command)
                            refresh = True
                        case _Observe(completed):
                            observations.append(completed)
                            refresh = True
                        case _:
                            assert_never(command)
                try:
                    if refresh:
                        self._catalog()
                        self._recover()
                    self._observe(refresh_packages=refresh)
                finally:
                    for completed in observations:
                        completed.set()
                if self._closing.is_set() and self._jobs.empty():
                    break
                if self._jobs.empty():
                    self._wake.wait(0.5)
                    self._wake.clear()
        except Exception:
            self.report_error(traceback.format_exc())
        finally:
            try:
                self.retain_work(())
            finally:
                self._closed.set()
