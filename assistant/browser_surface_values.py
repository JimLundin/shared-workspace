"""Immutable browser surface identities, layout observations and pure decisions.

No Qt object, browser connection, profile directory or live page is stored here.
The browser owner supplies observations and applies accepted decisions.
"""

import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import assert_never

from automation.task import identifier, integer, text, timestamp, variant

from . import canvas_model as m
from .page_source_values import MAX_TEXT, BrowserKind, Complete, Excerpt, ObservedPage, PageReference, Scope, web_app_url

MAX_COORDINATE = 128_000
MAX_CLIPS = 1_200


@dataclass(frozen=True, slots=True)
class SurfaceSpec:
    item_id: str
    app: m.WebApp

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")
        variant(self.app, m.WebApp, "saved web application")


@dataclass(frozen=True, slots=True)
class Navigate:
    item_id: str
    url: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")
        web_app_url(self.url)


@dataclass(frozen=True, slots=True)
class Back:
    item_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")


@dataclass(frozen=True, slots=True)
class Forward:
    item_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")


@dataclass(frozen=True, slots=True)
class Reload:
    item_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")


@dataclass(frozen=True, slots=True)
class Remove:
    item_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")


@dataclass(frozen=True, slots=True)
class Focus:
    item_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")


type SurfaceCommand = Navigate | Back | Forward | Reload | Remove | Focus


@dataclass(frozen=True, slots=True)
class Closed:
    item_id: str
    request_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "closed browser item")
        identifier(self.request_id, "browser close request")


@dataclass(frozen=True, slots=True)
class CloseCancelled:
    item_id: str
    request_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "closed browser item")
        identifier(self.request_id, "browser close request")


type CloseResult = Closed | CloseCancelled


def accepts_close(result: CloseResult, *, item_id: str, request_id: str) -> bool:
    match result:
        case Closed():
            return result.item_id == item_id and result.request_id == request_id
        case CloseCancelled():
            return False
        case _:
            assert_never(result)


@dataclass(frozen=True, slots=True)
class BrowserTarget:
    item_id: str
    instance_id: str
    document_id: str

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")
        identifier(self.instance_id, "browser page instance")
        identifier(self.document_id, "browser document identity")


@dataclass(frozen=True, slots=True)
class Loading:
    instance_id: str

    def __post_init__(self) -> None:
        identifier(self.instance_id, "browser page instance")


@dataclass(frozen=True, slots=True)
class Ready:
    target: BrowserTarget

    def __post_init__(self) -> None:
        variant(self.target, BrowserTarget, "browser document target")


@dataclass(frozen=True, slots=True)
class Failed:
    instance_id: str
    reason: str

    def __post_init__(self) -> None:
        identifier(self.instance_id, "browser page instance")
        text(self.reason, "browser failure", maximum=8_000)


@dataclass(frozen=True, slots=True)
class Crashed:
    instance_id: str
    reason: str

    def __post_init__(self) -> None:
        identifier(self.instance_id, "browser page instance")
        text(self.reason, "browser renderer failure", maximum=8_000)


class Dormant(Enum):
    SAVED = auto()
    CLOSED = auto()


type PageState = Loading | Ready | Failed | Crashed | Dormant


@dataclass(frozen=True, slots=True)
class BrowserObservation:
    item_id: str
    url: str
    title: str
    state: PageState
    can_back: bool = False
    can_forward: bool = False

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")
        variant(self.state, (Loading, Ready, Failed, Crashed, Dormant), "browser page state")
        text(self.url, "observed browser address", maximum=8_192, empty=not isinstance(self.state, Ready))
        self.url.encode("utf-8")
        text(self.title, "browser title", maximum=1_024, empty=True)
        self.title.encode("utf-8")
        if isinstance(self.state, Ready) and self.state.target.item_id != self.item_id:
            raise ValueError("A browser observation must belong to its exact card.")
        variant(self.can_back, bool, "browser back state")
        variant(self.can_forward, bool, "browser forward state")

    @property
    def target(self) -> BrowserTarget | None:
        match self.state:
            case Ready(target):
                return target
            case Loading() | Failed() | Crashed() | Dormant.SAVED | Dormant.CLOSED:
                return None
            case _:
                assert_never(self.state)


@dataclass(frozen=True, slots=True)
class BrowserSnapshot:
    entries: tuple[BrowserObservation, ...] = ()

    def __post_init__(self) -> None:
        variant(self.entries, tuple, "browser observations")
        if len(self.entries) > m.MAX_ITEMS:
            raise ValueError("There are too many browser observations.")
        for entry in self.entries:
            variant(entry, BrowserObservation, "browser observation")
        if len({entry.item_id for entry in self.entries}) != len(self.entries):
            raise ValueError("Browser observations must identify different cards.")


def current_target(snapshot: BrowserSnapshot, target: BrowserTarget) -> BrowserObservation | None:
    """Only an exact currently ready document can satisfy a page operation."""
    return next((entry for entry in snapshot.entries if entry.target == target), None)


@dataclass(frozen=True, slots=True)
class CapturedPage:
    target: BrowserTarget
    title: str
    url: str
    text: str
    at: float
    scope: Scope

    def __post_init__(self) -> None:
        variant(self.target, BrowserTarget, "captured browser target")
        text(self.title, "captured page title", maximum=4_096, empty=True)
        self.title.encode("utf-8")
        web_app_url(self.url)
        text(self.text, "captured page text", maximum=MAX_TEXT, empty=True)
        variant(self.scope, (Complete, Excerpt), "captured page coverage")
        units = len(self.text.encode("utf-16-le")) // 2
        if units > MAX_TEXT or (isinstance(self.scope, Excerpt) and self.scope.end_utf16 - self.scope.start_utf16 != units):
            raise ValueError("The captured text does not match its declared coverage.")
        timestamp(self.at, "page capture time")

    @property
    def observation(self) -> ObservedPage:
        reference = PageReference(BrowserKind.TOOLKIT, self.target.instance_id, self.target.document_id, self.url)
        return ObservedPage(reference, self.title, self.text, self.scope, self.at)


@dataclass(frozen=True, slots=True)
class CaptureFailed:
    target: BrowserTarget
    reason: str

    def __post_init__(self) -> None:
        variant(self.target, BrowserTarget, "captured browser target")
        text(self.reason, "page capture failure", maximum=8_000)


type CaptureResult = CapturedPage | CaptureFailed


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid browser {label}.")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"Invalid browser {label}.") from None
    if not math.isfinite(number) or abs(number) > MAX_COORDINATE:
        raise ValueError(f"Invalid browser {label}.")
    return number


@dataclass(frozen=True, slots=True)
class CssRect:
    """Shell CSS coordinates, before Qt's device-pixel conversion."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        _number(self.x, "x coordinate")
        _number(self.y, "y coordinate")
        if _number(self.width, "width") <= 0 or _number(self.height, "height") <= 0:
            raise ValueError("A browser rectangle needs positive dimensions.")
        if abs(self.right) > MAX_COORDINATE or abs(self.bottom) > MAX_COORDINATE:
            raise ValueError("A browser rectangle extends outside the supported canvas.")

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, other: CssRect) -> bool:
        return self.x <= other.x and self.y <= other.y and other.right <= self.right and other.bottom <= self.bottom


@dataclass(frozen=True, slots=True)
class SurfaceFrame:
    item_id: str
    rect: CssRect
    clips: tuple[CssRect, ...]
    zoom: float
    z: int = 0

    def __post_init__(self) -> None:
        identifier(self.item_id, "browser item identity")
        variant(self.rect, CssRect, "browser rectangle")
        variant(self.clips, tuple, "browser clipping rectangles")
        if len(self.clips) > MAX_CLIPS:
            raise ValueError("A browser surface has too many clipping rectangles.")
        for clip in self.clips:
            variant(clip, CssRect, "browser clipping rectangle")
            if not self.rect.contains(clip):
                raise ValueError("A browser clipping rectangle must stay inside its surface.")
        if not .25 <= _number(self.zoom, "zoom") <= 5:
            raise ValueError("Browser surface zoom must be between 25 and 500 percent.")
        integer(self.z, "browser surface order", maximum=m.MAX_ITEMS)


@dataclass(frozen=True, slots=True)
class Visible:
    entries: tuple[SurfaceFrame, ...]

    def __post_init__(self) -> None:
        variant(self.entries, tuple, "browser surface entries")
        if len(self.entries) > m.MAX_ITEMS:
            raise ValueError("There are too many visible browser surfaces.")
        for entry in self.entries:
            variant(entry, SurfaceFrame, "browser surface entry")
        if len({entry.item_id for entry in self.entries}) != len(self.entries):
            raise ValueError("A browser card can appear only once in a viewport.")


class Suspension(Enum):
    MODAL = auto()
    MOVING = auto()
    HIDDEN = auto()
    WORKSPACE = auto()
    SHUTDOWN = auto()


@dataclass(frozen=True, slots=True)
class Suspended:
    reason: Suspension

    def __post_init__(self) -> None:
        variant(self.reason, Suspension, "browser suspension reason")


@dataclass(frozen=True, slots=True)
class ViewportFrame:
    space_id: str
    epoch: str
    sequence: int
    viewport: CssRect
    mode: Visible | Suspended

    def __post_init__(self) -> None:
        identifier(self.space_id, "browser workspace identity")
        identifier(self.epoch, "browser viewport epoch")
        integer(self.sequence, "browser layout sequence")
        variant(self.viewport, CssRect, "browser viewport")
        variant(self.mode, (Visible, Suspended), "browser viewport mode")
        if isinstance(self.mode, Visible) and any(not self.viewport.contains(clip) for entry in self.mode.entries for clip in entry.clips):
            raise ValueError("Browser clipping rectangles must stay inside the canvas viewport.")


def accepts_frame(
    frame: ViewportFrame, *, space_id: str, epoch: str, after_sequence: int, item_ids: frozenset[str],
) -> bool:
    """A stale layout can never show surfaces in a different workspace or epoch."""
    if frame.space_id != space_id or frame.epoch != epoch or frame.sequence <= after_sequence:
        return False
    match frame.mode:
        case Visible(entries):
            return all(entry.item_id in item_ids for entry in entries)
        case Suspended():
            return True
        case _:
            assert_never(frame.mode)
