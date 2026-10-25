"""The desktop canvas: immutable resources, arrangements and request-bound results.

No native handles are durable identities. Effects enter as observations and
request events; updating content never changes an arrangement or an editor.
"""

import math
import re
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import assert_never
from urllib.parse import urlsplit

from automation.task import variant

from .page_source_values import PageProvenance, validate_provenance, web_app_url

MAX_ITEMS = 300
CANVAS_EXTENT = 100_000
MAX_TEXT = 64_000
MAX_CONTEXT = 96_000


def bounded(value: object, label: str, maximum: int = MAX_TEXT, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\0" in value or (not empty and not value.strip()):
        raise ValueError(f"{label} must contain {'0' if empty else '1'} to {maximum:,} characters.")
    return value


@dataclass(frozen=True, slots=True)
class LiveWindow:
    handle: int
    pid: int
    app: str
    title: str


@dataclass(frozen=True, slots=True)
class SavedWindow:
    app: str
    title: str


@dataclass(frozen=True, slots=True)
class WebPage:
    url: str
    provenance: PageProvenance | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        bounded(self.url, "Page URL", 8_192)
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError("Use an HTTP or HTTPS page without embedded credentials.")
        if self.provenance is not None:
            variant(self.provenance, PageProvenance, "page provenance")
            if self.provenance.reference.url != self.url:
                raise ValueError("The page URL does not match its source provenance.")


@dataclass(frozen=True, slots=True)
class WebApp:
    """Saved browser configuration; its live document is owned by the surface."""

    url: str
    profile_id: str

    def __post_init__(self) -> None:
        web_app_url(self.url)
        profile = bounded(self.profile_id, "Managed browser profile", 64)
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile) is None:
            raise ValueError("A managed browser profile needs a lowercase identifier, not a filesystem path.")


@dataclass(frozen=True, slots=True)
class FileSource:
    path: str

    def __post_init__(self) -> None:
        bounded(self.path, "File path", 4_096)


class LocalSource(Enum):
    NOTE = auto()
    CLIPBOARD = auto()


type Origin = LiveWindow | SavedWindow | WebPage | FileSource | LocalSource


def origin_label(origin: Origin) -> str:
    match origin:
        case LiveWindow(app=app) | SavedWindow(app=app):
            return app.removesuffix(".exe")
        case WebPage(url):
            return urlsplit(url).hostname or "Web page"
        case FileSource(path):
            return path.replace("\\", "/").rsplit("/", 1)[-1]
        case LocalSource.NOTE:
            return "Your note"
        case LocalSource.CLIPBOARD:
            return "Clipboard snapshot"
        case _:
            assert_never(origin)


@dataclass(frozen=True, slots=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.columns) <= 32 or len(self.rows) > 200 or any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("A canvas table supports up to 32 columns and 200 equally sized rows.")
        bounded("\n".join("\t".join(row) for row in (self.columns, *self.rows)), "Table", empty=True)


@dataclass(frozen=True, slots=True)
class Source:
    origin: Origin
    text: str
    observed_at: float
    table: Table | None = None

    def __post_init__(self) -> None:
        bounded(self.text, "Source text", empty=True)
        if not math.isfinite(self.observed_at):
            raise ValueError("A source needs a finite observation time.")
        if isinstance(self.origin, WebPage) and self.origin.provenance is not None:
            validate_provenance(self.origin.provenance, self.text)


@dataclass(frozen=True, slots=True)
class Note:
    text: str


@dataclass(frozen=True, slots=True)
class Evidence:
    item_id: str
    revision: int
    title: str
    text: str
    origin: Origin

    def __post_init__(self) -> None:
        if isinstance(self.origin, WebPage) and self.origin.provenance is not None:
            validate_provenance(self.origin.provenance, self.text)


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    space_id: str
    result_id: str
    prompt: str
    sources: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        for identity in (self.id, self.space_id, self.result_id):
            bounded(identity, "Identity", 128)
        bounded(self.prompt, "Request", 8_000)
        if len(self.sources) > 24 or sum(len(source.text) for source in self.sources) > MAX_CONTEXT:
            raise ValueError("Select up to 24 sources with at most 96,000 characters in total.")


class Stage(Enum):
    QUEUED = auto()
    WORKING = auto()


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class Attention:
    reason: str


@dataclass(frozen=True, slots=True)
class Running:
    request: Request
    stage: Stage | Question | Approval | Attention = Stage.QUEUED
    preview: str = ""
    steps: tuple[str, ...] = ()
    label: str = ""


class Settled(Enum):
    COMPLETE = auto()
    STOPPED = auto()
    INTERRUPTED = auto()


@dataclass(frozen=True, slots=True)
class Failure:
    reason: str


class NoTaskReference(Enum):
    NONE = auto()


@dataclass(frozen=True, slots=True)
class TaskReference:
    task_id: str

    def __post_init__(self) -> None:
        bounded(self.task_id, "Task identity", 128)


type ResultReference = NoTaskReference | TaskReference


@dataclass(frozen=True, slots=True)
class Result:
    prompt: str
    text: str
    sources: tuple[Evidence, ...]
    state: Running | Settled | Failure
    steps: tuple[str, ...] = ()
    reference: ResultReference = field(default=NoTaskReference.NONE, kw_only=True)


@dataclass(frozen=True, slots=True)
class Draft:
    text: str
    source: Evidence


type Content = Source | Note | Result | Draft | WebApp


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    title: str
    content: Content
    revision: int = 1

    def __post_init__(self) -> None:
        bounded(self.id, "Item identity", 128)
        bounded(self.title, "Title", 240)
        if self.revision < 1:
            raise ValueError("A resource revision starts at one.")
        bounded(body(self.content), "Item text", empty=True)


def body(content: Content) -> str:
    match content:
        case WebApp():
            return ""
        case Source(text=text) | Note(text=text) | Result(text=text) | Draft(text=text):
            return text
        case _:
            assert_never(content)


def evidence(item: Item) -> Evidence:
    match item.content:
        case Source(origin=origin):
            pass
        case WebApp(url):
            origin = WebPage(url)
        case Note() | Result() | Draft():
            origin = LocalSource.NOTE
        case _:
            assert_never(item.content)
    return Evidence(item.id, item.revision, item.title, body(item.content), origin)


def sources(content: Content) -> tuple[Evidence, ...]:
    match content:
        case Result(sources=used, state=state):
            return state.request.sources if isinstance(state, Running) and not content.text else used
        case Draft(source=source):
            return (source,)
        case Source() | Note() | WebApp():
            return ()
        case _:
            assert_never(content)


@dataclass(frozen=True, slots=True)
class Placement:
    item_id: str
    x: float
    y: float
    width: float = 440.0
    height: float = 340.0
    parked: bool = False

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) and abs(value) <= CANVAS_EXTENT for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("Canvas coordinates must be finite and inside the canvas.")
        if not 300 <= self.width <= 1_600 or not 220 <= self.height <= 1_600:
            raise ValueError("Canvas cards must have usable dimensions.")


@dataclass(frozen=True, slots=True)
class Camera:
    x: float = 470.0
    y: float = 360.0
    zoom: float = 0.9

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) and abs(value) <= CANVAS_EXTENT for value in (self.x, self.y, self.zoom)) or not .25 <= self.zoom <= 1.5:
            raise ValueError("The canvas view must have a finite position and 25 to 150% zoom.")


@dataclass(frozen=True, slots=True)
class Space:
    id: str
    title: str
    placements: tuple[Placement, ...] = ()
    selection: tuple[str, ...] = ()
    camera: Camera = Camera()
    composer: str = ""


@dataclass(frozen=True, slots=True)
class Canvas:
    items: tuple[Item, ...] = ()
    spaces: tuple[Space, ...] = (Space("main", "My workspace"),)
    current: str = "main"
    replace_taskbar: bool = False

    @property
    def space(self) -> Space:
        return next(space for space in self.spaces if space.id == self.current)

    def item(self, identity: str) -> Item | None:
        return next((item for item in self.items if item.id == identity), None)


def with_space(canvas: Canvas, space: Space) -> Canvas:
    return replace(canvas, spaces=tuple(space if value.id == space.id else value for value in canvas.spaces))


def put(canvas: Canvas, item: Item) -> Canvas:
    if canvas.item(item.id) is None:
        if len(canvas.items) >= MAX_ITEMS:
            raise ValueError("This canvas is full. Remove unused items before adding more.")
        return replace(canvas, items=(*canvas.items, item))
    return replace(canvas, items=tuple(item if value.id == item.id else value for value in canvas.items))


def place(canvas: Canvas, placement: Placement) -> Canvas:
    if canvas.item(placement.item_id) is None:
        return canvas
    space = canvas.space
    placements = tuple(value for value in space.placements if value.item_id != placement.item_id)
    return with_space(canvas, replace(space, placements=(*placements, placement)))


def add(canvas: Canvas, item: Item, *, x: float | None = None, y: float | None = None) -> Canvas:
    current = next((p for p in canvas.space.placements if p.item_id == item.id), None)
    if current is not None:
        return place(put(canvas, item), replace(current, parked=False))
    visible = tuple(value for value in canvas.space.placements if not value.parked)
    if x is not None or y is not None:
        return place(put(canvas, item), Placement(item.id, x or 0.0, y or 0.0))
    if isinstance(item.content, (Result, Draft)):
        used = {value.item_id for value in sources(item.content)}
        parents = tuple(value for value in visible if value.item_id in used) or visible
        left = min(CANVAS_EXTENT - 450, max((value.x + value.width for value in parents), default=0) + 100)
        top = min(CANVAS_EXTENT - 590, min((value.y for value in parents), default=0))
        return place(put(canvas, item), Placement(item.id, left, top, 450, 590))
    existing = tuple(value for value in visible if (previous := canvas.item(value.item_id)) is not None
                     and isinstance(previous.content, (Source, Note, WebApp)))
    # Fill the shorter column, keeping related sources in one compact working set.
    bottoms = tuple(max((value.y + value.height + 24 for value in existing if abs(value.x - left) < 200), default=0.0)
                    for left in (0.0, 448.0))
    column = 0 if bottoms[0] <= bottoms[1] else 1
    height = 410.0 if isinstance(item.content, Source) and (item.content.table is not None
               or isinstance(item.content.origin, (LiveWindow, SavedWindow))) else 285.0
    return place(put(canvas, item), Placement(item.id, column * 448.0, min(CANVAS_EXTENT - height, bottoms[column]), 410.0, height))


def select(canvas: Canvas, identity: str, *, multiple: bool = False) -> Canvas:
    space = canvas.space
    if not any(value.item_id == identity and not value.parked for value in space.placements):
        return canvas
    chosen = tuple(value for value in space.selection if value != identity) if multiple and identity in space.selection else (
        (*space.selection, identity) if multiple else (identity,)
    )
    if len(chosen) > 24:
        raise ValueError("Select up to 24 sources for one request.")
    return with_space(canvas, replace(space, selection=chosen))


def park(canvas: Canvas, identity: str, *, parked: bool = True) -> Canvas:
    space = canvas.space
    return with_space(canvas, replace(space,
        placements=tuple(replace(value, parked=parked) if value.item_id == identity else value for value in space.placements),
        selection=tuple(value for value in space.selection if not parked or value != identity)))


def edit(canvas: Canvas, identity: str, text: str) -> Canvas:
    bounded(text, "Your text", empty=True)
    item = canvas.item(identity)
    if item is None or not isinstance(item.content, (Note, Draft)) or item.content.text == text:
        return canvas
    return put(canvas, replace(item, revision=item.revision + 1, content=replace(item.content, text=text)))


def observe(canvas: Canvas, item: Item) -> Canvas:
    """An adapter supplies stable IDs. Equal content does not invalidate evidence."""
    previous = canvas.item(item.id)
    if previous is None:
        return add(canvas, item)
    if not isinstance(previous.content, Source) or not isinstance(item.content, Source):
        raise ValueError("An app observation can update only an app source.")
    changed = (previous.title, previous.content.origin, previous.content.text, previous.content.table) != (
        item.title, item.content.origin, item.content.text, item.content.table,
    )
    return put(canvas, replace(item, revision=previous.revision + int(changed)))


def outdated(canvas: Canvas, identity: str, visited: frozenset[str] = frozenset()) -> bool:
    item = canvas.item(identity)
    if item is None or identity in visited:
        return item is None
    for source in sources(item.content):
        current = canvas.item(source.item_id)
        if current is None or current.revision != source.revision or outdated(canvas, source.item_id, visited | {identity}):
            return True
    return False


def request(canvas: Canvas, identity: str, result_id: str, prompt: str, *, refresh: bool = False) -> tuple[Canvas, Request]:
    old = canvas.item(result_id)
    if refresh and (old is None or not isinstance(old.content, Result)):
        raise ValueError("Only an existing result can be refreshed.")
    if old is not None and isinstance(old.content, Result) and isinstance(old.content.state, Running):
        raise ValueError("This result is already running.")
    chosen = tuple(value.item_id for value in sources(old.content)) if refresh and old is not None else canvas.space.selection
    selected = tuple(canvas.item(value) for value in chosen if value != result_id)
    if any(item is None for item in selected):
        raise ValueError("A source is unavailable. Select the remaining sources to start a new result.")
    used = tuple(evidence(item) for item in selected if item is not None)
    issued = Request(identity, canvas.current, result_id, prompt.strip(), used)
    content = replace(old.content, state=Running(issued)) if old is not None and isinstance(old.content, Result) else (
        Result(prompt.strip(), "", used, Running(issued)))
    item = replace(old, content=content) if old is not None else Item(result_id, prompt.strip().splitlines()[0][:120], content)
    return (put(canvas, item) if old is not None else add(canvas, item)), issued


@dataclass(frozen=True, slots=True)
class Progress:
    request_id: str
    text: str
    steps: tuple[str, ...] = ()
    label: str = ""


@dataclass(frozen=True, slots=True)
class NeedsInput:
    request_id: str
    question: Question | Approval
    problem: str = ""


@dataclass(frozen=True, slots=True)
class NeedsAttention:
    request_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class Completed:
    request_id: str
    text: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class Stopped:
    request_id: str


@dataclass(frozen=True, slots=True)
class Failed:
    request_id: str
    reason: str


type Event = Progress | NeedsInput | NeedsAttention | Completed | Stopped | Failed


def accept(canvas: Canvas, event: Event) -> Canvas:
    """Only a currently running request can change its result; late events vanish."""
    for item in canvas.items:
        content = item.content
        if not isinstance(content, Result) or not isinstance(content.state, Running) or content.state.request.id != event.request_id:
            continue
        run = content.state
        revision, title = item.revision, item.title
        match event:
            case Progress(text=text, steps=steps, label=label):
                bounded(text, "Progress", empty=True)
                if len(steps) > 128:
                    raise ValueError("A result supports at most 128 displayed steps.")
                for step in steps:
                    bounded(step, "Step", 4_000, empty=True)
                bounded(label, "Progress label", 240, empty=True)
                content = replace(content, state=replace(run, stage=Stage.WORKING, preview=text, steps=steps, label=label))
            case NeedsInput(question=question, problem=problem):
                bounded(question.id, "Question identity", 128)
                bounded(question.title, "Question", 8_000)
                bounded(question.detail, "Question detail", 128_000 if isinstance(question, Approval) else MAX_TEXT, empty=True)
                content = replace(content, state=replace(run, stage=question, label=bounded(problem, "Reply problem", 240, empty=True)))
            case NeedsAttention(reason=reason):
                content = replace(content, state=replace(run, stage=Attention(bounded(reason, "Submission problem", 8_000))))
            case Completed(text=text, title=new_title):
                bounded(text, "Result", empty=True)
                title = bounded(new_title, "Result title", 240) if new_title else title
                content = Result(run.request.prompt, text, run.request.sources, Settled.COMPLETE, run.steps,
                                 reference=TaskReference(run.request.id))
                revision += 1
            case Stopped():
                content = replace(content, state=Settled.STOPPED, steps=run.steps)
            case Failed(reason=reason):
                content = replace(content, state=Failure(bounded(reason, "Failure", 8_000)), steps=run.steps)
            case _:
                assert_never(event)
        return put(canvas, replace(item, title=title, content=content, revision=revision))
    return canvas


def draft(canvas: Canvas, source_id: str, identity: str) -> Canvas:
    source = canvas.item(source_id)
    if source is None or not body(source.content):
        raise ValueError("This item has no text to make a draft from yet.")
    position = next((value for value in canvas.space.placements if value.item_id == source_id), Placement(source_id, 0, 0))
    return add(canvas, Item(identity, f"Draft · {source.title}"[:240], Draft(body(source.content), evidence(source))),
               x=position.x + position.width + 70, y=position.y)


def remove(canvas: Canvas, identity: str) -> Canvas:
    """Remove a placement; retain referenced content so evidence stays inspectable."""
    return with_space(canvas, replace(canvas.space,
        placements=tuple(value for value in canvas.space.placements if value.item_id != identity),
        selection=tuple(value for value in canvas.space.selection if value != identity)))


def delete(canvas: Canvas, identity: str) -> Canvas:
    """Delete a saved item everywhere; other items retain their exact evidence."""
    item = canvas.item(identity)
    if item is None:
        return canvas
    if isinstance(item.content, Result) and isinstance(item.content.state, Running):
        raise ValueError("Stop this result before deleting its saved canvas item.")
    return replace(canvas, items=tuple(value for value in canvas.items if value.id != identity), spaces=tuple(
        replace(space, placements=tuple(value for value in space.placements if value.item_id != identity),
                selection=tuple(value for value in space.selection if value != identity)) for space in canvas.spaces
    ))


def search(canvas: Canvas, query: str) -> tuple[Item, ...]:
    terms = query.casefold().split()
    return tuple(item for item in canvas.items if all(term in f"{item.title}\n{body(item.content)}".casefold() for term in terms))
