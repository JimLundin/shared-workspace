"""Real meeting editors with isolated, immutable source and receipt fixtures."""

import hashlib
import sys
import threading
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

if sys.platform != "win32":
    raise unittest.SkipTest("Native Windows meeting follow-through")

from assistant.canvas_widgets import STYLE
from assistant.meeting_followup import (
    ActionItem,
    ActionStatus,
    AssignedDue,
    AssignedOwner,
    CaptureNotice,
    CitedText,
    EmailAddress,
    EmailDraft,
    Followup,
    NoCaptureNotice,
    Transcript,
    TranscriptBundle,
    TranscriptSource,
    Unspecified,
    resolve_citation,
)
from assistant.meeting_followup_store import ActionRevision, DraftFiles, DraftRevision, Edited, Original, PublishedFollowup
from assistant.meeting_panel import ArtifactKey, DraftEdit, MeetingPanel, choice
from assistant.meeting_requests import PreparedFollowup
from assistant.meeting_workflow import (
    ActionChange,
    ActionSaved,
    AvailablePackages,
    DraftChange,
    DraftSaved,
    DraftSaveProblem,
    FollowupPackage,
    FollowupProgress,
    MeetingSnapshot,
    MeetingWorkflow,
    WorkPending,
)
from assistant.qt import Qt, QtCore
from assistant.qt_ui import application


def package_fixture(folder: Path, version: str = "a" * 64) -> FollowupPackage:
    transcripts: list[Transcript] = []
    for index in range(1, 4):
        content = f"# Recording part {index}\n\nAda will follow up.\n".encode()
        source = TranscriptSource(
            f"segment-{index}", "Meeting", str(folder / f"source-{index}.md"), hashlib.sha256(content).hexdigest(),
            1792659600 + index * 60, 60, "both", CaptureNotice("Microphone gap in this part.") if index == 2 else NoCaptureNotice.NONE,
        )
        transcripts.append(Transcript(source, content))
    sources = TranscriptBundle(tuple(transcripts))
    citations = (resolve_citation(sources, "segment-2", "Ada will follow up."),)
    action = ActionItem("invite", "Arrange the next discussion.", citations, Unspecified.NOT_STATED, Unspecified.NOT_STATED)
    draft = EmailDraft("follow-up", "Next discussion", "Hi Ada,\n\nPlease suggest a time.\n\nThank you.\n", (), citations, (action.id,))
    followup = Followup(sources, "Meeting follow-up", (CitedText("A follow-up was discussed.", citations),), (), (action,), (draft,))
    directory = folder / version
    files = DraftFiles(draft.id, directory / "draft.eml", directory / "draft.md")
    publication = PublishedFollowup("meeting-task", version, directory, followup, (files,))
    return FollowupPackage(publication, (DraftRevision(draft.id, draft.content, Original.GENERATED, files),),
                           (ActionRevision(action.id, action.content, Original.GENERATED, directory / "actions.csv"),))


class WorkflowFixture(MeetingWorkflow):
    __slots__ = ("actions", "drafts", "prepared", "refreshes", "value")

    def __init__(self, packages: tuple[FollowupPackage, ...]) -> None:
        bundle = packages[0].publication.followup.sources
        request = PreparedFollowup("meeting-task", bundle, "Prepare this saved meeting", 1792660000)
        self.value = MeetingSnapshot(tuple(reversed(tuple(item.source for item in bundle.transcripts))),
                                     (FollowupProgress(request, WorkPending.OBSERVATION, AvailablePackages(packages)),))
        self.prepared: list[tuple[TranscriptSource, ...]] = []
        self.drafts: list[DraftChange] = []
        self.actions: list[ActionChange] = []
        self.refreshes = 0

    def snapshot(self) -> MeetingSnapshot:
        return self.value

    def prepare(self, selected: tuple[TranscriptSource, ...]) -> None:
        self.prepared.append(selected)

    def save_draft(self, change: DraftChange) -> None:
        self.drafts.append(change)

    def save_action(self, change: ActionChange) -> None:
        self.actions.append(change)

    def refresh(self) -> threading.Event:
        self.refreshes += 1
        completed = threading.Event()
        completed.set()
        return completed


class NativeMeetings:
    __slots__ = ("canvas", "opened", "package", "root", "view", "workflow")

    def __init__(self, folder: Path, *, multiple: bool = False) -> None:
        self.root = application()
        self.package = package_fixture(folder)
        packages = (self.package, package_fixture(folder, "b" * 64)) if multiple else (self.package,)
        self.workflow = WorkflowFixture(packages)
        self.opened: list[Path] = []
        self.canvas: list[Path] = []
        self.view = MeetingPanel(self.workflow, open_file=self.opened.append, add_to_canvas=self.canvas.append)
        self.view.setStyleSheet(STYLE)
        self.view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.view.setGeometry(-4000, -3000, 1150, 760)
        self.view.show()
        self.root.processEvents()

    def publish(self, snapshot: MeetingSnapshot) -> None:
        self.workflow.value = snapshot
        self.view.render_snapshot(snapshot)
        self.root.processEvents()

    def with_package(self, package: FollowupPackage) -> MeetingSnapshot:
        progress = replace(self.workflow.value.preparations[0], packages=AvailablePackages((package,)))
        return replace(self.workflow.value, preparations=(progress,))

    def close(self) -> None:
        self.view.close()
        self.view.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


@contextmanager
def native_meetings(folder: Path, *, multiple: bool = False) -> Generator[NativeMeetings]:
    panel = NativeMeetings(folder, multiple=multiple)
    try:
        yield panel
    finally:
        panel.close()


def test_selected_segments_submit_only_on_request_in_chronological_order(tmp_path: Path) -> None:
    with native_meetings(tmp_path) as panel:
        view = panel.view
        assert not view.prepare_button.isEnabled() and not panel.workflow.prepared
        for index in (0, 2):
            item = view.recordings.topLevelItem(index)
            assert item is not None
            item.setCheckState(0, Qt.CheckState.Checked)
        panel.publish(panel.workflow.value)
        assert len(view.checked) == 2 and not panel.workflow.prepared
        view.prepare_button.click()
        assert tuple(source.id for source in panel.workflow.prepared[0]) == ("segment-1", "segment-3")


def test_same_title_sources_have_distinct_parts_and_citations_select_exact_saved_lines(tmp_path: Path) -> None:
    with native_meetings(tmp_path) as panel:
        view = panel.view
        labels = tuple(view.source_choice.itemText(index) for index in range(3))
        assert len(set(labels)) == 3 and all(f"Part {index}" in label for index, label in enumerate(labels, 1))
        assert "Part 2 · line 3" in view.notes.toPlainText()
        view.trace_source(QtCore.QUrl("source:segment-2#3"))
        assert view.tabs.currentWidget() is view.source_panel and choice(view.source_choice) == "segment-2"
        assert "Recording part 2" in view.source_text.toPlainText()
        assert view.source_text.textCursor().selectedText().strip() == "Ada will follow up."
        assert "Microphone gap" in view.source_notice.text()
        view.open_notes.click()
        view.canvas_notes.click()
        assert panel.opened == panel.canvas == [panel.package.publication.notes_path]


def test_multiple_versions_require_a_choice_and_preserve_local_edits_per_version(tmp_path: Path) -> None:
    with native_meetings(tmp_path, multiple=True) as panel:
        view = panel.view
        assert view.package() is None and not view.open_notes.isEnabled()
        assert "Choose a saved version" in view.notes.toPlainText()
        assert view.versions.itemText(1) != view.versions.itemText(2)
        view.versions.setCurrentIndex(1)
        view.draft_body.setPlainText("Version A local writing")
        view.versions.setCurrentIndex(2)
        view.draft_body.setPlainText("Version B local writing")
        panel.publish(panel.workflow.value)
        assert view.draft_body.toPlainText() == "Version B local writing"
        view.versions.setCurrentIndex(1)
        assert view.draft_body.toPlainText() == "Version A local writing" and len(view.draft_edits) == 2


def test_draft_recipients_preserve_full_names_and_reject_malformed_nonempty_input() -> None:
    content = DraftEdit("  Next discussion  ", "\n Hi Ada,\n\nThanks. \n", 'Ada Lovelace, "Doe, Jane" <jane@example.test>', 0).content()
    assert content.subject == "  Next discussion  " and content.body == "\n Hi Ada,\n\nThanks. \n"
    assert content.recipients[0].name == "Ada Lovelace" and content.recipients[0].email is Unspecified.NOT_STATED
    assert content.recipients[1].name == "Doe, Jane" and content.recipients[1].email == EmailAddress("jane@example.test")
    for invalid in ('bad@@example.test', 'Ada <', 'Ada <>', 'Ada <oops>', 'Ada <ada@example.test>,',
                    'Ada <ada@example.test>, nonsense@', '"Unclosed name', 'Ada; Bo'):
        with unittest.TestCase().assertRaises(ValueError, msg=invalid):
            DraftEdit("Subject", "Body", invalid, 0).content()


def test_draft_receipts_preserve_typing_during_save_and_ignore_other_revisions(tmp_path: Path) -> None:
    with native_meetings(tmp_path) as panel:
        view = panel.view
        view.subject.setText("  Subject with spaces  ")
        view.draft_body.setPlainText("\nMy complete draft.\n\n")
        view.save_draft_button.click()
        change = panel.workflow.drafts[-1]
        assert change.content.subject == "  Subject with spaces  " and change.content.body == "\nMy complete draft.\n\n"
        assert not view.open_draft_button.isEnabled()
        saved = replace(panel.package.drafts[0], content=change.content, origin=Edited(1, 1792660001))
        panel.publish(replace(panel.workflow.value, changes=(DraftSaved(replace(change, expected_revision=8), saved),)))
        assert view.pending_drafts and view.draft_body.toPlainText() == change.content.body
        view.draft_body.insertPlainText("More writing while saving. ")
        typed = view.draft_body.toPlainText()
        updated = replace(panel.package, drafts=(saved,))
        panel.publish(replace(panel.with_package(updated), changes=(DraftSaved(change, saved),)))
        assert not view.pending_drafts and view.draft_body.toPlainText() == typed
        view.open_draft()
        assert not panel.opened
        view.save_draft_button.click()
        next_change = panel.workflow.drafts[-1]
        assert next_change.expected_revision == 1 and next_change.content.body == typed
        next_saved = replace(saved, content=next_change.content, origin=Edited(2, 1792660002))
        panel.publish(replace(panel.with_package(replace(updated, drafts=(next_saved,))), changes=(DraftSaved(next_change, next_saved),)))
        assert not view.draft_edits and view.draft_status.text() == "Saved"
        view.open_draft_button.click()
        assert panel.opened == [next_saved.files.eml]


def test_failed_save_keeps_the_exact_edit_until_explicit_reload(tmp_path: Path) -> None:
    with native_meetings(tmp_path) as panel:
        view = panel.view
        view.draft_body.setPlainText("My unsaved paragraph.\n")
        view.save_draft_button.click()
        change = panel.workflow.drafts[-1]
        panel.publish(replace(panel.workflow.value, changes=(DraftSaveProblem(change, "The saved draft changed; reload or keep your writing."),)))
        key = ArtifactKey(change.task_id, change.version, change.draft_id)
        assert view.draft_edits[key].body == "My unsaved paragraph.\n" and not view.pending_drafts
        assert "saved draft changed" in view.draft_status.text() and not view.save_draft_button.isEnabled()
        view.reload_draft()
        assert not view.draft_edits and view.draft_body.toPlainText() == panel.package.drafts[0].content.body
        assert panel.workflow.refreshes == 1


def test_retry_of_identical_edit_ignores_the_previous_attempt_receipt(tmp_path: Path) -> None:
    with native_meetings(tmp_path) as panel:
        view = panel.view
        view.draft_body.setPlainText("My writing")
        view.save_draft_button.click()
        first = panel.workflow.drafts[-1]
        failed = replace(panel.workflow.value, changes=(DraftSaveProblem(first, "Temporarily unavailable"),))
        panel.publish(failed)
        view.draft_body.setPlainText("My writing updated")
        view.draft_body.setPlainText("My writing")
        view.save_draft_button.click()
        second = panel.workflow.drafts[-1]
        assert second.content == first.content and second.expected_revision == first.expected_revision
        assert second != first
        panel.publish(failed)
        assert view.pending_drafts and view.draft_status.text() == "Saving…"
        panel.publish(replace(failed, changes=(DraftSaveProblem(second, "Still unavailable"),)))
        assert not view.pending_drafts and view.draft_status.text() == "Still unavailable"
        assert view.draft_body.toPlainText() == "My writing"


def test_action_owner_due_and_completion_save_against_the_selected_revision(tmp_path: Path) -> None:
    with native_meetings(tmp_path) as panel:
        view = panel.view
        view.action_text.setPlainText("Send the invitation.")
        view.owner.setText("Ada")
        view.due.setText("Next week")
        view.done.setChecked(True)
        view.save_action_button.click()
        change = panel.workflow.actions[-1]
        assert change.action_id == "invite" and change.expected_revision == 0
        assert change.content.owner == AssignedOwner("Ada") and change.content.due == AssignedDue("Next week")
        assert change.content.status is ActionStatus.DONE
        saved = replace(panel.package.actions[0], content=change.content, origin=Edited(1, 1792660001))
        panel.publish(replace(panel.with_package(replace(panel.package, actions=(saved,))), changes=(ActionSaved(change, saved),)))
        assert not view.action_edits and view.action_status.text() == "Saved"
