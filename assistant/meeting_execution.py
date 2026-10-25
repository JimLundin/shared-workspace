"""Prepared meeting execution and exact artifact/mail recovery, outside the UI."""

import json
import threading
from dataclasses import dataclass
from typing import Protocol, assert_never, cast

from automation.actions import ActionError
from automation.execution import Outcome, WorkExecution
from automation.task import ActionSpec, ActionUncertain, ActionVerified, Blocked, Completed, RequestSource, Task, Waiting, digest, identifier, integer

from . import outlook
from .llm import Json
from .meeting_followup import CaptureNotice, EmailAddress, NoCaptureNotice, draft_data, followup_data, parse_followup
from .meeting_followup_store import FollowupStore, Publication, PublishedFollowup
from .meeting_requests import MeetingRequests, PreparedFollowup, task_identity

SOURCE_PAGE = 32_000
_MAIL_RESOURCE = "meeting-draft:"


def encoded(data: dict[str, Json]) -> str:
    return json.dumps(data, ensure_ascii=False, allow_nan=False)


class Drafts(Protocol):
    def save(self, request: outlook.DraftRequest, cancel: threading.Event) -> outlook.DraftResult: ...
    def inspect(self, request: outlook.DraftRequest, cancel: threading.Event) -> outlook.DraftResult | outlook.Missing: ...


@dataclass(frozen=True, slots=True)
class MailTarget:
    version: str
    draft_id: str
    revision: int
    fingerprint: str

    def __post_init__(self) -> None:
        digest(self.version, "meeting package version")
        identifier(self.draft_id, "meeting draft identity")
        integer(self.revision, "meeting draft revision")
        digest(self.fingerprint, "mail content fingerprint")

    @property
    def resource(self) -> str:
        return _MAIL_RESOURCE + json.dumps((self.version, self.draft_id, self.revision, self.fingerprint), separators=(",", ":"))


def mail_target(specification: ActionSpec) -> MailTarget:
    """Only bounded non-content identities are retained in action metadata."""
    values = tuple(value[len(_MAIL_RESOURCE):] for value in specification.resources if value.startswith(_MAIL_RESOURCE))
    if specification.tool != "meeting_save_draft" or len(values) != 1:
        raise ValueError("The saved mail action has no exact draft revision.")
    value: object = json.loads(values[0])
    if not isinstance(value, list):
        raise ValueError("The saved mail action has an invalid draft revision.")
    items = cast(list[object], value)
    if len(items) != 4:
        raise ValueError("The saved mail action has an invalid draft revision.")
    return MailTarget(digest(items[0]), identifier(items[1]), integer(items[2], "saved draft revision"), digest(items[3]))


def verified_mail(request: outlook.DraftRequest, result: outlook.DraftResult | outlook.Missing) -> ActionVerified | ActionUncertain:
    match result:
        case outlook.SavedOutlookDraft() | outlook.SavedEmlDraft():
            if result.key != outlook.draft_key(request) or result.fingerprint != outlook.fingerprint(request):
                return ActionUncertain("The saved mail result belongs to a different draft or content revision.")
            destination = "Outlook draft" if isinstance(result, outlook.SavedOutlookDraft) else "complete email draft file"
            return ActionVerified(f"The {destination} was read back with the exact subject, body and recipients; no message was sent.")
        case outlook.DraftConflict(reason) | outlook.DraftUncertain(reason):
            return ActionUncertain(reason)
        case outlook.Cancelled.BEFORE_EFFECT:
            return ActionVerified("The draft adapter stopped before saving a message.")
        case outlook.Missing.DRAFT:
            return ActionUncertain("The draft has no saved delivery binding to inspect.")
        case _:
            assert_never(result)


class MeetingWork:
    __slots__ = ("drafts", "packages", "requests")

    def __init__(self, requests: MeetingRequests, packages: FollowupStore, drafts: Drafts) -> None:
        self.requests, self.packages, self.drafts = requests, packages, drafts

    def session(self, task: Task) -> MeetingRun | None:
        # The workflow reserves this identity namespace before task submission.
        # A missing binding must not silently gain the general desktop toolset.
        if not task.id.startswith("meeting-followup-"):
            return None
        request = self.requests.get(task.id)
        if request is None:
            raise ValueError("The selected recording snapshots are missing. Prepare the meeting follow-up again.")
        if task.id != task_identity(request.bundle) or task.prompt != request.prompt.strip() or not isinstance(task.source, RequestSource):
            raise ValueError("This task no longer matches its exact meeting preparation. Prepare a new follow-up from the intended recordings.")
        return MeetingRun(request, self.packages, self.drafts)


class MeetingRun:
    __slots__ = ("drafts", "mail_results", "packages", "request", "selected")

    def __init__(self, request: PreparedFollowup, packages: FollowupStore, drafts: Drafts) -> None:
        self.request, self.packages, self.drafts = request, packages, drafts
        self.selected: PublishedFollowup | None = None
        self.mail_results: dict[MailTarget, outlook.DraftResult] = {}

    def sources(self, source_id: str = "", offset: int = 0) -> dict[str, Json]:
        integer(offset, "transcript offset", maximum=2 * 1024 * 1024)
        bundle = self.request.bundle
        if not source_id:
            if offset:
                raise ValueError("Choose a source before requesting a text offset.")
            sources: list[Json] = []
            for transcript in bundle.transcripts:
                source = transcript.source
                match source.notice:
                    case CaptureNotice(text=notice):
                        capture_notice: str | None = notice
                    case NoCaptureNotice.NONE:
                        capture_notice = None
                    case _:
                        assert_never(source.notice)
                sources.append({"source_id": source.id, "title": source.title, "sha256": source.sha256,
                                "started_at": source.started_at, "duration_seconds": source.duration_seconds,
                                "capture_notice": capture_notice, "characters": len(transcript.text)})
            return {"source_digest": bundle.fingerprint, "sources": sources}
        source_id = identifier(source_id, "selected recording identity")
        selected = next((value for value in bundle.transcripts if value.source.id == source_id), None)
        if selected is None:
            raise ValueError("That recording is outside this task's selected sources.")
        if offset > len(selected.text):
            raise ValueError("The requested text offset is outside the selected recording.")
        end = min(len(selected.text), offset + SOURCE_PAGE)
        return {"source_digest": bundle.fingerprint, "source_id": source_id, "sha256": selected.source.sha256,
                "offset": offset, "text": selected.text[offset:end], "next_offset": end if end < len(selected.text) else None}

    def publication(self, value: object) -> Publication:
        # Validation precedes action registration and any artifact writes.
        encoded_value = json.dumps(value, ensure_ascii=False, allow_nan=False)
        result = parse_followup(self.request.bundle, encoded_value)
        return self.packages.plan(self.request.task_id, result)

    def publish(self, publication: Publication) -> dict[str, Json]:
        if publication.task_id != self.request.task_id or publication.followup.sources != self.request.bundle:
            raise ValueError("The prepared artifacts belong to different meeting work.")
        self.selected = self.packages.publish(publication)
        return self._package(self.selected)

    def _read(self, version: str) -> PublishedFollowup:
        package = self.packages.read(self.request.task_id, digest(version, "package version"))
        if package.followup.sources != self.request.bundle:
            raise ValueError("The saved package belongs to different recording snapshots.")
        return package

    def _package(self, package: PublishedFollowup) -> dict[str, Json]:
        drafts: list[Json] = [
            {"draft_id": value.id, "revision": value.revision, "content": draft_data(value.content),
             "email_file": str(value.files.eml), "markdown_file": str(value.files.markdown)}
            for value in self.packages.drafts(package.task_id, package.version)
        ]
        return {"version": package.version, "source_digest": package.followup.sources.fingerprint,
                "notes_path": str(package.notes_path), "actions_path": str(package.actions_path),
                "followup": followup_data(package.followup), "drafts": drafts, "verified": True}

    def result(self, version: str = "") -> dict[str, Json]:
        if not version:
            versions: list[Json] = [
                {"version": package.version, "title": package.followup.title,
                 "source_digest": package.followup.sources.fingerprint}
                for package in self.packages.versions(self.request.task_id)
            ]
            return {"versions": versions, "source_digest": self.request.bundle.fingerprint}
        self.selected = self._read(version)
        return self._package(self.selected)

    def _draft(self, version: str, draft_id: str, revision: int) -> outlook.DraftRequest:
        self._read(version)
        content = self.packages.draft_revision(self.request.task_id, version, draft_id, revision).content
        recipients = tuple(outlook.Recipient(value.email.value) for value in content.recipients if isinstance(value.email, EmailAddress))
        return outlook.DraftRequest(self.request.task_id, draft_id, outlook.DraftContent(content.subject, content.body, to=recipients))

    def prepare_mail(self, version: str, draft_id: str, revision: int) -> tuple[MailTarget, outlook.DraftRequest]:
        version, draft_id = digest(version, "package version"), identifier(draft_id, "draft identity")
        revision = integer(revision, "draft revision")
        current = next((value for value in self.packages.drafts(self.request.task_id, version) if value.id == draft_id), None)
        if current is None or current.revision != revision:
            raise ValueError("The draft changed after it was selected. Inspect its current revision before saving to mail.")
        request = self._draft(version, draft_id, revision)
        return MailTarget(version, draft_id, revision, outlook.fingerprint(request)), request

    def save_mail(self, target: MailTarget, request: outlook.DraftRequest, stop: threading.Event) -> outlook.DraftResult:
        if outlook.fingerprint(request) != target.fingerprint or request.workflow_id != self.request.task_id or request.draft_id != target.draft_id:
            raise ValueError("The mail save does not match its exact selected draft.")
        result = self.drafts.save(request, stop)
        self.mail_results[target] = result
        return result

    def mail_result(self, target: MailTarget, result: outlook.DraftResult) -> dict[str, Json]:
        data: dict[str, Json] = {"version": target.version, "draft_id": target.draft_id, "revision": target.revision}
        match result:
            case outlook.SavedOutlookDraft():
                data.update(saved=True, destination="outlook", message="Saved an unsent Outlook draft and verified its complete contents.")
            case outlook.SavedEmlDraft(path=path, unavailable=unavailable):
                data.update(saved=True, destination="email_file", path=str(path), message=unavailable.reason)
            case outlook.DraftConflict(reason) | outlook.DraftUncertain(reason):
                data.update(saved=False, problem=reason)
            case outlook.Cancelled.BEFORE_EFFECT:
                data.update(saved=False, problem="Stopped before the mail draft was saved.")
            case _:
                assert_never(result)
        return data

    def recover(self, work: WorkExecution, stop: threading.Event) -> None:
        """An uncertain native Save can only be resolved by its exact readback."""
        with work.actions.resources.hold(("outlook",), stop):
            for action in work.store.unverified_actions(self.request.task_id):
                if action.specification.tool != "meeting_save_draft":
                    continue
                target = mail_target(action.specification)
                request = self._draft(target.version, target.draft_id, target.revision)
                if outlook.fingerprint(request) != target.fingerprint:
                    raise ActionError("The recorded mail attempt does not match the saved draft revision.")
                result = self.drafts.inspect(request, stop)
                observed = verified_mail(request, result)
                if not isinstance(observed, ActionVerified) or result is outlook.Cancelled.BEFORE_EFFECT:
                    reason = observed.reason if isinstance(observed, ActionUncertain) else "Mail inspection was stopped."
                    raise ActionError(reason)
                if work.store.finish_action(action.id, observed, at=work.clock()) is None:
                    raise ActionError("The draft was verified, but its action acknowledgement could not be saved.")
                if result is not outlook.Missing.DRAFT:
                    self.mail_results[target] = result

    def _deliveries(
        self, package: PublishedFollowup,
    ) -> dict[str, tuple[MailTarget, outlook.SavedOutlookDraft | outlook.SavedEmlDraft]]:
        delivered: dict[str, tuple[MailTarget, outlook.SavedOutlookDraft | outlook.SavedEmlDraft]] = {}
        for target, result in self.mail_results.items():
            if target.version != package.version or not isinstance(result, (outlook.SavedOutlookDraft, outlook.SavedEmlDraft)):
                continue
            request = self._draft(target.version, target.draft_id, target.revision)
            if target.fingerprint != outlook.fingerprint(request) or not isinstance(verified_mail(request, result), ActionVerified):
                raise ValueError("The saved mail receipt does not match its selected draft revision.")
            previous = delivered.get(target.draft_id)
            if previous is None or target.revision > previous[0].revision:
                delivered[target.draft_id] = target, result
        missing = tuple(draft.id for draft in package.followup.drafts if draft.id not in delivered)
        if missing:
            raise ValueError("These meeting drafts have no verified saved mail destination: " + ", ".join(missing) + ". Resume to save them.")
        return delivered

    def completed(self, outcome: Outcome) -> Outcome:
        if not isinstance(outcome, Completed):
            return outcome
        if self.selected is None:
            return Waiting(Blocked("A meeting result must be published and inspected before this task is complete. Resume to finish it."))
        try:
            package = self._read(self.selected.version)
            delivered = self._deliveries(package)
            local_drafts = self.packages.drafts(package.task_id, package.version)
        except (OSError, ValueError) as error:
            return Waiting(Blocked(f"The saved meeting artifacts could not be verified: {error}"[:4000]))
        result = (
            f"Prepared {package.followup.title} from {len(package.followup.sources.transcripts)} recording(s).\n\n"
            f"Notes: {package.notes_path}\nActions ({len(package.followup.actions)}): {package.actions_path}\n"
        )
        for draft in local_drafts:
            target, saved = delivered[draft.id]
            destination = str(saved.path) if isinstance(saved, outlook.SavedEmlDraft) else "unsent Outlook draft"
            result += f"\nSaved mail draft {draft.id} (revision {target.revision}): {destination}\nLocal draft: {draft.files.eml}"
            if draft.revision > target.revision:
                result += f"\nA newer local edit (revision {draft.revision}) is retained and was not copied to the saved mail draft."
        native = sum(isinstance(value[1], outlook.SavedOutlookDraft) for value in delivered.values())
        if native:
            result += f"\nVerified unsent Outlook drafts: {native}."
        result += "\nNo follow-up messages were sent."
        evidence = (*outcome.evidence, f"Verified meeting package SHA-256 {package.version}.",
                    f"Selected recording bundle SHA-256 {self.request.bundle.fingerprint}.")
        return Completed(result, evidence[-64:])
