"""Validated browser observations and pure choices for the agent's tab group."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal, NotRequired, TypedDict, assert_never, cast

from .llm import Json

MAX_ID = 2**31 - 1
type Color = Literal["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"]
COLORS: tuple[Color, ...] = ("grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange")


class PageData(TypedDict):
    id: str
    title: str
    url: str
    tab_id: NotRequired[int]
    created_in_session: NotRequired[bool]


class BrowserStatus(TypedDict):
    mode: Literal["stopped", "attached", "sign_in", "background"]
    tabs: list[PageData]
    browser: NotRequired[Literal["edge", "chrome"]]
    setup: NotRequired[str]
    total: NotRequired[int]
    offset: NotRequired[int]
    next_offset: NotRequired[int]


class TabData(TypedDict):
    tab_id: int
    window_id: int
    index: int
    group_id: int
    title: str
    url: str
    active: bool
    pinned: bool
    muted: bool


class TabRow(TabData):
    target_id: NotRequired[str]


class GroupStyle(TypedDict):
    title: str
    color: Color
    collapsed: bool


class GroupData(GroupStyle):
    group_id: int
    window_id: int
    shared: bool


class GroupProperties(TypedDict, total=False):
    title: str
    color: Color
    collapsed: bool


class StripData(TypedDict):
    mode: Literal["attached", "sign_in", "background"]
    view: Literal["strip"]
    browser: Literal["edge"]
    tabs: list[TabRow]
    groups: list[GroupData]
    total: int
    offset: int
    next_offset: NotRequired[int]


class UngroupedData(TypedDict):
    tab_ids: list[int]
    group_id: Literal[-1]


class ClosedTabsData(TypedDict):
    closed_tab_ids: list[int]
    closed_count: int
    group_id: NotRequired[int]


class PageClosureData(TypedDict):
    target_id: str
    state: Literal["closed", "requested"]


class BrowserError(OSError):
    pass


class InterruptedError(BrowserError):
    """An operation must stop, including JavaScript still running in the browser."""


class DisconnectedError(BrowserError):
    """The transport ended; an interrupted action must not be replayed."""


def mapping(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise BrowserError("The browser returned an unexpected response.")
    return value


def text(data: dict[str, Json], name: str, default: str = "") -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise BrowserError(f"{name} must be text.")
    return value


def number(data: dict[str, Json], name: str, default: int, lower: int, upper: int) -> int:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise BrowserError(f"{name} must be an integer between {lower} and {upper}.")
    return value


def boolean(data: dict[str, Json], name: str, default: bool = False) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise BrowserError(f"{name} must be a boolean.")
    return value


def tab_ids(data: dict[str, Json], *, required: bool = True, limit: int = 200) -> tuple[int, ...]:
    values = data.get("tab_ids", [])
    if (not isinstance(values, list) or len(values) > limit or (required and not values)
            or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_ID for value in values)):
        raise BrowserError(f"tab_ids must contain 1 to {limit} integer tab IDs from browser_tabs with view=strip.")
    unique: dict[int, None] = dict.fromkeys(cast(list[int], values))
    return tuple(unique)


def color_of(value: str) -> Color:
    for color in COLORS:
        if value == color:
            return color
    raise BrowserError("color must be one of " + ", ".join(COLORS) + ".")


@dataclass(frozen=True, slots=True)
class Page:
    id: str
    title: str
    url: str

    def data(self) -> PageData:
        return {"id": self.id, "title": self.title, "url": self.url}


def page_of(value: Json) -> Page:
    data = mapping(value)
    identity = text(data, "targetId")
    if not identity or len(identity) > 128:
        raise BrowserError("The browser returned an invalid page ID.")
    return Page(identity, text(data, "title")[:300], text(data, "url")[:1000])


@dataclass(frozen=True, slots=True)
class Tab:
    id: int
    window_id: int
    index: int
    group: InGroup | Ungrouped
    title: str
    url: str
    active: bool
    pinned: bool
    muted: bool

    @property
    def group_id(self) -> int:
        match self.group:
            case InGroup(identity):
                return identity
            case Ungrouped.TABS:
                return -1
            case _:
                assert_never(self.group)

    def data(self) -> TabRow:
        return {"tab_id": self.id, "window_id": self.window_id, "index": self.index, "group_id": self.group_id,
                "title": self.title, "url": self.url, "active": self.active, "pinned": self.pinned, "muted": self.muted}


def tab_of(value: Json) -> Tab:
    data = mapping(value)
    if not {"tab_id", "window_id", "index", "group_id", "title", "url", "active", "pinned", "muted"} <= data.keys():
        raise BrowserError("The browser returned an incomplete tab.")
    group = number(data, "group_id", -2, -1, MAX_ID)
    return Tab(number(data, "tab_id", -1, 0, MAX_ID), number(data, "window_id", -1, 0, MAX_ID),
               number(data, "index", -1, 0, MAX_ID), Ungrouped.TABS if group == -1 else InGroup(group),
               text(data, "title")[:300], text(data, "url")[:1000],
               boolean(data, "active"), boolean(data, "pinned"), boolean(data, "muted"))


@dataclass(frozen=True, slots=True)
class Group:
    id: int
    window_id: int
    title: str
    color: Color
    collapsed: bool
    shared: bool

    def data(self) -> GroupData:
        return {"group_id": self.id, "window_id": self.window_id, "title": self.title, "color": self.color,
                "collapsed": self.collapsed, "shared": self.shared}


def group_of(value: Json) -> Group:
    data = mapping(value)
    if not {"group_id", "window_id", "title", "color", "collapsed", "shared"} <= data.keys():
        raise BrowserError("The browser returned an incomplete tab group.")
    return Group(number(data, "group_id", -1, 0, MAX_ID), number(data, "window_id", -1, 0, MAX_ID),
                 text(data, "title")[:300], color_of(text(data, "color")), boolean(data, "collapsed"), boolean(data, "shared"))


@dataclass(frozen=True, slots=True)
class TabState:
    tabs: tuple[Tab, ...]
    groups: tuple[Group, ...]
    windows: tuple[int, ...]
    last_window: int | None


def tab_state(value: Json) -> TabState:
    data = mapping(value)
    tabs, groups, windows = data.get("tabs"), data.get("groups"), data.get("windows")
    if not isinstance(tabs, list) or not isinstance(groups, list) or not isinstance(windows, list):
        raise BrowserError("The browser did not provide its tabs, groups and windows.")
    if (len(tabs) > 20000 or len(groups) > 20000 or len(windows) > 20000
            or any(isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_ID for item in windows)):
        raise BrowserError("The browser returned an invalid tab list.")
    last = None if data.get("last_window") is None else number(data, "last_window", -1, 0, MAX_ID)
    return TabState(tuple(tab_of(tab) for tab in tabs), tuple(group_of(group) for group in groups), tuple(cast(list[int], windows)), last)


class DefaultGroup(Enum):
    TOOLKIT = auto()


class Ungrouped(Enum):
    TABS = auto()


@dataclass(frozen=True, slots=True)
class InGroup:
    id: int


@dataclass(frozen=True, slots=True)
class NewGroup:
    title: str
    color: Color
    collapsed: bool

    def data(self) -> GroupStyle:
        return {"title": self.title, "color": self.color, "collapsed": self.collapsed}


type Destination = DefaultGroup | Ungrouped | InGroup


def destination(args: dict[str, Json]) -> Destination:
    if "group_id" not in args:
        return DefaultGroup.TOOLKIT
    identity = number(args, "group_id", -2, -1, MAX_ID)
    return Ungrouped.TABS if identity == -1 else InGroup(identity)


@dataclass(frozen=True, slots=True)
class Opening:
    window_id: int
    group: NewGroup | InGroup | Ungrouped


def opening(state: TabState, requested: Destination, window: int | None) -> Opening:
    """Prefer the existing Toolkit group; never re-collapse a group the user opened."""
    if window is not None and window not in state.windows:
        raise BrowserError("That normal browser window is closed. Refresh browser_tabs with view=strip.")
    match requested:
        case InGroup(identity):
            group = next((group for group in state.groups if group.id == identity), None)
            if group is None:
                raise BrowserError("That tab group is closed. Refresh browser_tabs with view=strip.")
            if window is not None and window != group.window_id:
                raise BrowserError("window_id must match the selected group's window.")
            return Opening(group.window_id, requested)
        case DefaultGroup.TOOLKIT:
            def priority(group: Group) -> tuple[bool, int]:
                return group.window_id != state.last_window, group.id

            groups = sorted((group for group in state.groups if group.title == "Toolkit" and not group.shared
                             and (window is None or group.window_id == window)), key=priority)
            if groups:
                return Opening(groups[0].window_id, InGroup(groups[0].id))
            chosen: NewGroup | Ungrouped = NewGroup("Toolkit", "blue", True)
        case Ungrouped.TABS:
            chosen = Ungrouped.TABS
        case _:
            assert_never(requested)
    selected = window if window is not None else state.last_window
    if selected is None or selected not in state.windows:
        raise BrowserError("Open a normal Edge window before creating an agent tab.")
    return Opening(selected, chosen)


@dataclass(frozen=True, slots=True)
class StripQuery:
    offset: int
    limit: int
    window_id: int | None
    group: InGroup | Ungrouped | None


def strip_query(args: dict[str, Json]) -> StripQuery:
    identity = number(args, "group_id", -2, -1, MAX_ID) if "group_id" in args else None
    group = None if identity is None else Ungrouped.TABS if identity == -1 else InGroup(identity)
    return StripQuery(number(args, "offset", 0, 0, 20000), number(args, "limit", 20, 1, 40),
                      number(args, "window_id", -1, 0, MAX_ID) if "window_id" in args else None, group)


def strip_page(
    state: TabState, query: StripQuery, mode: Literal["attached", "sign_in", "background"], targets: Mapping[str, int], *, maximum: int,
) -> StripData:
    """Project an immutable observation into a bounded, typed response for tools or UI."""
    known = {identity: target for target, identity in targets.items()}
    tabs = tuple(tab for tab in state.tabs if (query.window_id is None or tab.window_id == query.window_id)
                 and (query.group is None or tab.group == query.group))
    page = tabs[query.offset:query.offset + query.limit]
    while True:
        rows: list[TabRow] = []
        for tab in page:
            row = tab.data()
            if tab.id in known:
                row["target_id"] = known[tab.id]
            rows.append(row)
        groups = [item.data() for item in state.groups if any(tab.group_id == item.id for tab in page)]
        result: StripData = {"mode": mode, "view": "strip", "browser": "edge", "tabs": rows, "groups": groups,
                            "total": len(tabs), "offset": query.offset}
        if query.offset + len(page) < len(tabs):
            result["next_offset"] = query.offset + len(page)
        if len(json.dumps(result, ensure_ascii=False)) <= maximum:
            return result
        if len(page) <= 1:
            raise BrowserError("The browser tab summary exceeded its output limit.")
        page = page[:-1]


@dataclass(frozen=True, slots=True)
class GroupChanges:
    title: str | None
    color: Color | None
    collapsed: bool | None

    def data(self) -> GroupProperties:
        result: GroupProperties = {}
        if self.title is not None:
            result["title"] = self.title
        if self.color is not None:
            result["color"] = self.color
        if self.collapsed is not None:
            result["collapsed"] = self.collapsed
        return result


@dataclass(frozen=True, slots=True)
class Move:
    index: int
    window_id: int | None


def move_of(args: dict[str, Json]) -> Move:
    return Move(number(args, "index", -1, -1, 20000), number(args, "window_id", -1, 0, MAX_ID) if "window_id" in args else None)


@dataclass(frozen=True, slots=True)
class CreateGroup:
    tab_ids: tuple[int, ...]
    style: NewGroup
    window_id: int | None
    move: Move | None


@dataclass(frozen=True, slots=True)
class EditGroup:
    group_id: int
    tab_ids: tuple[int, ...]
    changes: GroupChanges
    move: Move | None


type GroupRequest = CreateGroup | EditGroup


def group_request(args: dict[str, Json]) -> GroupRequest:
    existing = "group_id" in args
    identities = tab_ids(args, required=not existing)
    title = text(args, "title") if "title" in args else None
    if title is not None and len(title) > 300:
        raise BrowserError("Group titles may contain at most 300 characters.")
    color = color_of(text(args, "color")) if "color" in args else None
    collapsed = boolean(args, "collapsed") if "collapsed" in args else None
    move = move_of(args) if "index" in args or (existing and "window_id" in args) else None
    if existing:
        return EditGroup(number(args, "group_id", -1, 0, MAX_ID), identities, GroupChanges(title, color, collapsed), move)
    window = number(args, "window_id", -1, 0, MAX_ID) if "window_id" in args else None
    return CreateGroup(identities, NewGroup(title if title is not None else "Toolkit", color or "blue",
                                          collapsed if collapsed is not None else True), window, move)


class TabAction(Enum):
    ACTIVATE = "activate"
    PIN = "pin"
    UNPIN = "unpin"
    RELOAD = "reload"


@dataclass(frozen=True, slots=True)
class ChangeTab:
    tab_id: int
    action: TabAction


@dataclass(frozen=True, slots=True)
class MoveTab:
    tab_id: int
    move: Move


type TabRequest = ChangeTab | MoveTab


def tab_request(args: dict[str, Json]) -> TabRequest:
    identity = number(args, "tab_id", -1, 0, MAX_ID)
    action = text(args, "action")
    if action == "move":
        if "index" not in args:
            raise BrowserError("Moving a tab requires index; use -1 for the end of its window.")
        return MoveTab(identity, move_of(args))
    if "index" in args or "window_id" in args:
        raise BrowserError("index and window_id only apply to the move action.")
    try:
        return ChangeTab(identity, TabAction(action))
    except ValueError as error:
        raise BrowserError("Tab action must be activate, pin, unpin, move or reload.") from error


@dataclass(frozen=True, slots=True)
class UngroupTabs:
    tab_ids: tuple[int, ...]


type TabEdit = GroupRequest | TabRequest | UngroupTabs


def tab_edit(operation: str, args: dict[str, Json]) -> TabEdit:
    match operation:
        case "group":
            return group_request(args)
        case "tab":
            return tab_request(args)
        case "ungroup":
            return UngroupTabs(tab_ids(args))
        case _:
            raise BrowserError("Unknown tab operation.")


@dataclass(frozen=True, slots=True)
class ClosePage:
    target_id: str


class CloseState(Enum):
    CLOSED = "closed"
    REQUESTED = "requested"


@dataclass(frozen=True, slots=True)
class PageClosure:
    target_id: str
    state: CloseState

    def data(self) -> PageClosureData:
        return {"target_id": self.target_id, "state": self.state.value}


@dataclass(frozen=True, slots=True)
class CloseTabs:
    tab_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CloseGroup:
    group_id: int


class Disconnect(Enum):
    BROWSER = auto()


type NativeClosure = CloseTabs | CloseGroup
type Closure = ClosePage | NativeClosure | Disconnect


def closure(args: dict[str, Json]) -> Closure:
    target = text(args, "target_id")
    if bool(target) + ("tab_ids" in args) + ("group_id" in args) > 1:
        raise BrowserError("Choose target_id, tab_ids or group_id for closure, not more than one.")
    if target:
        return ClosePage(target)
    if "tab_ids" in args:
        return CloseTabs(tab_ids(args))
    if "group_id" in args:
        return CloseGroup(number(args, "group_id", -1, 0, MAX_ID))
    return Disconnect.BROWSER


@dataclass(frozen=True, slots=True)
class ClosureResult:
    request: NativeClosure
    tab_ids: tuple[int, ...]

    def data(self) -> ClosedTabsData:
        result: ClosedTabsData = {"closed_count": len(self.tab_ids), "closed_tab_ids": list(self.tab_ids[:200])}
        match self.request:
            case CloseGroup(identity):
                result["group_id"] = identity
            case CloseTabs():
                pass
            case _:
                assert_never(self.request)
        return result


@dataclass(frozen=True, slots=True)
class UngroupResult:
    tab_ids: tuple[int, ...]

    def data(self) -> UngroupedData:
        return {"tab_ids": list(self.tab_ids), "group_id": -1}
