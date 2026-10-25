"""Verified canvas handoffs through their public owner and real draft artifacts."""

import queue
import threading
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from assistant import canvas_model as m
from assistant.canvas_drafts import CanvasDrafts, DraftSnapshot, FailedDraft, PendingDraft, SavedDraft, mail_request
from assistant.canvas_store import CanvasStore
from assistant.outlook import (
    Cancelled,
    DraftConflict,
    DraftContent,
    DraftReadback,
    DraftUncertain,
    OutlookDrafts,
    PowerShellOutlook,
    SavedEmlDraft,
    SavedOutlookDraft,
    draft_key,
    read_eml,
)

from .test_outlook import GatedCommand, adapter


def draft_canvas(body: str = "Please review the budget before the launch.\n") -> m.Canvas:
    source = m.Evidence("comparison", 3, "Launch comparison", "The plan exceeds the cap by 40,000 kr.", m.LocalSource.NOTE)
    return m.add(m.Canvas(), m.Item("launch-draft", "Launch follow-up", m.Draft(body, source), revision=7))


def completion(events: queue.Queue[DraftSnapshot], attempt_id: str) -> SavedDraft | FailedDraft:
    while True:
        snapshot = events.get(timeout=10)
        receipt = next((item for item in snapshot.receipts if item.attempt.attempt_id == attempt_id), None)
        if isinstance(receipt, (SavedDraft, FailedDraft)):
            return receipt


def test_canvas_handoff_saves_complete_unsent_file_beyond_mailto_limit_with_frozen_source(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    command.configure("unavailable")
    body = "Review the complete budget and scope. Åäö 😀\n" * 300 + "Keep this final line.\n\n"
    canvas = draft_canvas(body)
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        attempt = owner.save_draft("launch-draft", 7, "first-save")
        pending = owner.snapshot()
        assert pending.receipts == (PendingDraft(attempt),)
        assert attempt.draft.content.body == body and attempt.draft.source.revision == 3
        owner.start()
        result = completion(events, "first-save")
        assert isinstance(result, SavedDraft) and result.attempt == attempt
        assert isinstance(result.result, SavedEmlDraft)
        content = read_eml(result.result.path.read_bytes())[2]
        assert content.subject == "Launch follow-up" and content.body == body
        assert content.to == content.cc == content.bcc == ()
        assert owner.snapshot().revision > pending.revision
        assert not command.files() and not errors
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_stale_missing_non_draft_and_invalid_attempts_are_rejected_without_mail_effects(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    canvas = m.add(draft_canvas(), m.Item("note", "A note", m.Note("Keep this note on the canvas.")))
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=lambda _: None)
    try:
        for identity, revision, attempt in (
            ("missing", 7, "unknown-item"), ("note", 1, "not-a-draft"), ("launch-draft", 6, "stale-revision"),
            ("launch-draft", True, "invalid-revision"), ("launch-draft", 7, ""),
        ):
            with TestCase().assertRaises(ValueError):
                owner.save_draft(identity, revision, attempt)
        assert owner.snapshot() == DraftSnapshot() and events.empty()
        assert not drafts.folder.exists() and not command.files()
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_queued_handoff_keeps_accepted_text_and_evidence_while_canvas_edits_create_a_new_revision(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    entered, release = threading.Event(), threading.Event()
    gated = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(GatedCommand(command, entered, release)))
    canvas = draft_canvas("The complete original wording.\n\n")
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(gated, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        owner.start()
        original = owner.save_draft("launch-draft", 7, "original-version")
        assert entered.wait(10)
        canvas = m.edit(canvas, "launch-draft", "My newer wording must stay in the editor.\n")
        canvas = m.place(canvas, m.Placement("launch-draft", 1234, 876, 560, 540))
        canvas = m.with_space(canvas, replace(canvas.space, camera=m.Camera(999, -233, .7), composer="A half-written request"))
        canvas = m.add(canvas, m.Item("comparison", "Newer comparison", m.Note("The gap is now 60,000 kr."), revision=4))
        edited = canvas
        release.set()
        saved = completion(events, original.attempt_id)
        assert isinstance(saved, SavedDraft) and isinstance(saved.result, SavedOutlookDraft)
        native = gated.transport.read(mail_request(original.draft), saved.result.location, threading.Event())
        assert isinstance(native, DraftReadback) and native.content.body == "The complete original wording.\n\n"
        assert saved.attempt.draft.source.text == "The plan exceeds the cap by 40,000 kr."
        changed = owner.save_draft("launch-draft", 8, "edited-version")
        newer = completion(events, changed.attempt_id)
        assert isinstance(newer, SavedDraft) and isinstance(newer.result, SavedOutlookDraft)
        assert newer.result.location != saved.result.location and len(command.files()) == 2
        assert newer.attempt.draft.content.body == "My newer wording must stay in the editor.\n"
        assert canvas == edited and not errors
    finally:
        release.set()
        owner.close()
        assert owner.wait_closed(10)


def test_queued_before_start_keeps_the_accepted_revision_when_the_canvas_changes(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    canvas = draft_canvas("The accepted version seven wording.\n\n")
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        accepted = owner.save_draft("launch-draft", 7, "queued-before-start")
        canvas = m.edit(canvas, "launch-draft", "Version eight must remain in the editor.\n")
        edited = canvas
        assert owner.snapshot().receipts == (PendingDraft(accepted),)
        assert not drafts.folder.exists() and not command.files()
        owner.start()
        saved = completion(events, accepted.attempt_id)
        assert isinstance(saved, SavedDraft) and isinstance(saved.result, SavedOutlookDraft)
        assert saved.attempt == accepted and owner.prepared(saved.result.key) == accepted.draft
        native = drafts.transport.read(mail_request(accepted.draft), saved.result.location, threading.Event())
        assert isinstance(native, DraftReadback) and native.content == accepted.draft.content
        current = canvas.item("launch-draft")
        assert current is not None and current.revision == 8
        assert canvas == edited and len(command.files()) == 1 and not errors
        assert owner.snapshot().receipts == (saved,)
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_editing_back_to_original_wording_saves_a_distinct_revision_and_keeps_both_versions(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    canvas = draft_canvas("Please review the original wording.\n\n")
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        owner.start()
        original = owner.save_draft("launch-draft", 7, "original-wording")
        first = completion(events, original.attempt_id)
        assert isinstance(first, SavedDraft) and isinstance(first.result, SavedOutlookDraft)
        canvas = m.edit(canvas, "launch-draft", "An intermediate version to reconsider.\n")
        canvas = m.edit(canvas, "launch-draft", original.draft.content.body)
        restored = owner.save_draft("launch-draft", 9, "restored-wording")
        assert restored.draft.revision == 9 and original.draft.revision == 7
        assert restored.draft.content == original.draft.content and restored.draft.source == original.draft.source
        second = completion(events, restored.attempt_id)
        assert isinstance(second, SavedDraft) and isinstance(second.result, SavedOutlookDraft)
        assert second.result.key != first.result.key and second.result.location != first.result.location
        assert owner.prepared(first.result.key) == original.draft
        assert owner.prepared(second.result.key) == restored.draft
        retry = owner.save_draft("launch-draft", 9, "restored-wording-retry")
        repeated = completion(events, retry.attempt_id)
        assert isinstance(repeated, SavedDraft) and repeated.result == second.result
        assert owner.snapshot().receipts == (first, second, repeated)
        assert len(command.files()) == 2 and not errors
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_restart_and_double_click_recover_one_uncertain_native_draft_in_its_original_mailbox(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    command.configure("lose_ack")
    canvas = draft_canvas()
    store = CanvasStore(tmp_path / "canvas.json")
    store.save(canvas)
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    first = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=lambda _: None)
    try:
        first.start()
        original = first.save_draft("launch-draft", 7, "lost-acknowledgement")
        uncertain = completion(events, original.attempt_id)
        assert isinstance(uncertain, FailedDraft) and isinstance(uncertain.result, DraftUncertain)
        assert len(command.files()) == 1 and not (drafts.folder / "drafts").exists()
    finally:
        first.close()
        assert first.wait_closed(10)
    command.configure(store="a-different-default-store", folder="different-drafts")
    restored = store.load()
    reopened = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(command))
    second = CanvasDrafts(reopened, lambda: restored, publish=events.put, report_error=lambda _: None)
    try:
        retry = second.save_draft("launch-draft", 7, "retry-after-restart")
        duplicate = second.save_draft("launch-draft", 7, "second-click")
        assert retry.draft == duplicate.draft == original.draft
        second.start()
        recovered = completion(events, retry.attempt_id)
        repeated = completion(events, duplicate.attempt_id)
        assert isinstance(recovered, SavedDraft) and isinstance(recovered.result, SavedOutlookDraft)
        assert isinstance(repeated, SavedDraft) and repeated.result == recovered.result
        assert recovered.result.location.folder.store_id == "store-a"
        assert len(command.files()) == 1 and not (drafts.folder / "drafts").exists()
        assert len(second.snapshot().receipts) == 2
    finally:
        second.close()
        assert second.wait_closed(10)


def test_preparation_precedes_mail_and_recovers_accepted_version_after_canvas_edit_and_restart(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    command.configure("lose_ack")
    entered, release = threading.Event(), threading.Event()
    gated = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(GatedCommand(command, entered, release)))
    canvas = draft_canvas("Accepted wording Åäö 😀\n" * 400 + "The original final line.\n\n")
    store = CanvasStore(tmp_path / "canvas.json")
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    first = CanvasDrafts(gated, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        first.start()
        accepted = first.save_draft("launch-draft", 7, "accepted-before-edit")
        assert entered.wait(10)
        key = draft_key(mail_request(accepted.draft))
        assert first.prepared(key) == accepted.draft, "The accepted version must be durable before the mail effect."
        assert not command.files()
        canvas = m.edit(canvas, "launch-draft", "Revision eight belongs in the current editor.\n")
        store.save(canvas)
        release.set()
        uncertain = completion(events, accepted.attempt_id)
        assert isinstance(uncertain, FailedDraft) and isinstance(uncertain.result, DraftUncertain)
        assert len(command.files()) == 1
    finally:
        release.set()
        first.close()
        assert first.wait_closed(10)

    restored = store.load()
    assert restored.item("launch-draft") == canvas.item("launch-draft")
    command.configure(store="new-default-store", folder="different-drafts")
    reopened = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(command))
    second = CanvasDrafts(reopened, lambda: restored, publish=events.put, report_error=errors.append)
    try:
        prepared = second.prepared(key)
        assert prepared is not None and prepared == accepted.draft and prepared.revision == 7
        assert prepared.source.text == "The plan exceeds the cap by 40,000 kr."
        assert prepared.source.revision == 3 and prepared.source.title == "Launch comparison"
        recovered = reopened.save(mail_request(prepared), threading.Event())
        assert isinstance(recovered, SavedOutlookDraft) and recovered.location.folder.store_id == "store-a"
        assert reopened.save(mail_request(prepared), threading.Event()) == recovered
        assert len(command.files()) == 1 and store.load() == restored and not errors
    finally:
        second.close()
        assert second.wait_closed(10)


def test_preparation_storage_failure_prevents_any_mail_command(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    canvas = draft_canvas()
    blocked = drafts.folder / "canvas-versions"
    blocked.parent.mkdir()
    blocked.write_bytes(b"Keep this existing file.")
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        owner.start()
        attempt = owner.save_draft("launch-draft", 7, "blocked-preparation")
        failed = completion(events, attempt.attempt_id)
        assert isinstance(failed, FailedDraft) and isinstance(failed.result, DraftUncertain)
        assert "No mail save was started" in failed.result.reason and errors
        assert blocked.read_bytes() == b"Keep this existing file."
        assert not (command.root / "calls.jsonl").exists()
        assert not (drafts.folder / "index").exists() and not (drafts.folder / "drafts").exists()
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_changed_corrupt_or_oversized_preparations_are_preserved_and_block_mail(tmp_path: Path) -> None:
    canvas = draft_canvas()
    for failure in ("content", "source", "truncated", "oversized"):
        drafts, command = adapter(tmp_path / failure)
        events: queue.Queue[DraftSnapshot] = queue.Queue()
        errors: list[str] = []
        owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
        try:
            attempt = owner.save_draft("launch-draft", 7, "existing-preparation")
            key = draft_key(mail_request(attempt.draft))
            path = drafts.folder / "canvas-versions" / (key + ".json")
            source = attempt.draft.source
            body = attempt.draft.content.body
            if failure == "content":
                body = "A different saved draft must not be overwritten.\n"
            if failure == "source":
                source = replace(source, text="A different saved source must not be overwritten.")
            item = m.Item("launch-draft", "Launch follow-up", m.Draft(body, source), revision=7)
            CanvasStore(path).save(m.Canvas(items=(item,)))
            if failure == "truncated":
                path.write_bytes(b'{"version":')
            if failure == "oversized":
                path.write_bytes(path.read_bytes() + b" " * 1_000_000)
            before = path.read_bytes()
            with TestCase().assertRaises(ValueError):
                owner.prepared(key)
            owner.start()
            failed = completion(events, attempt.attempt_id)
            assert isinstance(failed, FailedDraft) and isinstance(failed.result, DraftUncertain)
            assert path.read_bytes() == before and errors
            assert not (command.root / "calls.jsonl").exists()
            assert not (drafts.folder / "index").exists() and not (drafts.folder / "drafts").exists()
        finally:
            owner.close()
            assert owner.wait_closed(10)


def test_live_window_preparation_recovers_saved_evidence_with_the_same_mail_identity(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    command.configure("unavailable")
    source = m.Evidence("budget-window", 3, "Budget evidence", "The complete accepted source.\n",
                        m.LiveWindow(76543, 2468, "EXCEL.EXE", "The launch budget"))
    canvas = m.Canvas(items=(m.Item("launch-draft", "Launch follow-up", m.Draft("Please review the budget.\n", source), revision=7),))
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        owner.start()
        attempt = owner.save_draft("launch-draft", 7, "live-source")
        saved = completion(events, attempt.attempt_id)
        assert isinstance(saved, SavedDraft) and isinstance(saved.result, SavedEmlDraft)
        prepared = owner.prepared(saved.result.key)
        expected = replace(attempt.draft, source=replace(source, origin=m.SavedWindow("EXCEL.EXE", "The launch budget")))
        assert prepared is not None and prepared == expected and mail_request(prepared) == mail_request(attempt.draft)
        retry = owner.save_draft("launch-draft", 7, "live-source-retry")
        repeated = completion(events, retry.attempt_id)
        assert isinstance(repeated, SavedDraft) and repeated.result == saved.result
        assert len(tuple(saved.result.path.parent.glob("*.eml"))) == 1 and not errors
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_user_mail_edits_are_preserved_and_identical_failed_retries_keep_separate_receipts(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    canvas = draft_canvas()
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        owner.start()
        original = owner.save_draft("launch-draft", 7, "saved-original")
        saved = completion(events, original.attempt_id)
        assert isinstance(saved, SavedDraft)
        command.edit(DraftContent("Edited in Outlook", "The person's newer text must be retained.\n"))
        edited = command.files()[0].read_bytes()
        second = owner.save_draft("launch-draft", 7, "second-attempt")
        third = owner.save_draft("launch-draft", 7, "third-attempt")
        first_failure, next_failure = completion(events, second.attempt_id), completion(events, third.attempt_id)
        assert isinstance(first_failure, FailedDraft) and isinstance(first_failure.result, DraftConflict)
        assert isinstance(next_failure, FailedDraft) and next_failure.result == first_failure.result
        assert first_failure.attempt.draft == third.draft and first_failure.attempt != third
        assert owner.snapshot().receipts == (saved, first_failure, next_failure)
        assert len(command.files()) == 1 and command.files()[0].read_bytes() == edited
        canvas = m.edit(canvas, "launch-draft", "A newer canvas edit.")
        with TestCase().assertRaisesRegex(ValueError, "attempt identity"):
            owner.save_draft("launch-draft", 8, original.attempt_id)
        assert owner.snapshot().receipts == (saved, first_failure, next_failure) and not errors
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_receipt_history_is_bounded_without_duplicating_the_saved_draft(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    command.configure("unavailable")
    canvas = draft_canvas()
    events: queue.Queue[DraftSnapshot] = queue.Queue()
    errors: list[str] = []
    owner = CanvasDrafts(drafts, lambda: canvas, publish=events.put, report_error=errors.append)
    try:
        owner.start()
        for index in range(130):
            attempt = owner.save_draft("launch-draft", 7, f"save-{index}")
            result = completion(events, attempt.attempt_id)
            assert isinstance(result, SavedDraft) and isinstance(result.result, SavedEmlDraft), (result, errors)
        snapshot = owner.snapshot()
        assert len(snapshot.receipts) == 128
        assert snapshot.receipts[0].attempt.attempt_id == "save-2"
        latest = snapshot.receipts[-1]
        assert latest.attempt.attempt_id == "save-129"
        assert isinstance(latest, SavedDraft) and isinstance(latest.result, SavedEmlDraft)
        assert len(tuple(latest.result.path.parent.glob("*.eml"))) == 1
        assert not command.files() and not errors
    finally:
        owner.close()
        assert owner.wait_closed(10)


def test_queue_is_bounded_and_close_finishes_accepted_attempts_without_reopening_the_closed_canvas(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    canvas = draft_canvas()
    destroyed = threading.Event()

    def read() -> m.Canvas:
        assert not destroyed.is_set(), "A closed canvas must not be consulted by a late save command."
        return canvas

    events: queue.Queue[DraftSnapshot] = queue.Queue()
    owner = CanvasDrafts(drafts, read, publish=events.put, report_error=lambda _: None)
    try:
        attempts = tuple(owner.save_draft("launch-draft", 7, f"queued-{index}") for index in range(32))
        assert owner.save_draft("launch-draft", 7, "queued-0") == attempts[0]
        with TestCase().assertRaisesRegex(ValueError, "queue is full"):
            owner.save_draft("launch-draft", 7, "over-capacity")
        assert tuple(item.attempt for item in owner.snapshot().receipts) == attempts
        owner.close()
        assert owner.wait_closed(10)
        assert all(isinstance(item, FailedDraft) and item.result is Cancelled.BEFORE_EFFECT for item in owner.snapshot().receipts)
        destroyed.set()
        with TestCase().assertRaisesRegex(ValueError, "closing"):
            owner.save_draft("launch-draft", 7, "after-close")
        assert not drafts.folder.exists() and not command.files()
    finally:
        owner.close()
        assert owner.wait_closed(10)
