"""The local canvas page's validated commands and immutable view projection.

The web page renders observations. It cannot supply arbitrary native commands,
file paths or task identities to the operating system.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import assert_never, cast

from markdown_it import MarkdownIt

from . import browser_surface_values as browser
from . import canvas_model as m
from .browser_surface_values import CssRect, SurfaceFrame
from .canvas_drafts import DraftSnapshot, FailedDraft, PendingDraft, SavedDraft
from .canvas_store import integer, number, object_of, string
from .llm import Json
from .meeting_canvas import PackageRef, change_of
from .meeting_workflow import ActionChange, DraftChange, MeetingSnapshot
from .outlook import Cancelled, DraftConflict, DraftUncertain, SavedEmlDraft, SavedOutlookDraft
from .page_source_values import BrowserKind, Complete, Excerpt

NO_APPLICATIONS = browser.BrowserSnapshot()


class Control(Enum):
    CANVAS = "canvas"
    LAUNCHER = "launcher"
    SPACES = "spaces"
    SETTINGS = "settings"
    AGENT = "agent"
    WORK = "work"
    NATIVE_WORK = "native_work"
    STOP = "stop_agent"
    RECORD = "record"
    KEYBOARD = "keyboard"
    OVERLAY = "overlay"
    FILE = "add_file"
    PASTE = "paste"
    CONNECT_EDGE = "connect_edge"
    NOTE = "new_note"
    ARRANGE = "arrange"
    RESTORE_APPS = "restore_apps"
    TASKBAR = "taskbar"
    EXAMPLE = "example"
    HIDE = "hide"


class ItemAction(Enum):
    SELECT = "select"
    PARK = "park"
    RESTORE = "restore"
    OPEN = "open"
    REFRESH = "refresh"
    DRAFT = "draft"
    COPY = "copy"
    EXPORT = "export"
    REMOVE = "remove"
    DELETE = "delete"
    STOP = "stop"
    RETRY = "retry"


class WebAppAction(Enum):
    BACK = "back"
    FORWARD = "forward"
    RELOAD = "reload"
    CAPTURE = "capture"
    EXTERNAL = "external"
    ASK_AGENT = "ask_agent"


@dataclass(frozen=True, slots=True)
class CreateWebApp:
    app: m.WebApp


@dataclass(frozen=True, slots=True)
class ActOnWebApp:
    space_id: str
    identity: str
    operation: WebAppAction


@dataclass(frozen=True, slots=True)
class NavigateWebApp:
    space_id: str
    identity: str
    page: m.WebPage


@dataclass(frozen=True, slots=True)
class AppLayout:
    space_id: str
    epoch: str
    sequence: int
    mode: browser.Visible | browser.Suspended


@dataclass(frozen=True, slots=True)
class ActOnItem:
    identity: str
    operation: ItemAction


@dataclass(frozen=True, slots=True)
class WriteText:
    identity: str
    text: str


@dataclass(frozen=True, slots=True)
class MoveItem:
    space_id: str
    placement: m.Placement


@dataclass(frozen=True, slots=True)
class ViewCamera:
    space_id: str
    camera: m.Camera


@dataclass(frozen=True, slots=True)
class Compose:
    space_id: str
    text: str


@dataclass(frozen=True, slots=True)
class Submit:
    space_id: str
    text: str


@dataclass(frozen=True, slots=True)
class SwitchSpace:
    identity: str


@dataclass(frozen=True, slots=True)
class CreateSpace:
    title: str


@dataclass(frozen=True, slots=True)
class Activate:
    identity: str


@dataclass(frozen=True, slots=True)
class Answer:
    identity: str
    question_id: str
    text: str


@dataclass(frozen=True, slots=True)
class OpenLink:
    page: m.WebPage


@dataclass(frozen=True, slots=True)
class PinTask:
    identity: str


@dataclass(frozen=True, slots=True)
class SaveDraft:
    identity: str
    revision: int


@dataclass(frozen=True, slots=True)
class OpenMeeting:
    reference: PackageRef


@dataclass(frozen=True, slots=True)
class Preview:
    identity: str
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if (not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height))
                or not 0 <= self.x <= 64_000 or not 0 <= self.y <= 64_000
                or not 0 < self.width <= 64_000 or not 0 < self.height <= 64_000):
            raise ValueError("Window preview bounds must be finite, positive screen coordinates.")


@dataclass(frozen=True, slots=True)
class Previews:
    space_id: str
    entries: tuple[Preview, ...]


type Command = (Control | ActOnItem | WriteText | MoveItem | ViewCamera | Compose | Submit | SwitchSpace | CreateSpace | Activate
                | Answer | OpenLink | PinTask | SaveDraft | Previews | OpenMeeting | DraftChange | ActionChange
                | CreateWebApp | ActOnWebApp | NavigateWebApp | AppLayout)


def app_rectangle(value: Json) -> CssRect:
    data = object_of(value)
    return CssRect(number(data, "x"), number(data, "y"), number(data, "width"), number(data, "height"))


def app_layout(value: dict[str, Json]) -> AppLayout:
    entries = value.get("slots")
    if not isinstance(entries, list) or len(entries) > 12:
        raise ValueError("At most twelve applications can be visible on the canvas.")
    frames: list[SurfaceFrame] = []
    for order, entry in enumerate(entries):
        data = object_of(entry)
        clips = data.get("clips")
        if not isinstance(clips, list) or len(clips) > 48:
            raise ValueError("Application clipping is too complex.")
        frames.append(SurfaceFrame(string(data, "id", 128), app_rectangle(data.get("rect")),
                                   tuple(app_rectangle(clip) for clip in clips), number(data, "zoom"), order))
    if len({frame.item_id for frame in frames}) != len(frames):
        raise ValueError("An application can appear only once in a canvas layout.")
    mode: browser.Visible | browser.Suspended
    match value.get("mode", "visible"):
        case "visible":
            mode = browser.Visible(tuple(frames))
        case "modal" | "hidden" as reason if not frames:
            mode = browser.Suspended(browser.Suspension.MODAL if reason == "modal" else browser.Suspension.HIDDEN)
        case _:
            raise ValueError("A suspended application layout cannot display live pages.")
    return AppLayout(string(value, "space", 128), string(value, "epoch", 128), integer(value, "sequence"), mode)


def command(text: str, *, meetings: MeetingSnapshot | None = None) -> Command:
    if len(text) > 300_000:
        raise ValueError("This canvas request is too large.")
    value = object_of(cast(Json, json.loads(text)))
    kind = string(value, "kind", 40)
    match kind:
        case "new_web_app":
            return CreateWebApp(m.WebApp(string(value, "url", 8_192), "default"))
        case "web_app":
            space, identity, action = string(value, "space", 128), string(value, "id", 128), string(value, "action", 24)
            if action == "navigate":
                return NavigateWebApp(space, identity, m.WebPage(string(value, "url", 8_192)))
            return ActOnWebApp(space, identity, WebAppAction(action))
        case "app_layout":
            return app_layout(value)
        case "item":
            return ActOnItem(string(value, "id", 128), ItemAction(string(value, "action", 24)))
        case "edit":
            return WriteText(string(value, "id", 128), string(value, "text", empty=True))
        case "move":
            return MoveItem(string(value, "space", 128), m.Placement(string(value, "id", 128), number(value, "x"), number(value, "y"),
                                                                     number(value, "width"), number(value, "height")))
        case "camera":
            return ViewCamera(string(value, "space", 128), m.Camera(number(value, "x"), number(value, "y"), number(value, "zoom")))
        case "compose":
            return Compose(string(value, "space", 128), string(value, "text", 8_000, empty=True))
        case "submit":
            return Submit(string(value, "space", 128), string(value, "text", 8_000))
        case "space":
            return SwitchSpace(string(value, "id", 128))
        case "new_space":
            return CreateSpace(string(value, "title", 80))
        case "activate":
            return Activate(string(value, "id", 128))
        case "answer":
            return Answer(string(value, "id", 128), string(value, "question", 128), string(value, "text", 8_000))
        case "link":
            return OpenLink(m.WebPage(string(value, "url", 8_192)))
        case "pin_task":
            return PinTask(string(value, "id", 128))
        case "save_draft":
            return SaveDraft(string(value, "id", 128), integer(value, "revision"))
        case "meeting_open":
            return OpenMeeting(PackageRef(string(value, "task_id", 128), string(value, "version", 64)))
        case "meeting_edit":
            if meetings is None:
                raise ValueError("Meeting work is not available yet.")
            return change_of(meetings, value.get("change"))
        case "previews":
            entries = value.get("previews")
            if not isinstance(entries, list) or len(entries) > 12:
                raise ValueError("At most twelve window previews can be displayed.")
            parsed = tuple(object_of(entry) for entry in entries)
            previews = tuple(Preview(string(entry, "id", 128), number(entry, "x"), number(entry, "y"),
                                     number(entry, "width"), number(entry, "height")) for entry in parsed)
            return Previews(string(value, "space", 128), previews)
        case _:
            return Control(kind)


def meeting_space(reference: PackageRef) -> str:
    return "meeting-" + hashlib.sha256(f"{reference.task_id}:{reference.version}".encode()).hexdigest()[:24]


_HTML_CACHE: dict[str, str] = {}


def rendered_text(text: str) -> str:
    # Never interpret raw HTML from a document, agent output or saved note.
    if text in _HTML_CACHE:
        return _HTML_CACHE[text]
    parser = MarkdownIt("commonmark").disable(["html_inline", "html_block"]).enable("table")
    value: object = cast(object, parser.render(text))
    if not isinstance(value, str):
        raise TypeError("The Markdown renderer did not return HTML text.")
    if len(_HTML_CACHE) >= 128:
        _HTML_CACHE.pop(next(iter(_HTML_CACHE)))
    _HTML_CACHE[text] = value
    return value


def scope_description(origin: m.Origin) -> str:
    if not isinstance(origin, m.WebPage):
        return ""
    if origin.provenance is None:
        return "Saved page snapshot"
    match origin.provenance.scope:
        case Complete.MAIN_DOCUMENT:
            return "Main-document text"
        case Excerpt():
            return "Page excerpt · part of the main document"
        case _:
            assert_never(origin.provenance.scope)


def application_view(item: m.Item, observation: browser.BrowserObservation | None) -> dict[str, Json]:
    """Project a live page without turning its contents into source evidence."""
    if not isinstance(item.content, m.WebApp):
        raise ValueError("Only a web application has browser controls.")
    value: dict[str, Json] = {
        "url": item.content.url, "title": item.title, "loading": False,
        "live": False, "can_back": False, "can_forward": False, "can_capture": False, "error": "",
        "message": "Open this app from the canvas to continue.",
    }
    if observation is None:
        return value
    value.update(url=observation.url or item.content.url, title=observation.title or item.title,
                 live=not isinstance(observation.state, browser.Dormant),
                 can_back=observation.can_back, can_forward=observation.can_forward)
    match observation.state:
        case browser.Loading():
            value.update(loading=True, message="Opening application…")
        case browser.Ready():
            value.update(can_capture=True, message="Application ready")
        case browser.Failed(reason=reason) | browser.Crashed(reason=reason):
            value.update(error=reason, message=reason)
        case browser.Dormant.SAVED:
            value["message"] = "Open this saved app to continue."
        case browser.Dormant.CLOSED:
            value["message"] = "This application is closed. Reload it to continue."
        case _:
            assert_never(observation.state)
    return value


def source_view(content: m.Source) -> dict[str, Json]:
    origin, table = content.origin, content.table
    value: dict[str, Json] = {
        "kind": "source", "origin": m.origin_label(origin), "source_kind": "document", "icon": "doc", "scope": scope_description(origin),
    }
    match origin:
        case m.LiveWindow() | m.SavedWindow():
            value.update(source_kind="window", icon="canvas")
        case m.WebPage():
            value.update(source_kind="page", icon="globe")
            if origin.provenance is not None and origin.provenance.reference.browser is BrowserKind.TOOLKIT:
                value["captured_app"] = True
        case m.LocalSource.CLIPBOARD:
            value.update(source_kind="excerpt", icon="chat")
        case m.FileSource() | m.LocalSource.NOTE:
            pass
        case _:
            assert_never(origin)
    if table is not None:
        value.update(source_kind="table", icon="table", columns=list(table.columns), rows=[list(row) for row in table.rows])
    return value


def item_view(canvas: m.Canvas, item: m.Item, *, application: browser.BrowserObservation | None = None) -> dict[str, Json]:
    content = item.content
    value: dict[str, Json] = {
        "id": item.id, "title": item.title, "revision": item.revision, "text": m.body(content),
        "html": "" if isinstance(content, (m.Note, m.Draft)) else rendered_text(m.body(content)), "stale": m.outdated(canvas, item.id),
        "sources": [{"id": source.item_id, "revision": source.revision, "title": source.title,
                     "text": source.text, "origin": m.origin_label(source.origin), "scope": scope_description(source.origin)}
                    for source in m.sources(content)],
    }
    match content:
        case m.WebApp(url=url):
            projected = application_view(item, application)
            if application is None and any(p.item_id == item.id for p in canvas.space.placements):
                projected["message"] = "Close another app to continue here. The canvas can keep 32 apps open."
            value.update(kind="application", origin=m.origin_label(m.WebPage(url)), icon="globe", application=projected)
        case m.Source():
            value.update(source_view(content))
        case m.Note():
            value.update(kind="note", origin="Toolkit · your note", icon="chat")
        case m.Draft():
            value.update(kind="draft", origin="Toolkit · your draft", icon="mail")
        case m.Result(state=state):
            value.update(kind="result", origin="Toolkit · linked result", icon="agent", steps=list(content.steps))
            match state:
                case m.Running(stage=stage, label=label, preview=preview, steps=steps):
                    value.update(status="running", label=label or "Working…", html=rendered_text(preview or content.text), steps=list(steps))
                    match stage:
                        case m.Question(identity, title, detail):
                            value.update(status="question", question={"id": identity, "title": title, "detail": detail})
                        case m.Approval(_, title, detail):
                            value.update(status="review", review={"title": title, "detail": detail})
                        case m.Attention(reason):
                            value.update(status="attention", label=reason)
                        case m.Stage.QUEUED | m.Stage.WORKING:
                            pass
                        case _:
                            assert_never(stage)
                case m.Failure(reason):
                    value.update(status="failed", label=reason)
                case m.Settled.COMPLETE:
                    value.update(status="complete", label="Complete")
                case m.Settled.STOPPED | m.Settled.INTERRUPTED:
                    value.update(status="stopped", label="Stopped" if state is m.Settled.STOPPED else "Interrupted")
                case _:
                    assert_never(state)
        case _:
            assert_never(content)
    return value


def draft_receipts(snapshot: DraftSnapshot) -> dict[str, Json]:
    values: dict[str, Json] = {}
    for receipt in snapshot.receipts:
        draft = receipt.attempt.draft
        value: dict[str, Json] = {"revision": draft.revision}
        match receipt:
            case PendingDraft():
                value.update(status="pending", label=f"Saving v{draft.revision}…")
            case SavedDraft(result=result):
                match result:
                    case SavedOutlookDraft():
                        value.update(status="saved", label=f"v{draft.revision} saved to Outlook · unsent")
                    case SavedEmlDraft(path=path):
                        value.update(status="saved", label=f"v{draft.revision} saved as an email file · unsent", path=str(path))
                    case _:
                        assert_never(result)
            case FailedDraft(result=result):
                match result:
                    case DraftConflict(reason) | DraftUncertain(reason):
                        value.update(status="failed", label=reason)
                    case Cancelled.BEFORE_EFFECT:
                        value.update(status="failed", label="Draft saving was cancelled.")
                    case _:
                        assert_never(result)
            case _:
                assert_never(receipt)
        values[draft.item_id] = value
    return values


def snapshot(canvas: m.Canvas, status: str = "", *, applications: browser.BrowserSnapshot = NO_APPLICATIONS) -> dict[str, Json]:
    space = canvas.space
    apps = {entry.item_id: entry for entry in applications.entries}
    return {
        "space": {"id": space.id, "title": space.title, "selection": list(space.selection), "composer": space.composer,
                  "camera": {"x": space.camera.x, "y": space.camera.y, "zoom": space.camera.zoom}},
        "spaces": [{"id": value.id, "title": value.title, "count": len(value.placements)} for value in canvas.spaces],
        "placements": [{"id": value.item_id, "x": value.x, "y": value.y, "width": value.width, "height": value.height,
                        "parked": value.parked} for value in space.placements],
        "items": [item_view(canvas, item, application=apps.get(item.id)) for item in canvas.items],
        "status": status, "replace_taskbar": canvas.replace_taskbar,
    }
