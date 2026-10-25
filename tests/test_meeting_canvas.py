"""Meeting canvas projection and saves through real workflow and package owners."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase

from assistant import canvas_model as m
from assistant.llm import Json
from assistant.meeting_canvas import (
    ActionCard,
    ChangedSource,
    DraftCard,
    NotesCard,
    Readiness,
    SourceCard,
    SourceState,
    TaskCard,
    Unconfirmed,
    change_of,
    project,
)
from assistant.meeting_followup import ActionContent, ActionStatus, AssignedDue, AssignedOwner, DraftContent, action_data, draft_data
from assistant.meeting_followup_store import PublishedFollowup, observe_transcript
from assistant.meeting_workflow import ActionChange, ActionSaved, DraftChange, DraftSaved, ObservedWork, WorkUnavailable
from automation.files import filesystem_path
from automation.task import Completed

from .test_meeting_events import saved_meeting
from .test_meeting_workflow import WorkflowRig, publish_package


@contextmanager
def completed_meeting_canvas(folder: Path) -> Generator[tuple[WorkflowRig, PublishedFollowup]]:
    rig = WorkflowRig(folder)
    try:
        journals = tuple(saved_meeting(rig.archive, f"🚀 Shipping review, part {part}") for part in (1, 2, 3))
        rig.workflow.prepare(tuple(observe_transcript(journal).source for journal in journals))
        progress = rig.settle().preparations[0]
        published = publish_package(rig, progress.request)
        claim = rig.work.tasks.claim("canvas-meeting-worker", "canvas-meeting-run", at=rig.work.clock())
        assert claim is not None
        rig.work.tasks.finish(claim.run, Completed("Saved and verified the selected meeting package.", ()), at=rig.work.clock())
        rig.settle()
        yield rig, published
    finally:
        rig.close()


def test_verified_meeting_becomes_source_linked_cards_without_replacing_saved_revisions(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        snapshot = rig.workflow.snapshot()
        with TestCase().assertRaisesRegex(ValueError, "task"):
            project(snapshot, "unrelated-task", published.version)
        with TestCase().assertRaisesRegex(ValueError, "task"):
            change_of(snapshot, {
                "kind": "draft", "task_id": "unrelated-task", "version": published.version, "draft_id": "followup",
                "expected_revision": 0, "attempt_id": "foreign-task-first", "content": draft_data(published.followup.drafts[0].content),
            })
        canvas = project(snapshot, published.task_id, published.version)
        assert canvas.readiness is Readiness.VERIFIED and len(canvas.cards) == 7
        sources = tuple(card for card in canvas.cards if isinstance(card, SourceCard))
        assert tuple(card.transcript for card in sources) == published.followup.sources.transcripts
        assert all(card.state is SourceState.CURRENT for card in sources)
        assert all(card.reference.source_sha256 == card.transcript.source.sha256 for card in sources)
        notes = next(card for card in canvas.cards if isinstance(card, NotesCard))
        action = next(card for card in canvas.cards if isinstance(card, ActionCard))
        draft = next(card for card in canvas.cards if isinstance(card, DraftCard))
        task = next(card for card in canvas.cards if isinstance(card, TaskCard))
        assert isinstance(task.work, ObservedWork) and isinstance(task.work.task.state, Completed)
        assert task.reference.task_id == published.task_id
        assert draft.actions == (action.item.id,)
        assert action.value.revision == draft.value.revision == 0
        assert draft.value.content.body == published.followup.drafts[0].body
        assert action.reference.action_id == "ship" and draft.reference.draft_id == "followup"
        for link in (*notes.notes[0].citations, *action.citations, *draft.citations):
            source = next(card for card in sources if card.item.id == link.card_id)
            assert source.reference == link.reference
            assert source.transcript.text[link.citation.start:link.citation.end] == link.citation.quote
            assert source.reference.source_sha256 == link.citation.source_sha256
        assert len({item.id for item in canvas.items}) == 7 and all(len(item.id) < 128 for item in canvas.items)
        assert all(isinstance(item.content, m.Source) for item in canvas.items)
        assert project(snapshot, published.task_id, published.version) == canvas


def test_canvas_saves_use_meeting_revisions_and_preserve_card_arrangements_and_exact_receipts(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        snapshot = rig.workflow.snapshot()
        before = project(snapshot, published.task_id, published.version)
        original = next(card for card in before.cards if isinstance(card, DraftCard))
        arranged = m.Canvas()
        for item in before.items:
            arranged = m.observe(arranged, item)
        arranged = m.place(arranged, m.Placement(original.item.id, 777, 333, 500, 400))
        assert m.edit(arranged, original.item.id, "This generic canvas edit must not replace the saved draft.") == arranged
        draft_content = DraftContent("My saved subject", "My complete revised body.\n\nKeep all of this text.", ())
        draft = change_of(snapshot, {
            "kind": "draft", "task_id": published.task_id, "version": published.version, "draft_id": "followup",
            "expected_revision": 0, "attempt_id": "draft-first", "content": draft_data(draft_content),
        })
        action_content = ActionContent("Ship the reviewed release.", AssignedOwner("Ada"), AssignedDue("Next week"), ActionStatus.DONE)
        action = change_of(snapshot, {
            "kind": "action", "task_id": published.task_id, "version": published.version, "action_id": "ship",
            "expected_revision": 0, "attempt_id": "action-first", "content": action_data(action_content),
        })
        assert isinstance(draft, DraftChange) and isinstance(action, ActionChange)
        rig.workflow.save_draft(draft)
        rig.workflow.save_action(action)
        after = project(rig.refresh(), published.task_id, published.version)
        saved_draft = next(card for card in after.cards if isinstance(card, DraftCard))
        saved_action = next(card for card in after.cards if isinstance(card, ActionCard))
        assert saved_draft.value.revision == saved_action.value.revision == 1
        assert saved_draft.value.content == draft_content and saved_action.value.content == action_content
        assert saved_draft.item.id == original.item.id
        assert any(isinstance(receipt, DraftSaved) and receipt.change == draft and receipt.revision == saved_draft.value
                   for receipt in after.receipts)
        assert any(isinstance(receipt, ActionSaved) and receipt.change == action and receipt.revision == saved_action.value
                   for receipt in after.receipts)
        refreshed = arranged
        for item in after.items:
            refreshed = m.observe(refreshed, item)
        assert refreshed.spaces == arranged.spaces
        assert rig.followups.draft_revision(published.task_id, published.version, "followup", 1) == saved_draft.value
        assert rig.followups.action_revision(published.task_id, published.version, "ship", 1) == saved_action.value


def test_unavailable_observations_keep_retained_cards_and_refuse_unverified_package_edits(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        before = project(rig.workflow.snapshot(), published.task_id, published.version)
        retained = published.directory.with_name(".unavailable-package")
        filesystem_path(published.directory).rename(filesystem_path(retained))
        try:
            snapshot = rig.refresh()
            unavailable = project(snapshot, published.task_id, published.version)
            assert isinstance(unavailable.readiness, Unconfirmed)
            assert unavailable.cards == before.cards
            assert unavailable.readiness.reason in unavailable.problems
            with TestCase().assertRaisesRegex(ValueError, "confirmed"):
                change_of(snapshot, {
                    "kind": "draft", "task_id": published.task_id, "version": published.version, "draft_id": "followup",
                    "expected_revision": 0, "attempt_id": "unavailable-edit", "content": draft_data(DraftContent("My edit", "Keep this body.", ())),
                })
        finally:
            filesystem_path(retained).rename(filesystem_path(published.directory))
        assert project(rig.refresh(), published.task_id, published.version).readiness is Readiness.VERIFIED
        rig.work.bridge.online = False
        try:
            disconnected = project(rig.settle(), published.task_id, published.version)
            task = next(card for card in disconnected.cards if isinstance(card, TaskCard))
            assert isinstance(task.work, WorkUnavailable) and task.work.previous is not None
            assert task.work.reason in disconnected.problems
            assert disconnected.reference == before.reference
        finally:
            rig.work.bridge.online = True


def test_source_changes_and_read_failures_keep_the_exact_cited_transcript_version(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        transcript = published.followup.sources.transcripts[0]
        source_path = filesystem_path(Path(transcript.source.path))
        try:
            source_path.write_bytes(transcript.content + b"\nA later correction in the original file.\n")
            changed = project(rig.refresh(), published.task_id, published.version)
            card = next(card for card in changed.cards if isinstance(card, SourceCard) and card.reference.source_id == transcript.source.id)
            assert isinstance(card.state, ChangedSource) and card.state.observed.sha256 != transcript.source.sha256
            assert card.transcript == transcript and m.body(card.item.content) == transcript.text
            assert changed.readiness is Readiness.VERIFIED
            source_path.write_bytes(b"\xffinvalid UTF-8")
            unavailable = project(rig.refresh(), published.task_id, published.version)
            retained = next(value for value in unavailable.cards if isinstance(value, SourceCard) and value.reference == card.reference)
            assert retained.state is SourceState.UNCONFIRMED and unavailable.problems
            assert retained.transcript == transcript
        finally:
            source_path.write_bytes(transcript.content)


def test_save_attempts_and_old_package_versions_cannot_be_retargeted_or_acknowledged_as_new_edits(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        content = DraftContent("Saved subject", "The first user revision.", ())
        payload: dict[str, Json] = {
            "kind": "draft", "task_id": published.task_id, "version": published.version, "draft_id": "followup",
            "expected_revision": 0, "attempt_id": "saved-once", "content": draft_data(content),
        }
        initial = change_of(rig.workflow.snapshot(), payload)
        assert isinstance(initial, DraftChange)
        rig.workflow.save_draft(initial)
        snapshot = rig.refresh()
        with TestCase().assertRaisesRegex(ValueError, "attempt"):
            change_of(snapshot, payload)
        retry = change_of(snapshot, {**payload, "attempt_id": "retry-exact-content"})
        assert isinstance(retry, DraftChange) and retry.expected_revision == 0
        rig.workflow.save_draft(retry)
        snapshot = rig.refresh()
        original = project(snapshot, published.task_id, published.version)
        saved = next(card for card in original.cards if isinstance(card, DraftCard))
        assert saved.value.revision == 1 and saved.value.content == content
        assert {receipt.change.attempt_id for receipt in original.receipts} == {"saved-once", "retry-exact-content"}
        with TestCase().assertRaisesRegex(ValueError, "changed"):
            change_of(snapshot, {**payload, "attempt_id": "stale-other-content", "content": draft_data(DraftContent("New", "Stale text.", ()))})
        with TestCase().assertRaisesRegex(ValueError, "revision"):
            change_of(snapshot, {**payload, "attempt_id": "future-revision", "expected_revision": 2})
        later = publish_package(rig, snapshot.preparations[0].request, title="A separately generated package")
        snapshot = rig.refresh()
        newer = project(snapshot, published.task_id, later.version)
        new_draft = next(card for card in newer.cards if isinstance(card, DraftCard))
        assert new_draft.value.revision == 0 and new_draft.item.id != saved.item.id and newer.receipts == ()
        assert project(snapshot, published.task_id, published.version).receipts == original.receipts
        old_edit = change_of(snapshot, {
            **payload, "attempt_id": "explicit-old-version", "expected_revision": 1,
            "content": draft_data(DraftContent("Old package edit", "Keep this edit on its selected older package.", ())),
        })
        assert isinstance(old_edit, DraftChange) and old_edit.version == published.version
        rig.workflow.save_draft(old_edit)
        refreshed = rig.refresh()
        older_cards = project(refreshed, published.task_id, published.version).cards
        newer_cards = project(refreshed, published.task_id, later.version).cards
        assert next(card for card in older_cards if isinstance(card, DraftCard)).value.revision == 2
        assert next(card for card in newer_cards if isinstance(card, DraftCard)).value == new_draft.value
        with TestCase().assertRaises(ValueError):
            project(refreshed, published.task_id, "")
        for invalid in (
            {**payload, "attempt_id": "foreign-task", "task_id": "another-task"},
            {**payload, "attempt_id": "foreign-version", "version": "f" * 64},
            {**payload, "attempt_id": "foreign-draft", "draft_id": "another-draft"},
            {**payload, "attempt_id": "boolean-revision", "expected_revision": True},
            {**payload, "attempt_id": "extra-fields", "send": True},
            {**payload, "attempt_id": ""},
        ):
            with TestCase().assertRaises(ValueError):
                change_of(refreshed, invalid)
