"""Bounded JSON values for the volatile work/assistant process boundary.

Public presentation events and typed UI-owned requests cross this channel.
Payloads remain volatile; SDK objects and pickles never cross the boundary.
"""

import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final, assert_never, cast

from assistant.activity import Activity, ContextSnapshot
from assistant.agent_session import (
    Answer,
    Begin,
    Done,
    Failed,
    Finished,
    LiveMessage,
    Problem,
    Progress,
    ReasoningSummary,
    Said,
    StreamState,
    Succeeded,
    ToolActivity,
    ToolOutcome,
    ToolStatus,
)
from assistant.browser_surface_operations import AppOperation, AppResult
from assistant.browser_surface_operations import operation_data as app_operation_data
from assistant.browser_surface_operations import operation_of as app_operation_of
from assistant.browser_surface_operations import result_data as app_result_data
from assistant.browser_surface_operations import result_of as app_result_of
from assistant.llm import Json
from assistant.recording_state import AudioSource
from winapi.shapes import Window

from .execution import ApprovalReview
from .task import Approval, RunRef, digest, identifier, integer, text, timestamp, variant

VERSION: Final = 1
MAX_FRAME: Final = 1_048_576
MAX_EVENTS: Final = 128
MAX_TEXT: Final = 128_000
MAX_CONTEXT: Final = 128

type PresentationEvent = Begin | Progress | Said | Done | Problem | Finished | LiveMessage


@dataclass(frozen=True, slots=True)
class LogEvent:
    run: RunRef
    event: PresentationEvent


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    run: RunRef
    review: ApprovalReview


@dataclass(frozen=True, slots=True)
class StartMeeting:
    source: AudioSource
    title: str


class MeetingCommand(Enum):
    STOP = auto()
    RETRY = auto()
    LIST = auto()
    STATUS = auto()


type MeetingOperation = StartMeeting | MeetingCommand


@dataclass(frozen=True, slots=True)
class MeetingRequest:
    run: RunRef
    request_id: str
    operation: MeetingOperation


@dataclass(frozen=True, slots=True)
class MeetingSucceeded:
    text: str


@dataclass(frozen=True, slots=True)
class MeetingFailed:
    reason: str


type MeetingResult = MeetingSucceeded | MeetingFailed


@dataclass(frozen=True, slots=True)
class BrowserAppRequest:
    run: RunRef
    request_id: str
    operation: AppOperation
    expires_at: float

    def __post_init__(self) -> None:
        variant(self.run, RunRef, "browser app run")
        identifier(self.request_id, "browser app request")
        app_operation_of(app_operation_data(self.operation))
        object.__setattr__(self, "expires_at", timestamp(self.expires_at, "browser app expiry"))


@dataclass(frozen=True, slots=True)
class BrowserAppCancelled:
    run: RunRef
    request_id: str

    def __post_init__(self) -> None:
        variant(self.run, RunRef, "browser app run")
        identifier(self.request_id, "browser app request")


@dataclass(frozen=True, slots=True)
class Resync:
    reason: str


type BridgeEvent = LogEvent | ReviewEvent | MeetingRequest | BrowserAppRequest | BrowserAppCancelled | Resync


@dataclass(frozen=True, slots=True)
class Envelope:
    epoch: str
    sequence: int
    payload: BridgeEvent


@dataclass(frozen=True, slots=True)
class Hello:
    assistant_pid: int


@dataclass(frozen=True, slots=True)
class Poll:
    after: int
    limit: int


@dataclass(frozen=True, slots=True)
class Register:
    task_id: str
    context: ContextSnapshot | None
    assistant_pid: int


@dataclass(frozen=True, slots=True)
class PublishContext:
    context: ContextSnapshot
    assistant_pid: int


@dataclass(frozen=True, slots=True)
class MeetingResponse:
    run: RunRef
    request_id: str
    result: MeetingResult


@dataclass(frozen=True, slots=True)
class BrowserAppResponse:
    run: RunRef
    request_id: str
    result: AppResult


class Command(Enum):
    WAKE = auto()


type Operation = Hello | Poll | Register | PublishContext | MeetingResponse | BrowserAppResponse | Command


@dataclass(frozen=True, slots=True)
class Request:
    epoch: str
    client: str
    operation: Operation


@dataclass(frozen=True, slots=True)
class Accepted:
    epoch: str
    accepted: bool


@dataclass(frozen=True, slots=True)
class Batch:
    epoch: str
    events: tuple[Envelope, ...]


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: str


type Reply = Accepted | Batch | Rejected


def record(value: object, fields: str) -> dict[str, Json]:
    """Validate an external object's keys before narrowing its JSON values."""
    if not isinstance(value, dict) or set(cast("dict[object, object]", value)) != set(fields.split()):
        raise ValueError("Unexpected IPC fields.")
    return cast("dict[str, Json]", value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Invalid IPC boolean.")
    return value


def _kind(value: object) -> str:
    if not isinstance(value, dict):
        raise ValueError("Invalid IPC object.")
    return text(cast("dict[object, object]", value).get("kind"), "IPC kind", maximum=40)


def _string(value: object, *, maximum: int = MAX_TEXT) -> str:
    return text(value, "IPC text", maximum=maximum, empty=True)


def _array(value: object, *, maximum: int) -> list[Json]:
    if not isinstance(value, list) or len(cast("list[object]", value)) > maximum:
        raise ValueError("Invalid IPC list.")
    return cast("list[Json]", value)


def pack(data: dict[str, Json]) -> bytes:
    try:
        frame = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid IPC data.") from error
    if len(frame) > MAX_FRAME:
        raise ValueError("IPC frame is too large.")
    return frame


def unpack(frame: bytes) -> object:
    if not frame or len(frame) > MAX_FRAME:
        raise ValueError("Invalid IPC frame length.")
    try:
        value: object = json.loads(frame)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid IPC JSON.") from error
    return value


def _version(value: object) -> None:
    if integer(value, "IPC version") != VERSION:
        raise ValueError("Unsupported IPC version.")


def _run_data(run: RunRef) -> dict[str, Json]:
    return {"task": run.task_id, "run": run.run_id}


def _run(value: object) -> RunRef:
    data = record(value, "task run")
    return RunRef(identifier(data["task"]), identifier(data["run"]))


def context_data(context: ContextSnapshot) -> dict[str, Json]:
    return {
        "captured": context.captured, "state": context.state,
        "recent": [{
            "handle": activity.window.handle, "title": activity.window.title[:1000], "exe": activity.window.exe[:256],
            "minimized": activity.window.minimized, "started": activity.started, "seen": activity.seen,
            "agent_active": activity.agent_active,
        } for activity in context.recent[-MAX_CONTEXT:]],
    }


def decode_context(value: object) -> ContextSnapshot:
    data = record(value, "captured state recent")
    match data["state"]:
        case "active" | "toolkit" | "unavailable" | "disabled" as state:
            pass
        case _:
            raise ValueError("Invalid desktop context state.")
    entries: list[Activity] = []
    captured = timestamp(data["captured"])
    for item in _array(data["recent"], maximum=MAX_CONTEXT):
        entry = record(item, "handle title exe minimized started seen agent_active")
        started, seen = timestamp(entry["started"]), timestamp(entry["seen"])
        if started > seen or seen > captured:
            raise ValueError("Invalid context time ordering.")
        entries.append(Activity(Window(
            integer(entry["handle"], "window handle", maximum=2**64 - 1), _string(entry["title"], maximum=1000),
            _string(entry["exe"], maximum=256), _boolean(entry["minimized"]),
        ), started, seen, _boolean(entry["agent_active"])))
    return ContextSnapshot(captured, state, tuple(entries))


def _stream_state(value: object) -> StreamState:
    match value:
        case "running":
            return StreamState.RUNNING
        case "complete":
            return StreamState.COMPLETE
        case "interrupted":
            return StreamState.INTERRUPTED
        case _:
            raise ValueError("Invalid stream state.")


def _outcome_data(outcome: ToolOutcome) -> dict[str, Json]:
    match outcome:
        case Succeeded(message):
            return {"kind": "succeeded", "text": message}
        case Failed(message):
            return {"kind": "failed", "text": message}
        case ToolStatus.RUNNING | ToolStatus.CANCELLED:
            return {"kind": outcome.name.lower()}
        case _:
            assert_never(outcome)


def _outcome(value: object) -> ToolOutcome:
    kind = _kind(value)
    if kind in ("succeeded", "failed"):
        data = record(value, "kind text")
        message = _string(data["text"])
        return Succeeded(message) if kind == "succeeded" else Failed(message)
    record(value, "kind")
    match kind:
        case "running":
            return ToolStatus.RUNNING
        case "cancelled":
            return ToolStatus.CANCELLED
        case _:
            raise ValueError("Invalid tool outcome.")


def _presentation_data(event: PresentationEvent) -> dict[str, Json]:
    match event:
        case Begin(message, at, queued):
            return {"kind": "begin", "text": message, "at": at, "queued": queued}
        case Progress(message):
            return {"kind": "progress", "text": message}
        case Said(message, at) | Done(message, at) | Problem(message, at):
            return {"kind": type(event).__name__.lower(), "text": message, "at": at}
        case Finished():
            return {"kind": "finished"}
        case Answer(identity, message, at, state) | ReasoningSummary(identity, message, at, state):
            return {"kind": "answer" if isinstance(event, Answer) else "reasoning", "id": identity,
                    "text": message, "at": at, "state": state.name.lower()}
        case ToolActivity(identity, name, detail, at, outcome):
            return {"kind": "tool", "id": identity, "name": name, "detail": detail, "at": at, "outcome": _outcome_data(outcome)}
        case _:
            assert_never(event)


def _presentation(value: object) -> PresentationEvent:
    kind = _kind(value)
    match kind:
        case "begin":
            data = record(value, "kind text at queued")
            return Begin(_string(data["text"]), timestamp(data["at"]), _boolean(data["queued"]))
        case "progress":
            data = record(value, "kind text")
            return Progress(_string(data["text"]))
        case "said" | "done" | "problem":
            data = record(value, "kind text at")
            message, at = _string(data["text"]), timestamp(data["at"])
            return Said(message, at) if kind == "said" else Done(message, at) if kind == "done" else Problem(message, at)
        case "finished":
            record(value, "kind")
            return Finished()
        case "answer" | "reasoning":
            data = record(value, "kind id text at state")
            identity, message, at = identifier(data["id"]), _string(data["text"]), timestamp(data["at"])
            state = _stream_state(data["state"])
            return Answer(identity, message, at, state) if kind == "answer" else ReasoningSummary(identity, message, at, state)
        case "tool":
            data = record(value, "kind id name detail at outcome")
            return ToolActivity(identifier(data["id"]), _string(data["name"], maximum=240), _string(data["detail"]),
                                timestamp(data["at"]), _outcome(data["outcome"]))
        case _:
            raise ValueError("Unknown presentation event.")


def _meeting_data(operation: MeetingOperation) -> dict[str, Json]:
    match operation:
        case StartMeeting(source, title):
            return {"kind": "start", "source": source, "title": title}
        case MeetingCommand():
            return {"kind": operation.name.lower()}
        case _:
            assert_never(operation)


def _meeting(value: object) -> MeetingOperation:
    kind = _kind(value)
    if kind == "start":
        data = record(value, "kind source title")
        match data["source"]:
            case "both" | "microphone" as source:
                return StartMeeting(source, text(data["title"], "meeting title", maximum=240))
            case _:
                raise ValueError("Invalid meeting source.")
    record(value, "kind")
    match kind:
        case "stop":
            return MeetingCommand.STOP
        case "retry":
            return MeetingCommand.RETRY
        case "list":
            return MeetingCommand.LIST
        case "status":
            return MeetingCommand.STATUS
        case _:
            raise ValueError("Unknown meeting operation.")


def event_data(message: Envelope) -> dict[str, Json]:
    payload: dict[str, Json]
    match message.payload:
        case LogEvent(run, event):
            payload = {"kind": "log", "run": _run_data(run), "event": _presentation_data(event)}
        case ReviewEvent(run, review):
            payload = {"kind": "review", "run": _run_data(run), "task": review.task_id, "revision": review.goal_revision,
                       "request": {"id": review.request.id, "title": review.request.title, "detail": review.request.detail},
                       "fingerprint": review.fingerprint, "title": review.title, "detail": review.detail}
        case MeetingRequest(run, request_id, operation):
            payload = {"kind": "meeting", "run": _run_data(run), "request": request_id, "operation": _meeting_data(operation)}
        case BrowserAppRequest(run, request_id, operation, expires_at):
            payload = {"kind": "browser_app", "run": _run_data(run), "request": request_id,
                       "operation": app_operation_data(operation), "expires_at": expires_at}
        case BrowserAppCancelled(run, request_id):
            payload = {"kind": "browser_app_cancelled", "run": _run_data(run), "request": request_id}
        case Resync(reason):
            payload = {"kind": "resync", "reason": reason}
        case _:
            assert_never(message.payload)
    return {"epoch": message.epoch, "sequence": message.sequence, "payload": payload}


def decode_event(value: object) -> Envelope:
    data = record(value, "epoch sequence payload")
    value = data["payload"]
    payload: BridgeEvent
    match _kind(value):
        case "log":
            part = record(value, "kind run event")
            payload = LogEvent(_run(part["run"]), _presentation(part["event"]))
        case "review":
            part = record(value, "kind run task revision request fingerprint title detail")
            request = record(part["request"], "id title detail")
            run = _run(part["run"])
            review = ApprovalReview(identifier(part["task"]), integer(part["revision"], "goal revision"),
                                    Approval(identifier(request["id"]), text(request["title"], "review title", maximum=240),
                                             _string(request["detail"], maximum=16_000)),
                                    digest(part["fingerprint"]), text(part["title"], "review title", maximum=240),
                                    text(part["detail"], "review detail", maximum=MAX_TEXT))
            if run.task_id != review.task_id:
                raise ValueError("Review does not belong to its run.")
            payload = ReviewEvent(run, review)
        case "meeting":
            part = record(value, "kind run request operation")
            payload = MeetingRequest(_run(part["run"]), identifier(part["request"]), _meeting(part["operation"]))
        case "browser_app":
            part = record(value, "kind run request operation expires_at")
            payload = BrowserAppRequest(_run(part["run"]), identifier(part["request"]), app_operation_of(part["operation"]),
                                        timestamp(part["expires_at"], "browser app expiry"))
        case "browser_app_cancelled":
            part = record(value, "kind run request")
            payload = BrowserAppCancelled(_run(part["run"]), identifier(part["request"]))
        case "resync":
            part = record(value, "kind reason")
            payload = Resync(text(part["reason"], "resynchronization reason", maximum=1000))
        case _:
            raise ValueError("Unknown bridge event.")
    return Envelope(identifier(data["epoch"]), integer(data["sequence"], "event sequence", minimum=1), payload)


def _result_data(result: MeetingResult) -> dict[str, Json]:
    match result:
        case MeetingSucceeded(message):
            return {"kind": "succeeded", "text": message}
        case MeetingFailed(reason):
            return {"kind": "failed", "reason": reason}
        case _:
            assert_never(result)


def _result(value: object) -> MeetingResult:
    match _kind(value):
        case "succeeded":
            data = record(value, "kind text")
            return MeetingSucceeded(_string(data["text"]))
        case "failed":
            data = record(value, "kind reason")
            return MeetingFailed(text(data["reason"], "meeting failure", maximum=8000))
        case _:
            raise ValueError("Unknown meeting result.")


def _operation_data(operation: Operation) -> dict[str, Json]:
    match operation:
        case Hello(pid):
            return {"kind": "hello", "pid": pid}
        case Poll(after, limit):
            return {"kind": "poll", "after": after, "limit": limit}
        case Register(task_id, context, pid):
            return {"kind": "register", "task": task_id, "context": context_data(context) if context else None, "pid": pid}
        case PublishContext(context, pid):
            return {"kind": "context", "context": context_data(context), "pid": pid}
        case MeetingResponse(run, request_id, result):
            return {"kind": "response", "run": _run_data(run), "request": request_id, "result": _result_data(result)}
        case BrowserAppResponse(run, request_id, result):
            return {"kind": "browser_app_response", "run": _run_data(run), "request": request_id, "result": app_result_data(result)}
        case Command.WAKE:
            return {"kind": "wake"}
        case _:
            assert_never(operation)


def _operation(value: object) -> Operation:
    match _kind(value):
        case "hello":
            data = record(value, "kind pid")
            return Hello(integer(data["pid"], "assistant PID", minimum=1, maximum=2**32 - 1))
        case "poll":
            data = record(value, "kind after limit")
            return Poll(integer(data["after"], "event cursor"), integer(data["limit"], "poll limit", minimum=1, maximum=MAX_EVENTS))
        case "register":
            data = record(value, "kind task context pid")
            return Register(identifier(data["task"]), decode_context(data["context"]) if data["context"] is not None else None,
                            integer(data["pid"], "assistant PID", minimum=1, maximum=2**32 - 1))
        case "context":
            data = record(value, "kind context pid")
            return PublishContext(decode_context(data["context"]), integer(data["pid"], "assistant PID", minimum=1, maximum=2**32 - 1))
        case "response":
            data = record(value, "kind run request result")
            return MeetingResponse(_run(data["run"]), identifier(data["request"]), _result(data["result"]))
        case "browser_app_response":
            data = record(value, "kind run request result")
            return BrowserAppResponse(_run(data["run"]), identifier(data["request"]), app_result_of(data["result"]))
        case "wake":
            record(value, "kind")
            return Command.WAKE
        case _:
            raise ValueError("Unknown bridge command.")


def encode_request(request: Request) -> bytes:
    data: dict[str, Json] = {"version": VERSION, "epoch": request.epoch, "client": request.client,
                             "operation": _operation_data(request.operation)}
    frame = pack(data)
    decode_request(frame)
    return frame


def decode_request(frame: bytes) -> Request:
    data = record(unpack(frame), "version epoch client operation")
    _version(data["version"])
    return Request(identifier(data["epoch"]), identifier(data["client"]), _operation(data["operation"]))


def encode_reply(reply: Reply) -> bytes:
    data: dict[str, Json]
    match reply:
        case Accepted(epoch, accepted):
            data = {"version": VERSION, "kind": "accepted", "epoch": epoch, "accepted": accepted}
        case Batch(epoch, events):
            data = {"version": VERSION, "kind": "batch", "epoch": epoch, "events": [event_data(event) for event in events]}
        case Rejected(reason):
            data = {"version": VERSION, "kind": "rejected", "reason": reason}
        case _:
            assert_never(reply)
    frame = pack(data)
    decode_reply(frame)
    return frame


def decode_reply(frame: bytes) -> Reply:
    value = unpack(frame)
    match _kind(value):
        case "accepted":
            data = record(value, "version kind epoch accepted")
            _version(data["version"])
            return Accepted(identifier(data["epoch"]), _boolean(data["accepted"]))
        case "batch":
            data = record(value, "version kind epoch events")
            _version(data["version"])
            epoch = identifier(data["epoch"])
            events = tuple(decode_event(item) for item in _array(data["events"], maximum=MAX_EVENTS))
            previous = 0
            for event in events:
                if event.epoch != epoch or event.sequence <= previous:
                    raise ValueError("Unordered or stale bridge events.")
                previous = event.sequence
            return Batch(epoch, events)
        case "rejected":
            data = record(value, "version kind reason")
            _version(data["version"])
            return Rejected(text(data["reason"], "bridge rejection", maximum=1000))
        case _:
            raise ValueError("Unknown bridge reply.")
