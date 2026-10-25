"""Bounded app-page operations; no scripts, Qt objects, or browser debug handles."""

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import assert_never, cast

from automation.task import identifier, integer, text

from .page_source_values import web_app_url

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None

MAX_RESULT = 64_000
MAX_VALUE = 64_000


def _text(value: object, label: str, maximum: int, *, empty: bool = False) -> str:
    result = text(value, label, maximum=maximum, empty=empty)
    result.encode("utf-8")
    return result


def _identity(value: object) -> str:
    result = identifier(value, "app target identity")
    if any(character.isspace() or ord(character) < 32 for character in result):
        raise ValueError("Invalid app target identity.")
    result.encode("utf-8")
    return result


def app_target_id(item_id: str, instance_id: str, document_id: str) -> str:
    """Name an exact document while retaining a stable, non-authorizing card key."""
    card = hashlib.sha256(_identity(item_id).encode("utf-8")).hexdigest()[:32]
    parts = [_identity(instance_id), _identity(document_id)]
    document = json.dumps(parts, separators=(",", ":")).encode("utf-8")
    return card + "." + hashlib.sha256(document).hexdigest()


def target_card_id(value: object) -> str | None:
    """Recovery metadata only; a host must authorize the complete live target."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}\.[0-9a-f]{64}", value) is None:
        return None
    return value[:32]


def target_id(value: object) -> str:
    result = _identity(value)
    if target_card_id(result) is None:
        raise ValueError("Use an exact target_id returned by browser_app_list or browser_app_snapshot.")
    return result


def http_url(value: object) -> str:
    return web_app_url(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("The requested checked state must be a boolean.")
    return value


def _control(target: str, control: str) -> None:
    target_id(target)
    _identity(control)


@dataclass(frozen=True, slots=True)
class ListApps:
    offset: int = 0
    limit: int = 20

    def __post_init__(self) -> None:
        integer(self.offset, "app list offset", maximum=20_000)
        integer(self.limit, "app list limit", minimum=1, maximum=40)


@dataclass(frozen=True, slots=True)
class OpenApp:
    url: str
    title: str | None = None

    def __post_init__(self) -> None:
        http_url(self.url)
        if self.title is not None:
            _text(self.title, "app title", 240)


@dataclass(frozen=True, slots=True)
class NavigateApp:
    target_id: str
    url: str

    def __post_init__(self) -> None:
        target_id(self.target_id)
        http_url(self.url)


@dataclass(frozen=True, slots=True)
class SnapshotApp:
    target_id: str
    offset: int = 0
    limit: int = 60
    text_offset: int = 0

    def __post_init__(self) -> None:
        target_id(self.target_id)
        integer(self.offset, "control offset", maximum=20_000)
        integer(self.limit, "control limit", minimum=1, maximum=100)
        integer(self.text_offset, "app text offset", maximum=10_000_000)


@dataclass(frozen=True, slots=True)
class ClickApp:
    target_id: str
    control_id: str

    def __post_init__(self) -> None:
        _control(self.target_id, self.control_id)


@dataclass(frozen=True, slots=True)
class FillApp:
    target_id: str
    control_id: str
    value: str

    def __post_init__(self) -> None:
        _control(self.target_id, self.control_id)
        _text(self.value, "app field value", MAX_VALUE, empty=True)


@dataclass(frozen=True, slots=True)
class SelectApp:
    target_id: str
    control_id: str
    value: str

    def __post_init__(self) -> None:
        _control(self.target_id, self.control_id)
        _text(self.value, "app option value", MAX_VALUE, empty=True)


@dataclass(frozen=True, slots=True)
class CheckApp:
    target_id: str
    control_id: str
    checked: bool

    def __post_init__(self) -> None:
        _control(self.target_id, self.control_id)
        _boolean(self.checked)


type AppOperation = ListApps | OpenApp | NavigateApp | SnapshotApp | ClickApp | FillApp | SelectApp | CheckApp


@dataclass(frozen=True, slots=True)
class AppSucceeded:
    text: str

    def __post_init__(self) -> None:
        _text(self.text, "app response", MAX_RESULT)


@dataclass(frozen=True, slots=True)
class AppFailed:
    reason: str

    def __post_init__(self) -> None:
        _text(self.reason, "app failure", 2_000)


class AppCancelled(Enum):
    REQUEST = auto()


type AppResult = AppSucceeded | AppFailed | AppCancelled


def _object(value: object) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("An app operation must be a JSON object.")
    return cast(dict[str, Json], value)


def _fields(value: object, names: str) -> dict[str, Json]:
    data = _object(value)
    if set(data) != set(names.split()):
        raise ValueError("The app operation has missing or unexpected fields.")
    return data


def operation_data(operation: AppOperation) -> dict[str, Json]:
    match operation:
        case ListApps(offset, limit):
            return {"kind": "list", "offset": offset, "limit": limit}
        case OpenApp(url, title):
            return {"kind": "open", "url": url, "title": title}
        case NavigateApp(target, url):
            return {"kind": "navigate", "target_id": target, "url": url}
        case SnapshotApp(target, offset, limit, text_offset):
            return {"kind": "snapshot", "target_id": target, "offset": offset, "limit": limit, "text_offset": text_offset}
        case ClickApp(target, control):
            return {"kind": "click", "target_id": target, "id": control}
        case FillApp(target, control, value):
            return {"kind": "fill", "target_id": target, "id": control, "value": value}
        case SelectApp(target, control, value):
            return {"kind": "select", "target_id": target, "id": control, "value": value}
        case CheckApp(target, control, checked):
            return {"kind": "check", "target_id": target, "id": control, "checked": checked}
        case _:
            assert_never(operation)


def operation_of(value: object) -> AppOperation:
    data = _object(value)
    match data.get("kind"):
        case "list":
            _fields(data, "kind offset limit")
            return ListApps(integer(data["offset"], "app list offset"), integer(data["limit"], "app list limit"))
        case "open":
            _fields(data, "kind url title")
            title = None if data["title"] is None else _text(data["title"], "app title", 240)
            return OpenApp(http_url(data["url"]), title)
        case "navigate":
            _fields(data, "kind target_id url")
            return NavigateApp(target_id(data["target_id"]), http_url(data["url"]))
        case "snapshot":
            _fields(data, "kind target_id offset limit text_offset")
            return SnapshotApp(target_id(data["target_id"]), integer(data["offset"], "control offset"),
                               integer(data["limit"], "control limit"), integer(data["text_offset"], "app text offset"))
        case "click":
            _fields(data, "kind target_id id")
            return ClickApp(target_id(data["target_id"]), _identity(data["id"]))
        case "fill" | "select" as kind:
            _fields(data, "kind target_id id value")
            target, control = target_id(data["target_id"]), _identity(data["id"])
            content = _text(data["value"], "app field value", MAX_VALUE, empty=True)
            return FillApp(target, control, content) if kind == "fill" else SelectApp(target, control, content)
        case "check":
            _fields(data, "kind target_id id checked")
            return CheckApp(target_id(data["target_id"]), _identity(data["id"]), _boolean(data["checked"]))
        case _:
            raise ValueError("Unknown app operation.")


def result_data(result: AppResult) -> dict[str, Json]:
    match result:
        case AppSucceeded(message):
            return {"kind": "succeeded", "text": message}
        case AppFailed(reason):
            return {"kind": "failed", "reason": reason}
        case AppCancelled.REQUEST:
            return {"kind": "cancelled"}
        case _:
            assert_never(result)


def result_of(value: object) -> AppResult:
    data = _object(value)
    match data.get("kind"):
        case "succeeded":
            _fields(data, "kind text")
            return AppSucceeded(_text(data["text"], "app response", MAX_RESULT))
        case "failed":
            _fields(data, "kind reason")
            return AppFailed(_text(data["reason"], "app failure", 2_000))
        case "cancelled":
            _fields(data, "kind")
            return AppCancelled.REQUEST
        case _:
            raise ValueError("Unknown app response.")


def _arguments(args: dict[str, Json], required: str, optional: str = "") -> None:
    if not set(required.split()) <= set(args) or set(args) - set((required + " " + optional).split()):
        raise ValueError("The app tool has missing or unexpected arguments.")


def app_operation(name: str, args: dict[str, Json]) -> AppOperation:
    """Interpret explicit app tools; unknown inputs never fall through to Edge."""
    data: dict[str, Json]
    match name:
        case "app_list":
            _arguments(args, "", "offset limit")
            data = {"kind": "list", "offset": args.get("offset", 0), "limit": args.get("limit", 20)}
        case "app_open":
            _arguments(args, "url", "title")
            data = {"kind": "open", "url": args["url"], "title": args.get("title")}
        case "app_navigate":
            _arguments(args, "target_id url")
            data = {"kind": "navigate", **args}
        case "app_snapshot":
            _arguments(args, "target_id", "offset limit text_offset")
            data = {"kind": "snapshot", "target_id": args["target_id"], "offset": args.get("offset", 0),
                    "limit": args.get("limit", 60), "text_offset": args.get("text_offset", 0)}
        case "app_action":
            match args.get("action"):
                case "click":
                    _arguments(args, "target_id id action")
                case "fill" | "select":
                    _arguments(args, "target_id id action value")
                case "check":
                    _arguments(args, "target_id id action checked")
                case _:
                    raise ValueError("App controls support click, fill, select, or check.")
            data = {"kind": args["action"], **{key: value for key, value in args.items() if key != "action"}}
        case _:
            raise ValueError("Unknown app tool operation.")
    return operation_of(data)
