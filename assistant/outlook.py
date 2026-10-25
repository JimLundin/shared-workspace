"""Save and verify local drafts without sending or opening a mail window.

The durable identity binds a workflow/draft pair, exact content, and its chosen
store before a native Save. Uncertain native saves only search that marker on
retry. File drafts use the same identity and never overwrite an existing file.
"""

import base64
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from email import policy
from email.errors import HeaderParseError
from email.header import Header
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from enum import Enum, auto
from pathlib import Path
from typing import Protocol, assert_never, cast

from . import commands

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

MAX_BODY_BYTES = 4_000_000
# JSON may encode one valid body byte as a six-byte Unicode escape.
MAX_WIRE_BYTES = MAX_BODY_BYTES * 6 + 1_000_000
MAX_RECIPIENTS = 200


def _text(value: object, label: str, maximum: int, *, empty: bool = False, header: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value or len(value) > maximum or (not empty and not value):
        raise ValueError(f"Invalid {label}.")
    if header and any(character in value for character in "\r\n"):
        raise ValueError(f"Invalid {label}.")
    return value


def _digest(value: object) -> str:
    result = _text(value, "draft digest", 64)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError("Invalid draft digest.")
    return result


def _variant(value: object, expected: tuple[type[object], ...], label: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"Invalid {label}.")


def _json(value: Mapping[str, object]) -> str:
    values: dict[str, object] = dict(value)
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Invalid draft record.")
    return cast("dict[str, object]", value)


@dataclass(frozen=True, slots=True)
class Recipient:
    address: str

    def __post_init__(self) -> None:
        value = _text(self.address, "recipient address", 320, header=True)
        try:
            parsed = Address(addr_spec=value)
        except (ValueError, HeaderParseError) as error:
            raise ValueError("Use an explicit SMTP recipient address.") from error
        if not parsed.username or not parsed.domain or parsed.addr_spec != value:
            raise ValueError("Use an explicit SMTP address, without a display name or address list.")


def _recipients(value: object) -> tuple[Recipient, ...]:
    if not isinstance(value, tuple):
        raise ValueError("Draft recipients must be immutable tuples.")
    items = cast("tuple[object, ...]", value)
    if len(items) > MAX_RECIPIENTS or not all(isinstance(item, Recipient) for item in items):
        raise ValueError("Invalid draft recipients.")
    return cast("tuple[Recipient, ...]", items)


def canonical_address(recipient: Recipient) -> str:
    """DNS case is insignificant; the local part and recipient role are retained."""
    parsed = Address(addr_spec=recipient.address)
    return Address(username=parsed.username, domain=parsed.domain.lower()).addr_spec


def canonical_body(body: str) -> str:
    """Outlook uses CRLF. Normalize only newline representation, never whitespace."""
    return body.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class DraftContent:
    subject: str
    body: str
    to: tuple[Recipient, ...] = ()
    cc: tuple[Recipient, ...] = ()
    bcc: tuple[Recipient, ...] = ()

    def __post_init__(self) -> None:
        _text(self.subject, "draft subject", 998, empty=True, header=True)
        _text(self.body, "draft body", MAX_BODY_BYTES, empty=True)
        if len(self.body.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError("The draft body exceeds the supported complete-body size.")
        recipients = (*_recipients(self.to), *_recipients(self.cc), *_recipients(self.bcc))
        addresses = tuple(canonical_address(item) for item in recipients)
        if len(recipients) > MAX_RECIPIENTS or len(set(addresses)) != len(addresses):
            raise ValueError("A draft may have at most 200 distinct recipients across To, Cc and Bcc.")


@dataclass(frozen=True, slots=True)
class DraftRequest:
    workflow_id: str
    draft_id: str
    content: DraftContent

    def __post_init__(self) -> None:
        _text(self.workflow_id, "workflow identity", 256)
        _text(self.draft_id, "draft identity", 256)
        _variant(self.content, (DraftContent,), "draft content")


def draft_key(request: DraftRequest) -> str:
    return hashlib.sha256(_json({"workflow": request.workflow_id, "draft": request.draft_id}).encode("utf-8")).hexdigest()


def content_data(content: DraftContent) -> dict[str, object]:
    return {
        "subject": content.subject, "body": canonical_body(content.body),
        "to": sorted(canonical_address(item) for item in content.to),
        "cc": sorted(canonical_address(item) for item in content.cc),
        "bcc": sorted(canonical_address(item) for item in content.bcc),
    }


def fingerprint(request: DraftRequest) -> str:
    return hashlib.sha256(_json({"key": draft_key(request), "content": content_data(request.content)}).encode("utf-8")).hexdigest()


def matches(expected: DraftContent, actual: DraftContent) -> bool:
    return content_data(expected) == content_data(actual)


@dataclass(frozen=True, slots=True)
class DraftFolder:
    entry_id: str
    store_id: str

    def __post_init__(self) -> None:
        _text(self.entry_id, "Outlook folder identity", 16_384)
        _text(self.store_id, "Outlook store identity", 16_384)


@dataclass(frozen=True, slots=True)
class OutlookLocation:
    entry_id: str
    folder: DraftFolder

    def __post_init__(self) -> None:
        _text(self.entry_id, "Outlook item identity", 16_384)
        _variant(self.folder, (DraftFolder,), "draft folder")


@dataclass(frozen=True, slots=True)
class Available:
    folder: DraftFolder

    def __post_init__(self) -> None:
        _variant(self.folder, (DraftFolder,), "draft folder")


@dataclass(frozen=True, slots=True)
class Unavailable:
    reason: str

    def __post_init__(self) -> None:
        _text(self.reason, "availability reason", 2_000)


type Availability = Available | Unavailable


@dataclass(frozen=True, slots=True)
class DraftConflict:
    reason: str

    def __post_init__(self) -> None:
        _text(self.reason, "draft conflict", 2_000)


@dataclass(frozen=True, slots=True)
class DraftUncertain:
    reason: str

    def __post_init__(self) -> None:
        _text(self.reason, "uncertain draft", 2_000)


class Cancelled(Enum):
    BEFORE_EFFECT = auto()


class Missing(Enum):
    DRAFT = auto()


@dataclass(frozen=True, slots=True)
class DraftReadback:
    location: OutlookLocation
    key: str
    fingerprint: str
    content: DraftContent

    def __post_init__(self) -> None:
        _digest(self.key)
        _digest(self.fingerprint)
        _variant(self.location, (OutlookLocation,), "draft location")
        _variant(self.content, (DraftContent,), "draft content")


@dataclass(frozen=True, slots=True)
class SavedOutlookDraft:
    location: OutlookLocation
    key: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SavedEmlDraft:
    path: Path
    key: str
    fingerprint: str
    unavailable: Unavailable


type NativeResult = DraftReadback | Missing | Unavailable | DraftConflict | DraftUncertain
type DraftResult = SavedOutlookDraft | SavedEmlDraft | DraftConflict | DraftUncertain | Cancelled


class DraftTransport(Protocol):
    def status(self, cancel: threading.Event) -> Availability: ...
    def ensure(self, request: DraftRequest, folder: DraftFolder, cancel: threading.Event) -> NativeResult: ...
    def find(self, request: DraftRequest, folder: DraftFolder, cancel: threading.Event) -> NativeResult: ...
    def read(self, request: DraftRequest, location: OutlookLocation, cancel: threading.Event) -> NativeResult: ...


class CommandTransport(Protocol):
    def run(self, request: Mapping[str, object], response: Path, cancel: threading.Event) -> tuple[int, str]: ...


class PowerShellCommand:
    __slots__ = ()

    def run(self, request: Mapping[str, object], response: Path, cancel: threading.Event) -> tuple[int, str]:
        payload = base64.b64encode(_json({**request, "response_path": str(response)}).encode("utf-8")).decode("ascii")
        source = Path(__file__).with_suffix(".ps1").read_text(encoding="utf-8")
        script = f"$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json\n{source}"
        return commands.powershell(script, timeout_s=45, cancel=cancel)


def _folder(data: object) -> DraftFolder:
    value = _object(data)
    return DraftFolder(_text(value.get("entry_id"), "folder identity", 16_384), _text(value.get("store_id"), "store identity", 16_384))


def _addresses(data: object) -> tuple[Recipient, ...]:
    if not isinstance(data, list):
        raise ValueError("Invalid recipient readback.")
    values = cast("list[object]", data)
    if len(values) > MAX_RECIPIENTS:
        raise ValueError("Invalid recipient readback.")
    return tuple(Recipient(_text(item, "recipient", 320, header=True)) for item in values)


def _content(data: object) -> DraftContent:
    value = _object(data)
    return DraftContent(
        _text(value.get("subject"), "draft subject", 998, empty=True, header=True),
        _text(value.get("body"), "draft body", MAX_BODY_BYTES, empty=True),
        _addresses(value.get("to")), _addresses(value.get("cc")), _addresses(value.get("bcc")),
    )


class PowerShellOutlook:
    """Attach to an existing classic Outlook session; never start or activate it."""

    __slots__ = ("command", "supported")

    def __init__(self, command: CommandTransport | None = None) -> None:
        self.supported = command is not None or sys.platform == "win32"
        self.command = command if command is not None else PowerShellCommand()

    def _exchange(self, request: Mapping[str, object], cancel: threading.Event) -> dict[str, object] | DraftUncertain:
        try:
            with tempfile.TemporaryDirectory(prefix="toolkit-outlook-") as scratch:
                response = Path(scratch) / "response.json"
                code, _ = self.command.run(request, response, cancel)
                if code != 0:
                    return DraftUncertain("The Outlook operation did not return a verified result. Inspect the same draft identity before retrying.")
                with response.open("rb") as saved:
                    data = saved.read(MAX_WIRE_BYTES + 1)
                if len(data) > MAX_WIRE_BYTES:
                    return DraftUncertain("Outlook returned an oversized response; the draft is not verified.")
                raw: object = json.loads(data)
                return _object(raw)
        except (OSError, ValueError):
            return DraftUncertain("The Outlook result could not be read completely; the draft is not verified.")

    def status(self, cancel: threading.Event) -> Availability:
        if not self.supported:
            return Unavailable("Local classic Outlook automation is available only on Windows.")
        if cancel.is_set():
            return Unavailable("Outlook inspection was cancelled.")
        result = self._exchange({"operation": "status"}, cancel)
        if isinstance(result, DraftUncertain):
            return Unavailable(result.reason)
        try:
            if result.get("kind") == "available":
                return Available(_folder(result.get("folder")))
            if result.get("kind") == "unavailable":
                return Unavailable(_text(result.get("reason"), "availability reason", 2_000))
        except ValueError:
            pass
        return Unavailable("A running classic Outlook draft store could not be confirmed.")

    def _native(self, data: Mapping[str, object], cancel: threading.Event) -> NativeResult:
        if not self.supported:
            return Unavailable("Local classic Outlook automation is unavailable on this platform.")
        result = self._exchange(data, cancel)
        if isinstance(result, DraftUncertain):
            return result
        try:
            match result.get("kind"):
                case "draft":
                    if result.get("saved") is not True or result.get("sent") is not False or result.get("submitted") is not False:
                        return DraftConflict("The identified Outlook item is no longer an unchanged saved draft.")
                    return DraftReadback(
                        OutlookLocation(_text(result.get("entry_id"), "item identity", 16_384), _folder(result.get("folder"))),
                        _digest(result.get("key")), _digest(result.get("fingerprint")), _content(result.get("content")),
                    )
                case "missing":
                    return Missing.DRAFT
                case "unavailable":
                    return Unavailable(_text(result.get("reason"), "availability reason", 2_000))
                case "conflict":
                    return DraftConflict(_text(result.get("reason"), "draft conflict", 2_000))
                case "uncertain":
                    return DraftUncertain(_text(result.get("reason"), "uncertain draft", 2_000))
                case _:
                    pass
        except ValueError:
            pass
        return DraftUncertain("Outlook returned an invalid or incomplete draft readback.")

    def ensure(self, request: DraftRequest, folder: DraftFolder, cancel: threading.Event) -> NativeResult:
        return self._native({
            "operation": "ensure", "key": draft_key(request), "fingerprint": fingerprint(request),
            "folder": {"entry_id": folder.entry_id, "store_id": folder.store_id}, "content": content_data(request.content),
        }, cancel)

    def find(self, request: DraftRequest, folder: DraftFolder, cancel: threading.Event) -> NativeResult:
        return self._native({"operation": "find", "key": draft_key(request),
                             "folder": {"entry_id": folder.entry_id, "store_id": folder.store_id}}, cancel)

    def read(self, request: DraftRequest, location: OutlookLocation, cancel: threading.Event) -> NativeResult:
        return self._native({"operation": "read", "key": draft_key(request), "entry_id": location.entry_id,
                             "folder": {"entry_id": location.folder.entry_id, "store_id": location.folder.store_id}}, cancel)


def eml_bytes(request: DraftRequest) -> bytes:
    """Encode the entire body as bytes, without set_content's appended newline."""
    output_policy = cast("policy.EmailPolicy[EmailMessage[str, str]]", policy.SMTPUTF8)
    output_policy = output_policy.clone(refold_source="none")
    message: EmailMessage[str, str] = EmailMessage(policy=output_policy)
    # An encoded header keeps leading spaces/tabs that ordinary header parsing
    # otherwise consumes as field separators. Preserve its RFC 2047 folding.
    message.set_raw("Subject", Header(request.content.subject, "utf-8", header_name="Subject").encode(linesep="\r\n"))
    for name, recipients in (("To", request.content.to), ("Cc", request.content.cc), ("Bcc", request.content.bcc)):
        if recipients:
            message[name] = ", ".join(item.address for item in recipients)
    message["Message-ID"] = "<toolkit." + draft_key(request) + "@drafts.invalid>"
    message["X-Unsent"] = "1"
    message["X-Toolkit-Draft-Key"] = draft_key(request)
    message["X-Toolkit-Draft-Fingerprint"] = fingerprint(request)
    message["MIME-Version"] = "1.0"
    message["Content-Type"] = 'text/plain; charset="utf-8"'
    message["Content-Transfer-Encoding"] = "base64"
    message.set_payload(base64.encodebytes(request.content.body.encode("utf-8")).decode("ascii"))
    return message.as_bytes()


def read_eml(data: bytes) -> tuple[str, str, DraftContent]:
    if len(data) > MAX_WIRE_BYTES:
        raise ValueError("The saved draft exceeds the readback bound.")
    input_policy = cast("policy.EmailPolicy[EmailMessage[str, str]]", policy.default)
    message = BytesParser(policy=input_policy).parsebytes(data)
    if message.is_multipart() or message.defects or message.get_content_type() != "text/plain" or message.get_content_charset() != "utf-8":
        raise ValueError("The saved draft is no longer a complete plain-text draft.")
    headers: dict[str, list[str]] = {}
    for name, value in message.raw_items():
        headers.setdefault(name.lower(), []).append(value)
    for name in ("subject", "message-id", "x-unsent", "x-toolkit-draft-key", "x-toolkit-draft-fingerprint"):
        if len(headers.get(name, [])) != 1:
            raise ValueError("The saved draft has missing or duplicate identity headers.")
    if str(message["X-Unsent"]) != "1":
        raise ValueError("The saved message is not marked as an editable unsent draft.")
    key, digest = _digest(str(message["X-Toolkit-Draft-Key"])), _digest(str(message["X-Toolkit-Draft-Fingerprint"]))
    if str(message["Message-ID"]) != "<toolkit." + key + "@drafts.invalid>":
        raise ValueError("The saved message identity changed.")
    body = cast("object", message.get_payload(decode=True))
    if not isinstance(body, bytes):
        raise ValueError("The saved draft has no complete body.")
    groups: list[tuple[Recipient, ...]] = []
    for name in ("To", "Cc", "Bcc"):
        if len(headers.get(name.lower(), [])) > 1:
            raise ValueError("The saved draft has duplicate recipient headers.")
        values = [] if message[name] is None else [str(message[name])]
        groups.append(tuple(Recipient(address) for _, address in getaddresses(values)))
    return key, digest, DraftContent(str(message["Subject"]), body.decode("utf-8"), groups[0], groups[1], groups[2])


@dataclass(frozen=True, slots=True)
class PendingOutlook:
    folder: DraftFolder


@dataclass(frozen=True, slots=True)
class ReadyOutlook:
    location: OutlookLocation


@dataclass(frozen=True, slots=True)
class PendingEml:
    unavailable: Unavailable


@dataclass(frozen=True, slots=True)
class ReadyEml:
    unavailable: Unavailable


type Destination = PendingOutlook | ReadyOutlook | PendingEml | ReadyEml


@dataclass(frozen=True, slots=True)
class DraftRecord:
    key: str
    fingerprint: str
    destination: Destination


def record_data(record: DraftRecord) -> dict[str, object]:
    value: dict[str, object] = {"version": 1, "key": record.key, "fingerprint": record.fingerprint}
    match record.destination:
        case PendingOutlook(folder):
            value.update(kind="pending_outlook", folder={"entry_id": folder.entry_id, "store_id": folder.store_id})
        case ReadyOutlook(location):
            value.update(kind="outlook", entry_id=location.entry_id,
                         folder={"entry_id": location.folder.entry_id, "store_id": location.folder.store_id})
        case PendingEml(unavailable):
            value.update(kind="pending_eml", reason=unavailable.reason)
        case ReadyEml(unavailable):
            value.update(kind="eml", reason=unavailable.reason)
        case _:
            assert_never(record.destination)
    return value


def decode_record(data: bytes) -> DraftRecord:
    raw: object = json.loads(data)
    value = _object(raw)
    if type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unknown draft record version.")
    destination: Destination
    match value.get("kind"):
        case "pending_outlook":
            destination = PendingOutlook(_folder(value.get("folder")))
        case "outlook":
            destination = ReadyOutlook(OutlookLocation(_text(value.get("entry_id"), "item identity", 16_384), _folder(value.get("folder"))))
        case "pending_eml":
            destination = PendingEml(Unavailable(_text(value.get("reason"), "availability reason", 2_000)))
        case "eml":
            destination = ReadyEml(Unavailable(_text(value.get("reason"), "availability reason", 2_000)))
        case _:
            raise ValueError("Unknown draft destination.")
    return DraftRecord(_digest(value.get("key")), _digest(value.get("fingerprint")), destination)


def _atomic(path: Path, data: bytes, *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".draft-", dir=path.parent, delete=False) as temporary:
        pending = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temporary.close()
            pending.unlink(missing_ok=True)
            raise
    try:
        if create:
            os.link(pending, path)  # Publish complete bytes atomically, without replacing user edits.
        else:
            pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


@contextlib.contextmanager
def _hold(path: Path, cancel: threading.Event) -> Generator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        # Windows byte locks also forbid reads by competing owners. Locking
        # beyond EOF is supported, so the ownership file needs no contents.
        deadline = time.monotonic() + 10
        while True:
            if cancel.is_set():
                raise InterruptedError("Draft saving was cancelled before ownership was acquired.")
            lock.seek(0)
            try:
                if sys.platform == "win32":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another process still owns this draft.") from None
                cancel.wait(.05)
        try:
            yield
        finally:
            lock.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


class OutlookDrafts:
    """The owner of durable draft identity, native saving, and file fallback."""

    __slots__ = ("folder", "transport")

    def __init__(self, folder: Path, *, transport: DraftTransport | None = None) -> None:
        self.folder = folder.absolute()
        self.transport = transport if transport is not None else PowerShellOutlook()

    def status(self, cancel: threading.Event) -> Availability:
        return self.transport.status(cancel)

    def _record(self, record: DraftRecord) -> None:
        _atomic(self.folder / "index" / (record.key + ".json"), _json(record_data(record)).encode("utf-8"))

    def _load(self, key: str) -> DraftRecord | None:
        path = self.folder / "index" / (key + ".json")
        try:
            with path.open("rb") as source:
                data = source.read(65_537)
        except FileNotFoundError:
            return None
        if len(data) > 65_536:
            raise ValueError("The draft identity record is invalid.")
        record = decode_record(data)
        if record.key != key:
            raise ValueError("The saved draft identity does not match its record.")
        return record

    def _eml(self, request: DraftRequest, record: DraftRecord, cancel: threading.Event) -> DraftResult:
        destination = record.destination
        if not isinstance(destination, (PendingEml, ReadyEml)):
            raise ValueError("The draft is bound to Outlook.")
        path = self.folder / "drafts" / (record.key + ".eml")
        if cancel.is_set():
            return Cancelled.BEFORE_EFFECT
        if isinstance(destination, PendingEml) and not path.exists():
            # A writer may already have published it; readback must decide.
            with contextlib.suppress(FileExistsError):
                _atomic(path, eml_bytes(request), create=True)
        result = self._read_eml(request, record)
        if isinstance(result, SavedEmlDraft) and isinstance(destination, PendingEml):
            # A ready binding already records this verified identity. Replacing
            # it on retries adds a write that Windows readers can deny.
            self._record(DraftRecord(record.key, record.fingerprint, ReadyEml(destination.unavailable)))
        return result

    def _read_eml(self, request: DraftRequest, record: DraftRecord) -> DraftResult:
        destination = record.destination
        if not isinstance(destination, (PendingEml, ReadyEml)):
            raise ValueError("The draft is bound to Outlook.")
        path = self.folder / "drafts" / (record.key + ".eml")
        try:
            with path.open("rb") as source:
                data = source.read(MAX_WIRE_BYTES + 1)
        except FileNotFoundError:
            if isinstance(destination, ReadyEml):
                return DraftConflict("The previously saved file draft is missing; it was not recreated.")
            return DraftUncertain("The pending file draft has no complete saved artifact to verify.")
        try:
            key, digest, content = read_eml(data)
        except ValueError:
            return DraftConflict("The existing file draft cannot be verified as the requested draft; its contents were preserved.")
        if (key, digest) != (record.key, record.fingerprint) or not matches(request.content, content):
            return DraftConflict("The existing file draft differs from the request. Its edits were preserved.")
        return SavedEmlDraft(path, record.key, record.fingerprint, destination.unavailable)

    def _verified(self, request: DraftRequest, record: DraftRecord, result: NativeResult) -> DraftResult:
        if isinstance(result, DraftReadback):
            destination = record.destination
            folder = destination.folder if isinstance(destination, PendingOutlook) else (
                destination.location.folder if isinstance(destination, ReadyOutlook) else None
            )
            if (result.key, result.fingerprint) != (record.key, record.fingerprint) or result.location.folder != folder:
                return DraftConflict("The Outlook readback has a different draft or mailbox identity; nothing was overwritten.")
            if isinstance(destination, ReadyOutlook) and result.location != destination.location:
                return DraftConflict("Outlook returned a different item from the recorded draft.")
            if not matches(request.content, result.content):
                return DraftConflict("The Outlook draft differs from the requested content or recipients. Its edits were preserved.")
            return SavedOutlookDraft(result.location, record.key, record.fingerprint)
        if isinstance(result, (DraftConflict, DraftUncertain)):
            return result
        if result is Missing.DRAFT and isinstance(record.destination, ReadyOutlook):
            return DraftConflict("The previously saved Outlook draft was removed or moved. It will not be recreated automatically.")
        return DraftUncertain("The original Outlook draft could not be verified. Retry its exact identity when that mailbox is available.")

    def _commit_native(self, request: DraftRequest, record: DraftRecord, result: NativeResult) -> DraftResult:
        verified = self._verified(request, record, result)
        if isinstance(verified, SavedOutlookDraft) and isinstance(record.destination, PendingOutlook):
            self._record(DraftRecord(record.key, record.fingerprint, ReadyOutlook(verified.location)))
        return verified

    def _save(self, request: DraftRequest, cancel: threading.Event) -> DraftResult:
        key, digest = draft_key(request), fingerprint(request)
        record = self._load(key)
        if record is not None and record.fingerprint != digest:
            return DraftConflict("This workflow/draft identity is already bound to different content. Use an explicit new draft revision.")
        if record is None:
            available = self.transport.status(cancel)
            if cancel.is_set():
                return Cancelled.BEFORE_EFFECT
            destination: Destination = PendingOutlook(available.folder) if isinstance(available, Available) else PendingEml(available)
            record = DraftRecord(key, digest, destination)
            self._record(record)  # Persist identity and selected mailbox before any native Save.
            if isinstance(destination, PendingOutlook):
                if cancel.is_set():
                    return Cancelled.BEFORE_EFFECT
                return self._commit_native(request, record, self.transport.ensure(request, destination.folder, cancel))
        match record.destination:
            case PendingOutlook(folder):
                return self._commit_native(request, record, self.transport.find(request, folder, cancel))
            case ReadyOutlook(location):
                return self._commit_native(request, record, self.transport.read(request, location, cancel))
            case PendingEml() | ReadyEml():
                return self._eml(request, record, cancel)
            case _:
                assert_never(record.destination)

    def save(self, request: DraftRequest, cancel: threading.Event) -> DraftResult:
        _variant(request, (DraftRequest,), "draft request")
        if cancel.is_set():
            return Cancelled.BEFORE_EFFECT
        try:
            with _hold(self.folder / "index" / (draft_key(request) + ".lock"), cancel):
                if cancel.is_set():
                    return Cancelled.BEFORE_EFFECT
                return self._save(request, cancel)
        except InterruptedError:
            return Cancelled.BEFORE_EFFECT
        except (OSError, ValueError):
            return DraftUncertain("The draft could not be saved and verified durably. Its original identity must be inspected before retrying.")

    def inspect(self, request: DraftRequest, cancel: threading.Event) -> DraftResult | Missing:
        """Read an existing binding; never choose a destination, save, or update it."""
        _variant(request, (DraftRequest,), "draft request")
        if cancel.is_set():
            return Cancelled.BEFORE_EFFECT
        try:
            record = self._load(draft_key(request))
            if record is None:
                return Missing.DRAFT
            if record.fingerprint != fingerprint(request):
                return DraftConflict("This workflow/draft identity is bound to different content.")
            match record.destination:
                case PendingOutlook(folder):
                    return self._verified(request, record, self.transport.find(request, folder, cancel))
                case ReadyOutlook(location):
                    return self._verified(request, record, self.transport.read(request, location, cancel))
                case PendingEml() | ReadyEml():
                    return self._read_eml(request, record)
                case _:
                    assert_never(record.destination)
        except (OSError, ValueError):
            return DraftUncertain("The recorded draft could not be read completely; its outcome remains uncertain.")
