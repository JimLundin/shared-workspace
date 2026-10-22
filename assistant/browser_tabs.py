"""Edge's tab APIs, reached through a hidden, connection-owned Favorites page."""

import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict, assert_never

from .browser_values import (
    BrowserError,
    ChangeTab,
    CloseGroup,
    CloseTabs,
    ClosureResult,
    CreateGroup,
    DisconnectedError,
    EditGroup,
    Group,
    GroupProperties,
    GroupRequest,
    GroupStyle,
    InGroup,
    InterruptedError,
    Move,
    MoveTab,
    NativeClosure,
    NewGroup,
    Opening,
    Tab,
    TabRequest,
    TabState,
    Ungrouped,
    UngroupResult,
    boolean,
    group_of,
    mapping,
    tab_ids,
    tab_of,
    tab_state,
    text,
)
from .llm import Json

_PROBE = """({ready: location.protocol === 'edge:' && location.hostname === 'favorites' &&
    typeof chrome.tabs?.query === 'function' && typeof chrome.tabGroups?.update === 'function', url: location.href})"""

_COMMAND = r"""async request => {
    const tab = t => ({tab_id:t.id, window_id:t.windowId, index:t.index, group_id:t.groupId,
        title:(t.title || '').slice(0,300), url:(t.pendingUrl || t.url || '').slice(0,1000),
        active:t.active, pinned:t.pinned, muted:!!t.mutedInfo?.muted});
    const group = g => ({group_id:g.id, window_id:g.windowId, title:(g.title || '').slice(0,300),
        color:g.color, collapsed:g.collapsed, shared:!!g.shared});
    if (location.protocol !== 'edge:' || location.hostname !== 'favorites')
        throw new Error('The Edge tab controller is no longer on its browser-owned page.');
    switch (request.operation) {
        case 'list': {
            const windows = await chrome.windows.getAll({windowTypes:['normal']});
            const last = windows.length ? await chrome.windows.getLastFocused({windowTypes:['normal']}) : null;
            return {tabs:(await chrome.tabs.query({})).map(tab), groups:(await chrome.tabGroups.query({})).map(group),
                windows:windows.map(w => w.id), last_window:last?.id ?? null};
        }
        case 'open': {
            if (request.group_id !== undefined) await chrome.tabGroups.get(request.group_id);
            const created = await chrome.tabs.create({url:request.url, active:false, windowId:request.window_id});
            try {
                if (request.group_id !== undefined) {
                    await chrome.tabs.group({tabIds:[created.id], groupId:request.group_id});
                } else if (request.new_group) {
                    const id = await chrome.tabs.group({tabIds:[created.id]});
                    await chrome.tabGroups.update(id, request.new_group);
                }
                return tab(await chrome.tabs.get(created.id));
            } catch (error) {
                // Only the blank tab created by this command is rolled back.
                try { await chrome.tabs.remove(created.id); }
                catch (_) { throw new Error(`Tab ${created.id} was created but grouping failed: ${error.message}`); }
                throw error;
            }
        }
        case 'group': {
            let id = request.group_id;
            if (id !== undefined) await chrome.tabGroups.get(id);
            // Resolve every ID before making any change; stale lists do not partially regroup tabs.
            await Promise.all(request.tab_ids.map(id => chrome.tabs.get(id)));
            if (request.tab_ids.length) {
                const options = {tabIds:request.tab_ids};
                if (id !== undefined) options.groupId = id;
                else if (request.window_id !== undefined) options.createProperties = {windowId:request.window_id};
                id = await chrome.tabs.group(options);
            }
            if (Object.keys(request.properties).length) await chrome.tabGroups.update(id, request.properties);
            if (request.move) await chrome.tabGroups.move(id, request.move);
            return group(await chrome.tabGroups.get(id));
        }
        case 'ungroup':
            await Promise.all(request.tab_ids.map(id => chrome.tabs.get(id)));
            await chrome.tabs.ungroup(request.tab_ids);
            return {tab_ids:request.tab_ids, group_id:-1};
        case 'tab': {
            await chrome.tabs.get(request.tab_id);
            switch (request.action) {
                case 'activate': {
                    const selected = await chrome.tabs.update(request.tab_id, {active:true});
                    await chrome.windows.update(selected.windowId, {focused:true});
                    break;
                }
                case 'pin': await chrome.tabs.update(request.tab_id, {pinned:true}); break;
                case 'unpin': await chrome.tabs.update(request.tab_id, {pinned:false}); break;
                case 'move': await chrome.tabs.move(request.tab_id, request.move); break;
                case 'reload': await chrome.tabs.reload(request.tab_id); break;
                default: throw new Error('Unknown tab action.');
            }
            return tab(await chrome.tabs.get(request.tab_id));
        }
        case 'close':
            await Promise.all(request.tab_ids.map(id => chrome.tabs.get(id)));
            await chrome.tabs.remove(request.tab_ids);
            return {closed_tab_ids:request.tab_ids};
        case 'close_group': {
            await chrome.tabGroups.get(request.group_id);
            const ids = (await chrome.tabs.query({groupId:request.group_id})).map(t => t.id);
            if (ids.length) await chrome.tabs.remove(ids);
            return {closed_tab_ids:ids, group_id:request.group_id};
        }
        default: throw new Error('Unknown tab operation.');
    }
}"""


class Call(Protocol):
    def __call__(
        self, method: str, params: dict[str, Json], cancel: threading.Event, *, session: str = "", timeout: float = 15,
    ) -> dict[str, Json]: ...


@dataclass(frozen=True, slots=True)
class Control:
    target: str
    session: str


class MoveData(TypedDict):
    index: int
    windowId: NotRequired[int]


class ListCommand(TypedDict):
    operation: Literal["list"]


class OpenCommand(TypedDict):
    operation: Literal["open"]
    url: str
    window_id: int


class OpenInGroupCommand(OpenCommand):
    group_id: int


class OpenNewGroupCommand(OpenCommand):
    new_group: GroupStyle


class CreateGroupCommand(TypedDict):
    operation: Literal["group"]
    tab_ids: list[int]
    properties: GroupStyle
    window_id: NotRequired[int]
    move: NotRequired[MoveData]


class EditGroupCommand(TypedDict):
    operation: Literal["group"]
    group_id: int
    tab_ids: list[int]
    properties: GroupProperties
    move: NotRequired[MoveData]


type TabActionName = Literal["activate", "pin", "unpin", "reload"]


class ChangeTabCommand(TypedDict):
    operation: Literal["tab"]
    tab_id: int
    action: TabActionName


class MoveTabCommand(TypedDict):
    operation: Literal["tab"]
    tab_id: int
    action: Literal["move"]
    move: MoveData


class UngroupCommand(TypedDict):
    operation: Literal["ungroup"]
    tab_ids: list[int]


class CloseTabsCommand(TypedDict):
    operation: Literal["close"]
    tab_ids: list[int]


class CloseGroupCommand(TypedDict):
    operation: Literal["close_group"]
    group_id: int


type NativeCommand = (ListCommand | OpenCommand | OpenInGroupCommand | OpenNewGroupCommand | CreateGroupCommand | EditGroupCommand
                      | ChangeTabCommand | MoveTabCommand | UngroupCommand | CloseTabsCommand | CloseGroupCommand)


def move_data(move: Move) -> MoveData:
    result: MoveData = {"index": move.index}
    if move.window_id is not None:
        result["windowId"] = move.window_id
    return result


def group_command(request: GroupRequest) -> CreateGroupCommand | EditGroupCommand:
    match request:
        case CreateGroup(identities, style, window, move):
            created: CreateGroupCommand = {"operation": "group", "tab_ids": list(identities), "properties": style.data()}
            if window is not None:
                created["window_id"] = window
            if move is not None:
                created["move"] = move_data(move)
            return created
        case EditGroup(identity, identities, changes, move):
            edited: EditGroupCommand = {"operation": "group", "group_id": identity, "tab_ids": list(identities), "properties": changes.data()}
            if move is not None:
                edited["move"] = move_data(move)
            return edited
        case _:
            assert_never(request)


def tab_command(request: TabRequest) -> ChangeTabCommand | MoveTabCommand:
    match request:
        case ChangeTab(identity, action):
            return {"operation": "tab", "tab_id": identity, "action": action.value}
        case MoveTab(identity, move):
            return {"operation": "tab", "tab_id": identity, "action": "move", "move": move_data(move)}
        case _:
            assert_never(request)


class TabStrip:
    """Own one hidden helper, never a visible user tab or an extension installation."""

    __slots__ = ("clock", "control")

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.control: Control | None = None

    @staticmethod
    def _evaluate(call: Call, session: str, expression: str, cancel: threading.Event) -> Json:
        result = call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True}, cancel, session=session)
        if "exceptionDetails" in result:
            details = mapping(result["exceptionDetails"])
            exception = details.get("exception")
            message = text(exception, "description") if isinstance(exception, dict) else text(details, "text", "Tab operation failed.")
            raise BrowserError(message[:700] + " Inspect browser_tabs with view=strip before repeating a change.")
        return mapping(result.get("result", {})).get("value")

    def _ensure(self, call: Call, cancel: threading.Event) -> Control:
        if self.control is not None:
            return self.control
        target = text(call("Target.createTarget", {"url": "edge://favorites/", "background": True, "hidden": True}, cancel), "targetId")
        if not target:
            raise BrowserError("Edge did not create its hidden tab controller.")
        try:
            session = text(call("Target.attachToTarget", {"targetId": target, "flatten": True}, cancel), "sessionId")
            if not session:
                raise BrowserError("Edge did not attach its tab controller.")
            deadline = self.clock() + 5
            while True:
                try:
                    result = mapping(self._evaluate(call, session, _PROBE, cancel))
                    if boolean(result, "ready"):
                        self.control = Control(target, session)
                        return self.control
                except (InterruptedError, DisconnectedError):
                    raise
                except BrowserError:
                    pass  # Only this read-only probe retries while the initial context loads.
                if self.clock() >= deadline:
                    raise BrowserError("This Edge version does not expose its native tab/group APIs. "
                                       "Use browser_open with group_id=-1 for an ordinary background tab.")
                if cancel.wait(.05):
                    raise InterruptedError("Stopped while preparing Edge tab controls.")
        except BaseException:
            with contextlib.suppress(OSError):
                call("Target.closeTarget", {"targetId": target}, threading.Event(), timeout=1)
            raise

    def _command(self, call: Call, request: NativeCommand, cancel: threading.Event) -> Json:
        control = self._ensure(call, cancel)
        return self._evaluate(call, control.session, f"({_COMMAND})({json.dumps(request)})", cancel)

    def snapshot(self, call: Call, cancel: threading.Event) -> TabState:
        return tab_state(self._command(call, ListCommand(operation="list"), cancel))

    def open(self, call: Call, url: str, plan: Opening, cancel: threading.Event) -> Tab:
        request: OpenCommand | OpenInGroupCommand | OpenNewGroupCommand
        match plan.group:
            case InGroup(identity):
                request = OpenInGroupCommand(operation="open", url=url, window_id=plan.window_id, group_id=identity)
            case NewGroup() as style:
                request = OpenNewGroupCommand(operation="open", url=url, window_id=plan.window_id, new_group=style.data())
            case Ungrouped.TABS:
                request = OpenCommand(operation="open", url=url, window_id=plan.window_id)
            case _:
                assert_never(plan.group)
        return tab_of(self._command(call, request, cancel))

    def group(self, call: Call, request: GroupRequest, cancel: threading.Event) -> Group:
        return group_of(self._command(call, group_command(request), cancel))

    def tab(self, call: Call, request: TabRequest, cancel: threading.Event) -> Tab:
        return tab_of(self._command(call, tab_command(request), cancel))

    def ungroup(self, call: Call, identities: tuple[int, ...], cancel: threading.Event) -> UngroupResult:
        result = mapping(self._command(call, UngroupCommand(operation="ungroup", tab_ids=list(identities)), cancel))
        if tab_ids(result) != identities or result.get("group_id") != -1:
            raise BrowserError("The browser did not confirm the ungrouped tabs. Inspect browser_tabs before retrying.")
        return UngroupResult(identities)

    def close(self, call: Call, request: NativeClosure, cancel: threading.Event) -> ClosureResult:
        command: CloseTabsCommand | CloseGroupCommand
        match request:
            case CloseTabs(identities):
                command = CloseTabsCommand(operation="close", tab_ids=list(identities))
            case CloseGroup(identity):
                command = CloseGroupCommand(operation="close_group", group_id=identity)
            case _:
                assert_never(request)
        result = mapping(self._command(call, command, cancel))
        closed = tab_ids({"tab_ids": result.get("closed_tab_ids")}, required=False, limit=20000)
        if isinstance(request, CloseTabs) and closed != request.tab_ids:
            raise BrowserError("The browser did not confirm the closed tabs. Inspect browser_tabs before retrying.")
        return ClosureResult(request, closed)
