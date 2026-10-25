"""Draft contracts through complete MIME files and a real local command boundary."""

import json
import os
import sys
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import cast
from unittest import TestCase

from assistant import commands
from assistant.outlook import (
    Cancelled,
    DraftConflict,
    DraftContent,
    DraftReadback,
    DraftRequest,
    DraftUncertain,
    Missing,
    OutlookDrafts,
    PowerShellOutlook,
    Recipient,
    SavedEmlDraft,
    SavedOutlookDraft,
    content_data,
    draft_key,
    eml_bytes,
    fingerprint,
    matches,
    read_eml,
)

# This executable fixture replaces only the external Outlook command. It reads
# and writes actual files, and returns the same complete JSON response protocol.
# Tests do not inspect or assert on generated PowerShell text.
_COMMAND = r"""
import hashlib, json, sys
from pathlib import Path
payload = json.load(sys.stdin)
request = payload['request']
root = Path(payload['fixture'])
control = json.loads((root / 'control.json').read_text(encoding='utf-8'))
operation = request['operation']
with (root / 'calls.jsonl').open('a', encoding='utf-8') as calls:
    calls.write(json.dumps({'operation': operation, 'folder': request.get('folder')}) + '\n')
if operation == 'status':
    result = ({'kind': 'unavailable', 'reason': 'Classic Outlook is not running.'} if control['mode'] == 'unavailable'
              else {'kind': 'available', 'folder': {'entry_id': control['folder'], 'store_id': control['store']}})
else:
    (root / 'items').mkdir(exist_ok=True)
    found = []
    for path in (root / 'items').glob('*.json'):
        value = json.loads(path.read_text(encoding='utf-8'))
        same = value['folder'] == request['folder']
        same = same and (value['entry_id'] == request['entry_id'] if operation == 'read' else value['key'] == request['key'])
        if same:
            found.append(value)
    if control['mode'] == 'unavailable':
        result = {'kind': 'unavailable', 'reason': 'The original mailbox is unavailable.'}
    elif len(found) > 1:
        result = {'kind': 'conflict', 'reason': 'Multiple items have this workflow key.'}
    elif found:
        result = found[0]
    elif operation != 'ensure':
        result = {'kind': 'missing'}
    else:
        record = json.loads((Path(payload['index']) / (request['key'] + '.json')).read_text(encoding='utf-8'))
        assert record['fingerprint'] == request['fingerprint'] and record['folder'] == request['folder']
        assert record['kind'] == 'pending_outlook', 'A durable mailbox binding must precede Save.'
        result = {
            'kind': 'draft', 'entry_id': 'item-' + request['key'], 'folder': request['folder'],
            'key': request['key'], 'fingerprint': request['fingerprint'], 'content': request['content'],
            'saved': True, 'sent': False, 'submitted': False,
        }
        name = hashlib.sha256(request['folder']['store_id'].encode()).hexdigest() + '-' + request['key'] + '.json'
        (root / 'items' / name).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        if control['mode'] == 'lose_ack':
            sys.exit(17)
    if result.get('kind') == 'draft':
        if control['mode'] == 'short_body':
            result['content']['body'] = result['content']['body'][:7]
        if control['mode'] == 'extra_bcc':
            result['content']['bcc'].append('unexpected@example.invalid')
        if control['mode'] == 'wrong_store':
            result['folder']['store_id'] = 'unrelated-store'
        if control['mode'] == 'already_sent':
            result['sent'] = True
        if control['mode'] == 'malformed_recipient':
            result['content']['to'] = ['unresolved recipient']
Path(payload['response']).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
"""


class LocalOutlookCommand:
    __slots__ = ("index", "root")

    def __init__(self, root: Path, drafts: Path) -> None:
        self.root, self.index = root, drafts / "index"
        self.root.mkdir(parents=True)
        self.configure()

    def configure(self, mode: str = "normal", *, store: str = "store-a", folder: str = "drafts-a") -> None:
        data: dict[str, object] = {"mode": mode, "store": store, "folder": folder}
        (self.root / "control.json").write_text(json.dumps(data), encoding="utf-8")

    def run(self, request: Mapping[str, object], response: Path, cancel: threading.Event) -> tuple[int, str]:
        payload: dict[str, object] = {
            "request": dict(request), "response": str(response), "fixture": str(self.root), "index": str(self.index),
        }
        return commands.run([sys.executable, "-c", _COMMAND], input_text=json.dumps(payload), timeout_s=10, cancel=cancel)

    def files(self) -> tuple[Path, ...]:
        return tuple(sorted((self.root / "items").glob("*.json")))

    def edit(self, content: DraftContent) -> None:
        path = self.files()[0]
        value = cast("dict[str, object]", json.loads(path.read_bytes()))
        value["content"] = content_data(content)
        path.write_text(json.dumps(value), encoding="utf-8")

    def duplicate(self) -> None:
        path = self.files()[0]
        value = cast("dict[str, object]", json.loads(path.read_bytes()))
        value["entry_id"] = "a-user-created-copy"
        (path.parent / "duplicate.json").write_text(json.dumps(value), encoding="utf-8")


def request(body: str = "Factual notes\n\nPlease review the assigned actions.\n") -> DraftRequest:
    return DraftRequest("meeting-workflow", "administrative-followup-v1", DraftContent("Interview follow-up", body))


def adapter(tmp_path: Path) -> tuple[OutlookDrafts, LocalOutlookCommand]:
    folder = tmp_path / "drafts"
    command = LocalOutlookCommand(tmp_path / "outlook-fixture", folder)
    return OutlookDrafts(folder, transport=PowerShellOutlook(command)), command


def test_content_identity_retains_whitespace_recipient_roles_and_explicit_revision() -> None:
    original = request("First line\nLast line\n\n")
    assert matches(original.content, replace(original.content, body="First line\r\nLast line\r\n\r\n"))
    assert fingerprint(original) == fingerprint(replace(original, content=replace(original.content, body="First line\r\nLast line\r\n\r\n")))
    assert not matches(original.content, replace(original.content, body=original.content.body.rstrip()))
    addressed = replace(original.content, to=(Recipient("Ada@EXAMPLE.invalid"),))
    assert matches(addressed, replace(addressed, to=(Recipient("Ada@example.invalid"),)))
    assert not matches(addressed, replace(addressed, to=(), bcc=addressed.to))
    assert not matches(addressed, replace(addressed, bcc=(Recipient("other@example.invalid"),)))
    assert draft_key(original) == draft_key(replace(original, content=replace(original.content, body="edited")))
    assert fingerprint(original) != fingerprint(replace(original, content=replace(original.content, body="edited")))
    assert draft_key(original) != draft_key(replace(original, draft_id="administrative-followup-v2"))


def test_external_addresses_and_header_injection_are_rejected_before_saving() -> None:
    for address in (
        "guessed name", "a@example.invalid\nBcc: other@example.invalid", "a@example.invalid, b@example.invalid", "Ada <a@example.invalid>",
    ):
        with TestCase().assertRaises(ValueError):
            Recipient(address)
    with TestCase().assertRaises(ValueError):
        DraftContent("Meeting\r\nBcc: other@example.invalid", "text")
    with TestCase().assertRaises(ValueError):
        DraftContent("Meeting", "bad\0body")
    with TestCase().assertRaises(ValueError):
        DraftContent("Meeting", "text", to=(Recipient("same@example.invalid"),), bcc=(Recipient("same@example.invalid"),))


def test_eml_roundtrip_keeps_complete_unicode_body_and_all_recipient_roles() -> None:
    body = 'Åäö, quoted "text", $script and `literal`.\n' * 10_000 + "last character"
    original = DraftRequest("workflow", "draft", DraftContent(
        "Réunion — full notes", body, (Recipient("ada@example.invalid"),), (Recipient("lee@example.invalid"),), (Recipient("audit@example.invalid"),),
    ))
    encoded = eml_bytes(original)
    native_policy = cast("policy.EmailPolicy[EmailMessage[str, str]]", policy.default)
    parsed = BytesParser(policy=native_policy).parsebytes(encoded)
    actual = cast("object", parsed.get_payload(decode=True))
    assert isinstance(actual, bytes) and actual.decode("utf-8") == body
    assert str(parsed["X-Unsent"]) == "1" and str(parsed["Subject"]) == original.content.subject
    key, digest, recovered = read_eml(encoded)
    assert recovered == original.content and (key, digest) == (draft_key(original), fingerprint(original))


def test_eml_subject_preserves_leading_whitespace_and_long_encoded_headers() -> None:
    for subject in ("", " leading", "trailing ", "  multiple  ", "\ttab\t", "A\tB", "x" * 998, "Å" * 998):
        original = DraftRequest("workflow", "draft", DraftContent(subject, "Complete body.\n"))
        key, digest, recovered = read_eml(eml_bytes(original))
        assert recovered == original.content, repr(subject[:20])
        assert (key, digest) == (draft_key(original), fingerprint(original))


def test_unavailable_outlook_saves_one_complete_editable_eml_and_retries_that_file(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    command.configure("unavailable")
    original = request("Complete source-linked notes.\n" * 5_000 + "end")
    saved = drafts.save(original, threading.Event())
    assert isinstance(saved, SavedEmlDraft), saved
    assert "not running" in saved.unavailable.reason and saved.path.is_file()
    assert read_eml(saved.path.read_bytes())[2].body == original.content.body
    before = saved.path.read_bytes()
    identity_before = {path.name: path.read_bytes() for path in (drafts.folder / "index").glob("*.json")}
    command.configure("normal", store="a-new-default-store")
    restarted = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(command))
    assert restarted.inspect(original, threading.Event()) == saved
    assert {path.name: path.read_bytes() for path in (drafts.folder / "index").glob("*.json")} == identity_before
    assert restarted.save(original, threading.Event()) == saved
    assert saved.path.read_bytes() == before and len(tuple((drafts.folder / "drafts").glob("*.eml"))) == 1
    assert not command.files(), "An existing fallback must not migrate into a newly available mailbox."
    assert original.content.body not in "".join(value.decode() for value in identity_before.values())


def test_file_draft_user_edits_and_same_key_collisions_are_preserved(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    command.configure("unavailable")
    original = request()
    saved = drafts.save(original, threading.Event())
    assert isinstance(saved, SavedEmlDraft)
    changed = replace(original, content=replace(original.content, body="The user's edited draft.\n"))
    edited = eml_bytes(changed).replace(fingerprint(changed).encode(), fingerprint(original).encode())
    saved.path.write_bytes(edited)
    assert isinstance(drafts.inspect(original, threading.Event()), DraftConflict)
    assert isinstance(drafts.save(original, threading.Event()), DraftConflict)
    assert isinstance(drafts.save(changed, threading.Event()), DraftConflict)
    assert saved.path.read_bytes() == edited
    saved.path.unlink()
    assert isinstance(drafts.save(original, threading.Event()), DraftConflict)
    assert not saved.path.exists()


def test_verified_retries_keep_the_readable_binding_and_still_check_edited_or_missing_drafts(tmp_path: Path) -> None:
    for mode in ("unavailable", "normal"):
        drafts, command = adapter(tmp_path / mode)
        command.configure(mode)
        original = request()
        saved = drafts.save(original, threading.Event())
        assert isinstance(saved, (SavedEmlDraft, SavedOutlookDraft)), saved
        index = drafts.folder / "index" / (saved.key + ".json")
        binding = index.read_bytes()
        # This reader allows verification. On Windows it denies replacing the
        # file; on other platforms its identity exposes an unnecessary rewrite.
        with index.open("rb") as reader:
            identity = os.fstat(reader.fileno())
            restarted = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(command))
            for _ in range(3):
                assert restarted.inspect(original, threading.Event()) == saved
                retried = restarted.save(original, threading.Event())
                assert retried == saved, retried
                assert os.path.samestat(identity, index.stat()), "A verified retry replaced its existing binding."
                assert index.read_bytes() == binding
            changed = replace(original, content=replace(original.content, body="The person's later edit.\n"))
            if isinstance(saved, SavedEmlDraft):
                artifact = saved.path
                artifact.write_bytes(eml_bytes(changed).replace(fingerprint(changed).encode(), fingerprint(original).encode()))
            else:
                command.edit(changed.content)
                artifact = command.files()[0]
            edited = artifact.read_bytes()
            assert isinstance(restarted.save(original, threading.Event()), DraftConflict)
            assert artifact.read_bytes() == edited
            artifact.unlink()
            assert isinstance(restarted.save(original, threading.Event()), DraftConflict)
            assert not artifact.exists()
            assert os.path.samestat(identity, index.stat()) and index.read_bytes() == binding


def test_native_save_has_durable_mailbox_binding_and_preserves_body_beyond_command_output_limit(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    original = request("Private complete draft content.\n" * 5_000 + "verified ending")
    original = replace(original, content=replace(original.content,
                       to=(Recipient("ada@example.invalid"),), cc=(Recipient("lee@example.invalid"),), bcc=(Recipient("audit@example.invalid"),)))
    saved = drafts.save(original, threading.Event())
    assert isinstance(saved, SavedOutlookDraft), saved
    assert saved.location.folder.store_id == "store-a" and len(command.files()) == 1
    native = drafts.transport.read(original, saved.location, threading.Event())
    assert isinstance(native, DraftReadback) and native.content.body == original.content.body
    index_before = {path.name: path.read_bytes() for path in (drafts.folder / "index").glob("*.json")}
    assert drafts.inspect(original, threading.Event()) == saved
    assert {path.name: path.read_bytes() for path in (drafts.folder / "index").glob("*.json")} == index_before
    assert original.content.body not in "".join(value.decode() for value in index_before.values())


def test_native_response_bound_retains_valid_content_when_json_expands_control_characters(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    original = request("\u0001" * 1_500_000 + "complete final line")
    saved = drafts.save(original, threading.Event())
    assert isinstance(saved, SavedOutlookDraft), saved
    actual = drafts.transport.read(original, saved.location, threading.Event())
    assert isinstance(actual, DraftReadback) and actual.content == original.content
    assert len(command.files()) == 1


class BlocksIndexCommit:
    __slots__ = ("inner", "path")

    def __init__(self, inner: LocalOutlookCommand, path: Path) -> None:
        self.inner, self.path = inner, path

    def run(self, request: Mapping[str, object], response: Path, cancel: threading.Event) -> tuple[int, str]:
        result = self.inner.run(request, response, cancel)
        if request.get("operation") == "status":
            self.path.mkdir(parents=True)  # A real incompatible filesystem entry prevents atomic index publication.
        return result


def test_failed_identity_publication_prevents_native_save_or_fallback(tmp_path: Path) -> None:
    original = request()
    drafts, command = adapter(tmp_path)
    index = drafts.folder / "index" / (draft_key(original) + ".json")
    saving = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(BlocksIndexCommit(command, index)))
    assert isinstance(saving.save(original, threading.Event()), DraftUncertain)
    assert index.is_dir() and not command.files() and not (drafts.folder / "drafts").exists()
    calls = [cast("dict[str, object]", json.loads(line)) for line in (command.root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [entry["operation"] for entry in calls] == ["status"]


def test_malformed_existing_identity_fails_closed_without_probing_or_mutating_drafts(tmp_path: Path) -> None:
    original = request()
    value: dict[str, object] = {
        "version": 1, "kind": "pending_outlook", "key": draft_key(original), "fingerprint": fingerprint(original),
        "folder": {"entry_id": "drafts-a", "store_id": "store-a"},
    }
    changes: tuple[dict[str, object], ...] = (
        {"version": True}, {"kind": "unknown"}, {"fingerprint": "invalid"}, {"key": "0" * 64},
        {"folder": {"entry_id": "drafts-a", "store_id": None}},
    )
    invalid = [b"{"]
    for change in changes:
        mutated: dict[str, object] = {**value, **change}
        invalid.append(json.dumps(mutated).encode())
    for number, content in enumerate(invalid):
        drafts, command = adapter(tmp_path / str(number))
        index = drafts.folder / "index" / (draft_key(original) + ".json")
        index.parent.mkdir(parents=True)
        index.write_bytes(content)
        assert isinstance(drafts.inspect(original, threading.Event()), DraftUncertain)
        assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
        assert index.read_bytes() == content and not (command.root / "calls.jsonl").exists()
        assert not command.files() and not (drafts.folder / "drafts").exists()


def test_lost_native_save_acknowledgement_recovers_same_marker_in_original_store(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    original = request()
    command.configure("lose_ack")
    assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
    assert len(command.files()) == 1 and not (drafts.folder / "drafts").exists()
    index_path = drafts.folder / "index" / (draft_key(original) + ".json")
    pending = index_path.read_bytes()
    command.configure(store="store-b", folder="drafts-b")
    restarted = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(command))
    observed = restarted.inspect(original, threading.Event())
    assert isinstance(observed, SavedOutlookDraft) and observed.location.folder.store_id == "store-a"
    assert index_path.read_bytes() == pending, "Read-only recovery must not update the draft index."
    saved = restarted.save(original, threading.Event())
    assert saved == observed and len(command.files()) == 1
    assert not (drafts.folder / "drafts").exists()
    command.files()[0].unlink()
    assert isinstance(restarted.save(original, threading.Event()), DraftConflict), "A recovered saved binding must retain its known item identity."
    assert not command.files()


def test_uncertain_native_save_never_selects_fallback_or_replays_a_missing_draft(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    original = request()
    command.configure("lose_ack")
    assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
    command.configure("unavailable")
    assert isinstance(drafts.inspect(original, threading.Event()), DraftUncertain)
    assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
    command.files()[0].unlink()
    command.configure()
    assert isinstance(drafts.inspect(original, threading.Event()), DraftUncertain)
    assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
    assert not command.files() and not (drafts.folder / "drafts").exists()


def test_native_user_edits_deletion_and_changed_request_never_restore_model_content(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    original = request()
    assert isinstance(drafts.save(original, threading.Event()), SavedOutlookDraft)
    command.edit(replace(original.content, body="The user's revision", bcc=(Recipient("reviewer@example.invalid"),)))
    changed = command.files()[0].read_bytes()
    assert isinstance(drafts.save(original, threading.Event()), DraftConflict)
    assert isinstance(drafts.save(replace(original, content=replace(original.content, body="A different request")), threading.Event()), DraftConflict)
    assert command.files()[0].read_bytes() == changed
    command.files()[0].unlink()
    assert isinstance(drafts.inspect(original, threading.Event()), DraftConflict)
    assert isinstance(drafts.save(original, threading.Event()), DraftConflict)
    assert not command.files()


def test_ambiguous_marker_and_incomplete_or_wrong_readback_do_not_verify(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    original = request()
    command.configure("lose_ack")
    assert isinstance(drafts.save(original, threading.Event()), DraftUncertain)
    command.duplicate()
    command.configure()
    assert isinstance(drafts.inspect(original, threading.Event()), DraftConflict)
    assert len(command.files()) == 2
    for index, mode in enumerate(("short_body", "extra_bcc", "wrong_store", "already_sent", "malformed_recipient")):
        other_drafts, other_command = adapter(tmp_path / str(index))
        other_command.configure(mode)
        result = other_drafts.save(original, threading.Event())
        assert isinstance(result, (DraftConflict, DraftUncertain)), (mode, result)
        assert len(other_command.files()) == 1 and not (other_drafts.folder / "drafts").exists()


class GatedCommand:
    __slots__ = ("entered", "inner", "release")

    def __init__(self, inner: LocalOutlookCommand, entered: threading.Event, release: threading.Event) -> None:
        self.inner, self.entered, self.release = inner, entered, release

    def run(self, request: Mapping[str, object], response: Path, cancel: threading.Event) -> tuple[int, str]:
        if request.get("operation") == "ensure":
            self.entered.set()
            assert self.release.wait(10)
        return self.inner.run(request, response, cancel)


def test_parallel_retry_waits_for_one_owner_and_waiting_stop_does_not_create_a_draft(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    first = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(GatedCommand(command, entered, release)))
    second = OutlookDrafts(drafts.folder, transport=PowerShellOutlook(command))
    original = request()
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(first.save, original, threading.Event())
        try:
            assert entered.wait(10)
            waiting = pool.submit(second.save, original, cancelled)
            cancelled.set()
            assert waiting.result(5) is Cancelled.BEFORE_EFFECT
        finally:
            release.set()
        saved = saving.result(10)
    assert isinstance(saved, SavedOutlookDraft) and len(command.files()) == 1
    assert second.save(original, threading.Event()) == saved


def test_inspection_and_pre_cancelled_save_do_not_create_records_or_probe_outlook(tmp_path: Path) -> None:
    drafts, command = adapter(tmp_path)
    original = request()
    assert drafts.inspect(original, threading.Event()) is Missing.DRAFT
    cancel = threading.Event()
    cancel.set()
    assert drafts.save(original, cancel) is Cancelled.BEFORE_EFFECT
    assert not drafts.folder.exists() and not (command.root / "calls.jsonl").exists()
