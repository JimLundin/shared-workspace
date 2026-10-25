"""Meeting JSON and command routing through real temporary workflow owners."""

import json
from pathlib import Path
from typing import cast
from unittest import TestCase

from assistant.llm import Json
from assistant.meeting_canvas import project
from assistant.meeting_followup import ActionContent, ActionStatus, DraftContent, Unspecified, action_data, draft_data
from assistant.meeting_web import catalog, dispatch, payload
from assistant.meeting_workflow import ActionChange, DraftChange
from automation.files import filesystem_path

from .test_meeting_canvas import completed_meeting_canvas
from .test_meeting_workflow import publish_package


def rows(data: dict[str, Json], field: str) -> list[Json]:
    value = data[field]
    assert isinstance(value, list)
    return value


def object_of(value: Json) -> dict[str, Json]:
    assert isinstance(value, dict)
    return value


def card_of(data: dict[str, Json], kind: str) -> dict[str, Json]:
    return next(object_of(value) for value in rows(data, "cards") if object_of(value)["kind"] == kind)


def test_json_keeps_complete_source_links_and_explicit_package_versions(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        first = rig.workflow.snapshot()
        later = publish_package(rig, first.preparations[0].request, title="Another explicit package")
        snapshot = rig.refresh()
        listing = catalog(snapshot)
        packages = tuple(object_of(value) for value in rows(listing, "packages"))
        assert {(value["task_id"], value["version"]) for value in packages} == {
            (published.task_id, published.version), (later.task_id, later.version),
        }
        data = payload(project(snapshot, published.task_id, published.version))
        assert cast(Json, json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))) == data
        assert data["task_id"] == published.task_id and data["version"] == published.version
        assert data["source_digest"] == published.followup.sources.fingerprint
        assert object_of(data["readiness"])["kind"] == "verified" and len(rows(data, "cards")) == 7
        source = card_of(data, "source")
        transcript = published.followup.sources.transcripts[0]
        assert object_of(source["source"])["text"] == transcript.text
        assert object_of(source["source"])["sha256"] == transcript.source.sha256
        assert object_of(source["reference"])["source_sha256"] == transcript.source.sha256
        assert object_of(source["state"])["kind"] == "current"
        notes = card_of(data, "notes")
        statement = object_of(rows(notes, "notes")[0])
        citation = object_of(rows(statement, "citations")[0])
        retained_citation = published.followup.notes[0].citations[0]
        assert "🚀" in transcript.text[:retained_citation.start]
        assert citation["card_id"] == source["id"] and citation["quote"] == "The team decided to ship on Friday."
        assert citation["source_sha256"] == transcript.source.sha256
        assert (citation["start"], citation["end"], citation["line"], citation["offset_unit"]) == (
            retained_citation.start, retained_citation.end, retained_citation.line, "unicode_codepoints",
        )
        action, draft = card_of(data, "action"), card_of(data, "draft")
        assert action["revision"] == draft["revision"] == 0
        assert object_of(action["reference"])["action_id"] == "ship"
        assert object_of(draft["reference"])["draft_id"] == "followup" and rows(draft, "actions") == [action["id"]]
        assert object_of(draft["content"])["body"] == published.followup.drafts[0].body
        task = card_of(data, "task")
        assert object_of(object_of(task["work"])["task"])["state"] == "completed"
        assert rows(data, "receipts") == []


def test_dispatch_queues_exact_changes_and_receipts_report_each_committed_revision(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        snapshot = rig.workflow.snapshot()
        draft: dict[str, Json] = {
            "kind": "draft", "task_id": published.task_id, "version": published.version, "draft_id": "followup",
            "expected_revision": 0, "attempt_id": "web-draft-first", "content": draft_data(DraftContent("First edit", "Keep my first revision.", ())),
        }
        action: dict[str, Json] = {
            "kind": "action", "task_id": published.task_id, "version": published.version, "action_id": "ship",
            "expected_revision": 0, "attempt_id": "web-action-first",
            "content": action_data(ActionContent("Ship this completed release.", Unspecified.NOT_STATED, Unspecified.NOT_STATED, ActionStatus.DONE)),
        }
        for command in (draft, action):
            acknowledgment = dispatch(snapshot, command, save_draft=rig.workflow.save_draft, save_action=rig.workflow.save_action)
            assert acknowledgment == {"kind": "queued", "change": command}
        first = rig.refresh()
        second_draft = {**draft, "expected_revision": 1, "attempt_id": "web-draft-second",
                        "content": draft_data(DraftContent("Second edit", "Keep my complete second revision.", ()))}
        dispatch(first, second_draft, save_draft=rig.workflow.save_draft, save_action=rig.workflow.save_action)
        data = payload(project(rig.refresh(), published.task_id, published.version))
        assert card_of(data, "draft")["revision"] == 2
        assert card_of(data, "action")["revision"] == 1
        receipts = {str(object_of(value)["kind"]) + ":" + str(object_of(object_of(value)["change"])["attempt_id"]): object_of(value)
                    for value in rows(data, "receipts")}
        for command, kind, revision in ((draft, "draft_saved", 1), (action, "action_saved", 1), (second_draft, "draft_saved", 2)):
            receipt = receipts[f"{kind}:{command['attempt_id']}"]
            assert receipt["change"] == command
            saved = object_of(receipt["saved"])
            assert saved["revision"] == revision and saved["content"] == command["content"]
            assert object_of(saved["origin"])["kind"] == "edited"
            if kind == "draft_saved":
                assert saved["draft_id"] == "followup" and object_of(saved["files"])["eml"]
            else:
                assert saved["action_id"] == "ship" and saved["path"]


def test_invalid_and_stale_commands_do_not_enqueue_and_enqueue_failure_is_not_acknowledged(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        snapshot = rig.workflow.snapshot()
        draft: dict[str, Json] = {
            "kind": "draft", "task_id": published.task_id, "version": published.version, "draft_id": "followup",
            "expected_revision": 0, "attempt_id": "validation-draft", "content": draft_data(DraftContent("Saved", "My saved body.", ())),
        }
        action: dict[str, Json] = {
            "kind": "action", "task_id": published.task_id, "version": published.version, "action_id": "ship",
            "expected_revision": 0, "attempt_id": "validation-action",
            "content": action_data(ActionContent("Finished release.", Unspecified.NOT_STATED, Unspecified.NOT_STATED, ActionStatus.DONE)),
        }
        calls: list[DraftChange | ActionChange] = []
        invalid: tuple[object, ...] = (
            None, [], {**draft, "kind": "send"}, {**draft, "version": "f" * 64}, {**draft, "draft_id": "another-draft"},
            {**draft, "expected_revision": True}, {**draft, "content": {}}, {**draft, "extra": "field"},
        )
        for command in invalid:
            with TestCase().assertRaises(ValueError):
                dispatch(snapshot, command, save_draft=calls.append, save_action=calls.append)
        assert calls == []
        for command in (draft, action):
            dispatch(snapshot, command, save_draft=rig.workflow.save_draft, save_action=rig.workflow.save_action)
        current = rig.refresh()
        for command in (
            {**draft, "attempt_id": "stale-draft", "content": draft_data(DraftContent("Stale", "A different old draft.", ()))},
            {**action, "attempt_id": "stale-action", "content": action_data(published.followup.actions[0].content)},
        ):
            with TestCase().assertRaisesRegex(ValueError, "changed"):
                dispatch(current, command, save_draft=calls.append, save_action=calls.append)
        assert calls == []

        def unavailable(change: DraftChange) -> None:
            calls.append(change)
            raise OSError("The workflow queue has closed.")

        with TestCase().assertRaisesRegex(OSError, "queue has closed"):
            dispatch(current, {**draft, "attempt_id": "enqueue-failure"}, save_draft=unavailable, save_action=calls.append)
        assert len(calls) == 1 and isinstance(calls[0], DraftChange) and calls[0].attempt_id == "enqueue-failure"


def test_unavailable_packages_keep_json_and_failed_save_receipts_without_allowing_new_dispatch(tmp_path: Path) -> None:
    with completed_meeting_canvas(tmp_path) as (rig, published):
        snapshot = rig.workflow.snapshot()
        before = payload(project(snapshot, published.task_id, published.version))
        draft: dict[str, Json] = {
            "kind": "draft", "task_id": published.task_id, "version": published.version, "draft_id": "followup",
            "expected_revision": 0, "attempt_id": "missing-draft", "content": draft_data(DraftContent("Unsaved", "Retain this requested edit.", ())),
        }
        action: dict[str, Json] = {
            "kind": "action", "task_id": published.task_id, "version": published.version, "action_id": "ship",
            "expected_revision": 0, "attempt_id": "missing-action",
            "content": action_data(ActionContent("Unsaved action.", Unspecified.NOT_STATED, Unspecified.NOT_STATED, ActionStatus.DONE)),
        }
        retained = published.directory.with_name(".unavailable-web-package")
        filesystem_path(published.directory).rename(filesystem_path(retained))
        try:
            for command in (draft, action):
                assert dispatch(snapshot, command, save_draft=rig.workflow.save_draft, save_action=rig.workflow.save_action)["kind"] == "queued"
            unavailable = rig.refresh()
            data = payload(project(unavailable, published.task_id, published.version))
            assert data["cards"] == before["cards"] and object_of(data["readiness"])["kind"] == "unconfirmed"
            package = object_of(rows(catalog(unavailable), "packages")[0])
            assert package["task_id"] == published.task_id and package["version"] == published.version
            assert object_of(package["readiness"])["kind"] == "unconfirmed"
            receipts = tuple(object_of(value) for value in rows(data, "receipts"))
            assert len(receipts) == 2
            for command, kind in ((draft, "draft_error"), (action, "action_error")):
                receipt = next(value for value in receipts if value["kind"] == kind)
                assert receipt["change"] == command and receipt["reason"] and "saved" not in receipt
            calls: list[DraftChange | ActionChange] = []
            for command in (draft, action):
                with TestCase().assertRaisesRegex(ValueError, "confirmed"):
                    dispatch(unavailable, {**command, "attempt_id": "blocked-new-attempt"}, save_draft=calls.append, save_action=calls.append)
            assert calls == []
        finally:
            filesystem_path(retained).rename(filesystem_path(published.directory))
        restored = payload(project(rig.refresh(), published.task_id, published.version))
        assert restored["cards"] == before["cards"] and object_of(restored["readiness"])["kind"] == "verified"
        assert len(rows(restored, "receipts")) == 2
