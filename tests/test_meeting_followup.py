"""Meeting follow-through uses exact source bytes and preserves committed edits."""

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import threading
import zipfile
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import replace
from email.parser import BytesParser
from pathlib import Path
from typing import Literal, cast
from unittest import SkipTest, TestCase

from assistant.llm import Json
from assistant.meeting_followup import (
    MAX_MODEL_BYTES,
    MAX_TRANSCRIPT_BYTES,
    ActionContent,
    ActionStatus,
    Addressee,
    AssignedDue,
    AssignedOwner,
    CaptureNotice,
    DraftContent,
    EmailAddress,
    NamedOwner,
    StatedDue,
    TranscriptBundle,
    Unspecified,
    bundle_data,
    bundle_of,
    followup_data,
    parse_followup,
    resolve_citation,
)
from assistant.meeting_followup_store import (
    ArtifactChangedError,
    EditConflictError,
    FollowupStore,
    PublishedFollowup,
    SourceChangedError,
    TranscriptNotReadyError,
    observe_transcript,
)
from assistant.meeting_store import MeetingArchive, MeetingStore
from automation.actions import FileVerifier
from automation.files import filesystem_path
from automation.task import ActionVerified

from .test_meetings import SPEECH

ASSIGNMENT = "Ada will send the release notes by Thursday."
ADDRESS = "Ada can be reached at ada@example.test."
DECISION = "We decided to ship on Friday."
UNASSIGNED = "Please arrange a customer review; no owner or date was assigned."
NOTICE = "Microphone disconnected; the missing interval was not recorded."
BODY = "Hi Ada,\n\nPlease send the release notes by Thursday. We also need to arrange a customer review.\n\nThank you."


def completed_meeting(archive: MeetingArchive, title: str, transcript: str, *, notice: str = "") -> MeetingStore:
    store = archive.create("microphone", title)
    store.append(SPEECH)
    store.finish_capture(notice)
    batch = store.batch()
    assert batch is not None
    store.complete(batch, transcript)
    store.export()
    return store


def followup_sources(tmp_path: Path) -> tuple[TranscriptBundle, tuple[MeetingStore, ...]]:
    archive = MeetingArchive(tmp_path / "meetings")
    first = completed_meeting(archive, "Release planning, part 1", ASSIGNMENT + "\n" + ADDRESS + "\n" + DECISION, notice=NOTICE)
    second = completed_meeting(archive, "Release planning, part 2", UNASSIGNED)
    return TranscriptBundle((observe_transcript(first), observe_transcript(second))), (first, second)


def followup_response(bundle: TranscriptBundle) -> dict[str, Json]:
    first, second = (item.source.id for item in bundle.transcripts)
    assignment: Json = {"source_id": first, "quote": ASSIGNMENT}
    address: Json = {"source_id": first, "quote": ADDRESS}
    decision: Json = {"source_id": first, "quote": DECISION}
    unassigned: Json = {"source_id": second, "quote": UNASSIGNED}
    return {
        "source_digest": bundle.fingerprint, "title": "Release planning follow-through",
        "notes": [{"text": "The meeting covered release notes and a customer review.", "citations": [assignment, unassigned]}],
        "decisions": [{"text": "Ship on Friday.", "citations": [decision]}],
        "actions": [
            {"id": "notes", "text": "Send the release notes.", "citations": [assignment],
             "owner": {"kind": "named", "name": "Ada", "evidence": assignment},
             "due": {"kind": "stated", "text": "Thursday", "evidence": assignment}},
            {"id": "review", "text": "Arrange a customer review.", "citations": [unassigned],
             "owner": {"kind": "unspecified"}, "due": {"kind": "unspecified"}},
        ],
        "drafts": [
            {"id": "follow-up", "subject": "Release planning follow-up", "body": BODY,
             "recipients": [{"name": "Ada", "email": "ada@example.test", "citations": [address]}],
             "citations": [assignment, address, unassigned], "action_ids": ["notes", "review"]},
        ],
    }


def response_item(data: dict[str, Json], key: str, index: int = 0) -> dict[str, Json]:
    collection = data[key]
    assert isinstance(collection, list)
    item = collection[index]
    assert isinstance(item, dict)
    return item


@contextmanager
def long_package_root(tmp_path: Path) -> Generator[tuple[Path, Path]]:
    top = tmp_path / "deep-packages"
    root = top
    while len(str(root)) < 300:
        root /= "nested-" + "x" * 40
    # Fixture inspection and cleanup must still work when production access fails.
    native = str(top)
    if os.name == "nt":
        native = "\\\\?\\UNC\\" + native[2:] if native.startswith("\\\\") else "\\\\?\\" + native
    accessible_top = Path(native)
    try:
        yield root, accessible_top / root.relative_to(top)
    finally:
        if accessible_top.exists():
            shutil.rmtree(accessible_top)


def test_windows_filesystem_spelling_preserves_drive_unc_and_extended_targets() -> None:
    if os.name != "nt":
        raise SkipTest("Windows filesystem spelling")
    cases = (
        (r"C:\projects\previous\..\notes.md", r"\\?\C:\projects\notes.md"),
        (r"\\server\share\previous\..\notes.md", r"\\?\UNC\server\share\notes.md"),
        (r"\\?\C:\projects\notes.md", r"\\?\C:\projects\notes.md"),
        (r"\\?\UNC\server\share\notes.md", r"\\?\UNC\server\share\notes.md"),
        (r"\\.\C:\projects\notes.md", r"\\.\C:\projects\notes.md"),
    )
    for original, expected in cases:
        assert str(filesystem_path(Path(original))) == expected


def test_observation_keeps_corrected_bytes_capture_notices_and_ordered_source_identity(tmp_path: Path) -> None:
    bundle, journals = followup_sources(tmp_path)
    original = bundle.transcripts[0]
    corrected = b"# Corrected transcript\r\n\r\n" + "Åsa corrected the wording.\r\n".encode()
    before_revision = journals[0].state().revision
    journals[0].transcript.write_bytes(corrected)
    changed = observe_transcript(journals[0])
    assert journals[0].state().revision == before_revision
    assert changed.content == corrected and changed.source.sha256 == hashlib.sha256(corrected).hexdigest()
    assert changed.text == "# Corrected transcript\r\n\r\nÅsa corrected the wording.\r\n"
    assert isinstance(changed.source.notice, CaptureNotice) and changed.source.notice.text == NOTICE
    assert changed.source.sha256 != original.source.sha256
    restored = bundle_of(bundle_data(TranscriptBundle((changed, bundle.transcripts[1]))))
    assert restored.transcripts[0].content == corrected
    damaged_snapshot = bundle_data(restored)
    response_item(damaged_snapshot, "sources")["text"] = "A different transcript with the old digest."
    with TestCase().assertRaisesRegex(ValueError, "source digest"):
        bundle_of(damaged_snapshot)
    assert TranscriptBundle(tuple(reversed(bundle.transcripts))).fingerprint != bundle.fingerprint
    with TestCase().assertRaisesRegex(ValueError, "more than once"):
        TranscriptBundle((original, original))


def test_notes_assignments_and_addresses_resolve_to_their_own_source_quotes(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    assigned, unknown = followup.actions
    assert isinstance(assigned.owner, NamedOwner) and assigned.owner.name == "Ada"
    assert isinstance(assigned.due, StatedDue) and assigned.due.text == "Thursday"
    assert assigned.owner.evidence in assigned.citations and assigned.due.evidence in assigned.citations
    assert unknown.owner is Unspecified.NOT_STATED and unknown.due is Unspecified.NOT_STATED
    assert followup.drafts[0].recipients[0].addressee.email == EmailAddress("ada@example.test")
    assert followup.drafts[0].body == BODY and followup.drafts[0].action_ids == ("notes", "review")
    assert parse_followup(bundle, json.dumps(followup_data(followup))) == followup


def test_repeated_quotes_need_disambiguation_and_cannot_switch_sources(tmp_path: Path) -> None:
    bundle, journals = followup_sources(tmp_path)
    journals[0].transcript.write_bytes(b"# Corrected\n\nPlease follow up.\n\nPlease follow up.\n")
    observed = observe_transcript(journals[0])
    current = TranscriptBundle((observed, bundle.transcripts[1]))
    with TestCase().assertRaisesRegex(ValueError, "ambiguous"):
        resolve_citation(current, observed.source.id, "Please follow up.")
    selected = resolve_citation(current, observed.source.id, "Please follow up.", occurrence=1)
    assert selected.line == 5 and selected.start == 32 and selected.end == 49
    with TestCase().assertRaisesRegex(ValueError, "does not occur"):
        resolve_citation(current, bundle.transcripts[1].source.id, "Please follow up.")
    with TestCase().assertRaisesRegex(ValueError, "does not exist"):
        resolve_citation(current, observed.source.id, "Please follow up.", occurrence=2)


def test_model_cannot_invent_assignments_addresses_sources_or_unattached_evidence(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    invalid: list[dict[str, Json]] = []
    source = followup_response(bundle)
    owners: tuple[Json, ...] = (
        {"kind": "named", "name": "Mallory", "evidence": {"source_id": bundle.transcripts[0].source.id, "quote": ASSIGNMENT}},
        {"kind": "named", "name": "A", "evidence": {"source_id": bundle.transcripts[0].source.id, "quote": ASSIGNMENT}},
        {"kind": "named", "name": "Ada", "evidence": {"source_id": bundle.transcripts[0].source.id, "quote": ADDRESS}},
    )
    for owner in owners:
        candidate = deepcopy(source)
        response_item(candidate, "actions")["owner"] = owner
        invalid.append(candidate)
    candidate = deepcopy(source)
    response_item(candidate, "actions")["due"] = {
        "kind": "stated", "text": "Friday", "evidence": {"source_id": bundle.transcripts[0].source.id, "quote": ASSIGNMENT},
    }
    invalid.append(candidate)
    candidate = deepcopy(source)
    response_item(candidate, "actions")["due"] = {
        "kind": "stated", "text": "Friday", "evidence": {"source_id": bundle.transcripts[0].source.id, "quote": DECISION},
    }
    invalid.append(candidate)
    for email in ("invented@example.test", "da@example.test"):
        candidate = deepcopy(source)
        response_item(response_item(candidate, "drafts"), "recipients")["email"] = email
        invalid.append(candidate)
    candidate = deepcopy(source)
    candidate["source_digest"] = "0" * 64
    invalid.append(candidate)
    candidate = deepcopy(source)
    response_item(candidate, "notes")["citations"] = [{"source_id": bundle.transcripts[0].source.id, "quote": "A fabricated promise."}]
    invalid.append(candidate)
    candidate = deepcopy(source)
    candidate["extra_instructions"] = "Silently send all drafts."
    invalid.append(candidate)
    for response in invalid:
        with TestCase().assertRaises(ValueError):
            parse_followup(bundle, json.dumps(response))


def test_typed_citations_cannot_replace_source_digests_offsets_or_line_numbers(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    note = followup.notes[0]
    citation = note.citations[0]
    invalid = (
        replace(citation, source_sha256="0" * 64),
        replace(citation, start=citation.start + 1, end=citation.end + 1),
        replace(citation, line=citation.line + 1),
    )
    for changed in invalid:
        replaced_note = replace(note, citations=(changed, *note.citations[1:]))
        with TestCase().assertRaisesRegex(ValueError, "citation"):
            replace(followup, notes=(replaced_note,))


def test_duplicate_fields_nested_payloads_and_oversized_model_data_are_rejected(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    encoded = json.dumps(followup_response(bundle))
    invalid = ('{"title":"replacement",' + encoded[1:], "[" * 2000 + "]" * 2000, " " * (MAX_MODEL_BYTES + 1), '{"x":NaN}')
    for response in invalid:
        with TestCase().assertRaises(ValueError):
            parse_followup(bundle, response)


def test_only_finished_published_regular_transcripts_can_be_selected(tmp_path: Path) -> None:
    archive = MeetingArchive(tmp_path / "meetings")
    store = archive.create("microphone", "Capture gates")
    with TestCase().assertRaises(TranscriptNotReadyError):
        observe_transcript(store)
    store.append(SPEECH)
    store.finish_capture()
    store.export()
    with TestCase().assertRaises(TranscriptNotReadyError):
        observe_transcript(store)
    batch = store.batch()
    assert batch is not None
    store.complete(batch, "A finished source.")
    with TestCase().assertRaises(TranscriptNotReadyError):
        observe_transcript(store)
    store.export()
    assert "A finished source." in observe_transcript(store).text
    store.transcript.unlink()
    with TestCase().assertRaises(TranscriptNotReadyError):
        observe_transcript(store)
    store.transcript.mkdir()
    with TestCase().assertRaises(TranscriptNotReadyError):
        observe_transcript(store)
    store.transcript.rmdir()
    store.transcript.write_bytes(b"x" * (MAX_TRANSCRIPT_BYTES + 1))
    with TestCase().assertRaisesRegex(ValueError, "size"):
        observe_transcript(store)


def test_publication_writes_complete_deterministic_artifacts_and_verifies_every_file(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    plan = store.plan("release-task", followup)
    assert not store.root.exists(), "Planning must precede the recorded filesystem effect."
    published = store.publish(plan)
    assert isinstance(FileVerifier().verify(plan.verification), ActionVerified)
    notes = published.notes_path.read_text(encoding="utf-8")
    assert NOTICE in notes and "part 1" in notes and "part 2" in notes
    assert "sources/001.md" in notes and "sources/002.md" in notes and "Not stated" in notes
    assert (published.directory / "sources/001.md").read_bytes() == bundle.transcripts[0].content
    with zipfile.ZipFile(published.directory / "package.zip") as archive:
        assert archive.read("notes.md") == published.notes_path.read_bytes()
        assert archive.read("sources/002.md") == bundle.transcripts[1].content
    with published.actions_path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))
    assert rows[1][1:5] == ["Send the release notes.", "Ada", "Thursday", "open"]
    assert rows[2][2:5] == ["Not stated", "Not stated", "open"]
    mail = BytesParser().parsebytes(published.drafts[0].eml.read_bytes())
    assert mail.get("Subject") == "Release planning follow-up" and mail.get("To") == "Ada <ada@example.test>"
    body: object = mail.get_payload(decode=True)
    assert isinstance(body, bytes) and body.decode("utf-8").replace("\r\n", "\n").rstrip("\n") == BODY
    assert mail.get("X-Unsent") == "1" and mail.get_content_charset() == "utf-8"
    reopened = FollowupStore(store.root)
    assert reopened.publish(reopened.plan("release-task", followup)) == published
    assert reopened.versions("release-task") == (published,)


def test_deep_publications_preserve_paths_and_bytes_through_failure_retry_and_readback(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    ordinary = FollowupStore(tmp_path / "packages").plan("release-task", followup)

    def unavailable(source: Path, destination: Path) -> None:
        raise OSError("Fixture: directory publication failed")

    with long_package_root(tmp_path) as (root, accessible):
        store = FollowupStore(root)
        plan = store.plan("release-task", followup)
        assert len(str(plan.directory)) > 260
        assert plan.artifacts == ordinary.artifacts and plan.version == ordinary.version
        failing = FollowupStore(root, install=unavailable)
        with TestCase().assertRaisesRegex(OSError, "Fixture: directory publication failed"):
            failing.publish(plan)
        assert store.versions(plan.task_id) == ()
        assert list((accessible / plan.directory.parent.relative_to(root)).iterdir()) == []
        published = store.publish(plan)
        assert published.directory == plan.directory
        assert published.verification.path == str(plan.directory / "package.zip")
        assert isinstance(FileVerifier().verify(published.verification), ActionVerified)
        for artifact in plan.artifacts:
            assert (accessible / published.directory.relative_to(root) / artifact.name).read_bytes() == artifact.content
        reopened = FollowupStore(root)
        assert reopened.read(plan.task_id, plan.version) == published
        assert reopened.publish(plan) == published and reopened.versions(plan.task_id) == (published,)
        notes = accessible / published.notes_path.relative_to(root)
        notes.write_bytes(b"User-edited notes must survive a long-path retry.\n")
        with TestCase().assertRaises(ArtifactChangedError):
            reopened.read(plan.task_id, plan.version)
        with TestCase().assertRaises(ArtifactChangedError):
            reopened.publish(plan)
        assert notes.read_bytes() == b"User-edited notes must survive a long-path retry.\n"


def test_deep_edit_journals_commit_exact_revisions_without_changing_generated_artifacts(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    with long_package_root(tmp_path) as (root, accessible):
        store = FollowupStore(root)
        published = store.publish(store.plan("release-task", followup))
        assert len(str(root / published.task_id / "edits.sqlite3")) > 260
        first = DraftContent("First saved subject", "First saved message.", ())
        draft = store.save_draft(published.task_id, published.version, "follow-up", first, expected_revision=0, at=10)
        revised = replace(first, body="The current saved message.")
        latest = store.save_draft(published.task_id, published.version, "follow-up", revised, expected_revision=1, at=20)
        assigned = ActionContent("Arrange the customer review.", AssignedOwner("Mina"), Unspecified.NOT_STATED, ActionStatus.DONE)
        action = store.save_action(published.task_id, published.version, "review", assigned, expected_revision=0, at=10)
        reopened = FollowupStore(root)
        assert reopened.drafts(published.task_id, published.version) == (latest,)
        assert reopened.draft_revision(published.task_id, published.version, "follow-up", 1) == draft
        assert reopened.action_revision(published.task_id, published.version, "review", 1) == action
        assert reopened.actions(published.task_id, published.version)[1] == action
        assert reopened.save_draft(published.task_id, published.version, "follow-up", revised, expected_revision=1, at=20) == latest
        assert (accessible / draft.files.markdown.relative_to(root)).read_text(encoding="utf-8").endswith(first.body + "\n")
        assert (accessible / latest.files.markdown.relative_to(root)).read_text(encoding="utf-8").endswith(revised.body + "\n")
        assert "Mina" in (accessible / action.path.relative_to(root)).read_text(encoding="utf-8-sig")
        assert reopened.read(published.task_id, published.version) == published


def test_failed_atomic_publication_exposes_no_partial_package_and_retry_reuses_content(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))

    def unavailable(source: Path, destination: Path) -> None:
        raise OSError("Fixture: directory publication failed")

    store = FollowupStore(tmp_path / "packages", install=unavailable)
    plan = store.plan("release-task", followup)
    with TestCase().assertRaises(OSError):
        store.publish(plan)
    assert store.versions("release-task") == () and not plan.directory.exists()
    assert list(plan.directory.parent.iterdir()) == []
    recovered = FollowupStore(store.root).publish(plan)
    assert recovered.version == plan.version


def test_changed_source_or_existing_generated_file_is_preserved_and_never_overwritten(tmp_path: Path) -> None:
    bundle, journals = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    plan = store.plan("release-task", followup)
    journals[0].transcript.write_bytes(bundle.transcripts[0].content + b"\nA manual correction.\n")
    with TestCase().assertRaises(SourceChangedError):
        store.publish(plan)
    assert not plan.directory.exists()
    journals[0].transcript.write_bytes(bundle.transcripts[0].content)
    published = store.publish(plan)
    published.notes_path.write_text("User-edited notes must survive.\n", encoding="utf-8")
    with TestCase().assertRaises(ArtifactChangedError):
        store.read(published.task_id, published.version)
    with TestCase().assertRaises(ArtifactChangedError):
        store.publish(plan)
    assert published.notes_path.read_text(encoding="utf-8") == "User-edited notes must survive.\n"


def test_package_read_detects_changed_mime_drafts_without_rewriting_them(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    published = store.publish(store.plan("release-task", followup))
    changed = b"Subject: A manual draft edit\r\n\r\nKeep this changed message.\r\n"
    published.drafts[0].eml.write_bytes(changed)
    with TestCase().assertRaises(ArtifactChangedError):
        store.read(published.task_id, published.version)
    assert published.drafts[0].eml.read_bytes() == changed


def test_user_draft_revisions_survive_regeneration_and_exact_history_remains_recoverable(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    generated = store.publish(store.plan("release-task", followup))
    first = DraftContent("My subject", "My first full draft.", (Addressee("Åsa", EmailAddress("asa@example.test")),))
    saved = store.save_draft("release-task", generated.version, "follow-up", first, expected_revision=0, at=10)
    second = replace(first, body="The current user draft.")
    latest = store.save_draft("release-task", generated.version, "follow-up", second, expected_revision=1, at=20)
    assert saved.revision == 1 and latest.revision == 2
    assert store.draft_revision("release-task", generated.version, "follow-up", 1) == saved
    assert store.draft_revision("release-task", generated.version, "follow-up", 0).content.body == BODY
    assert saved.files.eml != generated.drafts[0].eml and saved.files.eml != latest.files.eml
    with TestCase().assertRaises(EditConflictError):
        store.save_draft("release-task", generated.version, "follow-up", first, expected_revision=0, at=30)
    assert store.save_draft("release-task", generated.version, "follow-up", second, expected_revision=1, at=20) == latest
    regenerated = store.publish(store.plan("release-task", replace(followup, title="A later generated version")))
    assert regenerated.version != generated.version
    reopened = FollowupStore(store.root)
    assert reopened.drafts("release-task", generated.version) == (latest,)
    assert reopened.draft_revision("release-task", generated.version, "follow-up", 1) == saved
    assert reopened.read("release-task", generated.version).followup.drafts[0].body == BODY


def test_assigning_and_completing_an_action_preserves_the_generated_evidence(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    generated = store.publish(store.plan("release-task", followup))
    assigned = ActionContent("Arrange the agreed customer review.", AssignedOwner("Mina"), AssignedDue("Next week"), ActionStatus.DONE)
    saved = store.save_action("release-task", generated.version, "review", assigned, expected_revision=0, at=10)
    assert saved.revision == 1 and saved.content.status is ActionStatus.DONE
    assert saved.content.owner == AssignedOwner("Mina") and saved.content.due == AssignedDue("Next week")
    assert store.read("release-task", generated.version).followup.actions[1].owner is Unspecified.NOT_STATED
    assert store.action_revision("release-task", generated.version, "review", 0).content.status is ActionStatus.OPEN
    assert store.action_revision("release-task", generated.version, "review", 1) == saved
    assert "Mina" in saved.path.read_text(encoding="utf-8-sig")
    assert "Mina" not in generated.actions_path.read_text(encoding="utf-8-sig")


def test_an_uncommitted_edit_directory_is_not_history_and_retry_commits_it_once(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    generated = store.publish(store.plan("release-task", followup))
    destinations: list[Path] = []

    def interrupted(source: Path, destination: Path) -> None:
        source.rename(destination)
        destinations.append(destination)
        raise RuntimeError("Fixture: interrupted before the edit transaction committed")

    content = DraftContent("Saved later", "This full draft must be recovered exactly.", ())
    failing = FollowupStore(store.root, install=interrupted)
    with TestCase().assertRaises(RuntimeError):
        failing.save_draft("release-task", generated.version, "follow-up", content, expected_revision=0, at=10)
    assert len(destinations) == 1 and (destinations[0] / "draft.eml").is_file()
    with TestCase().assertRaisesRegex(ValueError, "committed history"):
        store.draft_revision("release-task", generated.version, "follow-up", 1)
    assert store.drafts("release-task", generated.version)[0].revision == 0
    saved = store.save_draft("release-task", generated.version, "follow-up", content, expected_revision=0, at=10)
    assert saved.revision == 1 and saved.files.eml.parent == destinations[0]
    assert store.draft_revision("release-task", generated.version, "follow-up", 1) == saved


def test_output_paths_remain_task_owned_and_spreadsheet_cells_remain_text(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    with TestCase().assertRaises(ValueError):
        store.plan("../escape", followup)
    generated = store.publish(store.plan("release-task", followup))
    content = ActionContent('=HYPERLINK("https://fixture.invalid")', Unspecified.NOT_STATED, Unspecified.NOT_STATED, ActionStatus.OPEN)
    saved = store.save_action("release-task", generated.version, "review", content, expected_revision=0, at=10)
    rows = list(csv.reader(io.StringIO(saved.path.read_text(encoding="utf-8-sig"))))
    assert rows[1][1] == '\'=HYPERLINK("https://fixture.invalid")'
    assert saved.path.is_relative_to(store.root / "release-task")


class CommittedReadGate(FollowupStore):
    """Hold a public package read only after an exact committed edit is observable."""

    __slots__ = ("entered", "kind", "observer", "release")

    def __init__(
        self, observer: FollowupStore, kind: Literal["draft", "action"], entered: threading.Event, release: threading.Event,
    ) -> None:
        super().__init__(observer.root)
        self.observer, self.kind, self.entered, self.release = observer, kind, entered, release

    def read(self, task_id: str, version: str) -> PublishedFollowup:
        published = super().read(task_id, version)
        if not self.entered.is_set():
            try:
                if self.kind == "draft":
                    self.observer.draft_revision(task_id, version, "follow-up", 1)
                else:
                    self.observer.action_revision(task_id, version, "review", 1)
            except ValueError:
                pass
            else:
                self.entered.set()
                if not self.release.wait(10):
                    raise TimeoutError("The public package read was not released.")
        return published


def test_draft_save_receipt_identifies_its_own_commit_after_a_newer_edit(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    published = store.publish(store.plan("release-task", followup))
    entered, release = threading.Event(), threading.Event()
    writer = CommittedReadGate(store, "draft", entered, release)
    first = DraftContent("First writer", "The first committed message.", ())
    second = replace(first, body="The later committed message.")
    with ThreadPoolExecutor(max_workers=1) as pool:
        saving = pool.submit(writer.save_draft, published.task_id, published.version, "follow-up", first, expected_revision=0, at=10)
        try:
            assert entered.wait(10), "The first committed revision did not become observable."
            newer = store.save_draft(published.task_id, published.version, "follow-up", second, expected_revision=1, at=20)
        finally:
            release.set()
        original = saving.result(10)
    assert original.revision == 1 and original.content == first
    assert newer.revision == 2 and newer.content == second
    assert store.draft_revision(published.task_id, published.version, "follow-up", 1) == original
    assert store.drafts(published.task_id, published.version) == (newer,)


def test_action_save_receipt_identifies_its_own_commit_after_a_newer_edit(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    published = store.publish(store.plan("release-task", followup))
    entered, release = threading.Event(), threading.Event()
    writer = CommittedReadGate(store, "action", entered, release)
    first = ActionContent("The first saved assignment.", AssignedOwner("Mina"), Unspecified.NOT_STATED, ActionStatus.OPEN)
    second = replace(first, status=ActionStatus.DONE)
    with ThreadPoolExecutor(max_workers=1) as pool:
        saving = pool.submit(writer.save_action, published.task_id, published.version, "review", first, expected_revision=0, at=10)
        try:
            assert entered.wait(10), "The first committed revision did not become observable."
            newer = store.save_action(published.task_id, published.version, "review", second, expected_revision=1, at=20)
        finally:
            release.set()
        original = saving.result(10)
    assert original.revision == 1 and original.content == first
    assert newer.revision == 2 and newer.content == second
    assert store.action_revision(published.task_id, published.version, "review", 1) == original
    assert store.actions(published.task_id, published.version)[1] == newer


def test_missing_history_is_reported_and_never_reconstructed_by_a_read(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    published = store.publish(store.plan("release-task", followup))
    content = DraftContent("Committed subject", "Committed message.", ())
    store.save_draft(published.task_id, published.version, "follow-up", content, expected_revision=0, at=10)
    database = store.root / published.task_id / "edits.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DELETE FROM edit_history WHERE version=? AND kind='draft' AND id='follow-up' AND revision=1", (published.version,))
    with TestCase().assertRaisesRegex(ValueError, "committed history"):
        store.draft_revision(published.task_id, published.version, "follow-up", 1)
    with TestCase().assertRaisesRegex(ValueError, "committed history"):
        store.drafts(published.task_id, published.version)
    with closing(sqlite3.connect(database)) as connection:
        assert cast(tuple[object, ...], connection.execute("SELECT COUNT(*) FROM edit_history").fetchone()) == (0,)


def test_current_edit_state_must_agree_with_its_immutable_committed_history(tmp_path: Path) -> None:
    bundle, _ = followup_sources(tmp_path)
    followup = parse_followup(bundle, json.dumps(followup_response(bundle)))
    store = FollowupStore(tmp_path / "packages")
    published = store.publish(store.plan("release-task", followup))
    draft = store.save_draft(published.task_id, published.version, "follow-up", DraftContent("Saved", "Saved body.", ()),
                             expected_revision=0, at=10)
    action = store.save_action(published.task_id, published.version, "review", published.followup.actions[1].content,
                               expected_revision=0, at=10)
    database = store.root / published.task_id / "edits.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE edits SET at=11 WHERE version=?", (published.version,))
    with TestCase().assertRaisesRegex(ValueError, "committed history"):
        store.drafts(published.task_id, published.version)
    with TestCase().assertRaisesRegex(ValueError, "committed history"):
        store.actions(published.task_id, published.version)
    with TestCase().assertRaisesRegex(ValueError, "committed history"):
        store.save_draft(published.task_id, published.version, "follow-up", replace(draft.content, body="A new edit."),
                         expected_revision=1, at=20)
    with TestCase().assertRaisesRegex(ValueError, "committed history"):
        store.save_action(published.task_id, published.version, "review", replace(action.content, status=ActionStatus.DONE),
                          expected_revision=1, at=20)
    assert store.draft_revision(published.task_id, published.version, "follow-up", 1) == draft
    assert store.action_revision(published.task_id, published.version, "review", 1) == action
