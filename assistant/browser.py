"""Background DOM automation in an attached browser or Toolkit's own Edge."""

import base64
import binascii
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never
from urllib.parse import urlsplit

from . import browser_dom
from .browser_session import (
    MAX_RESPONSE,
    Attached,
    BrowserSession,
    Cdp,
    Managed,
    NativeSessions,
    Owner,
    Process,
    Product,
    Separate,
    SessionOpener,
    Source,
    Stopped,
    edge_path,
    endpoint,
    read_endpoint,
    setup_message,
    source_of,
    user_data_root,
)
from .browser_tabs import TabStrip
from .browser_values import (
    MAX_ID,
    BrowserStatus,
    ChangeTab,
    CloseGroup,
    ClosePage,
    CloseState,
    CloseTabs,
    Closure,
    CreateGroup,
    Destination,
    Disconnect,
    EditGroup,
    GroupData,
    InGroup,
    MoveTab,
    Page,
    PageClosure,
    TabData,
    TabEdit,
    Ungrouped,
    UngroupedData,
    UngroupTabs,
    closure,
    destination,
    mapping,
    number,
    opening,
    page_of,
    strip_page,
    strip_query,
    tab_edit,
    text,
)
from .browser_values import BrowserError as BrowserError  # noqa: PLC0414 (public exception re-export)
from .browser_values import DisconnectedError as DisconnectedError  # noqa: PLC0414
from .browser_values import InterruptedError as InterruptedError  # noqa: PLC0414
from .llm import Json

MAX_OUTPUT = 20_000

__all__ = [
    "MAX_OUTPUT", "MAX_RESPONSE", "Attached", "Browser", "BrowserError", "BrowserResult", "Cdp", "DisconnectedError",
    "InterruptedError", "Managed", "Product", "Separate", "Stopped", "edge_path", "endpoint", "read_endpoint", "user_data_root", "web_url",
]


def web_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise BrowserError("Use a valid HTTP(S) page URL.") from error
    if value == "about:blank":
        return value
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in value):
        raise BrowserError("Use an http(s) page URL without embedded credentials, or about:blank.")
    return value


@dataclass(frozen=True, slots=True)
class BrowserResult:
    text: str
    image: bytes | None = None


class Browser:
    """Own launched processes; borrow a personal browser through its opt-in CDP."""

    __slots__ = ("_live", "_opener", "created", "lock", "native_targets", "profile", "sessions", "source", "strip")

    def __init__(
        self, profile: Path, *, source: Source = Separate.PROFILE, executable: Path | None = None, opener: SessionOpener | None = None,
        strip: TabStrip | None = None,
    ) -> None:
        self.profile = profile
        self._opener = opener if opener is not None else NativeSessions(profile, executable=executable)
        self.source = source
        self._live: BrowserSession | None = None
        self.sessions: dict[str, str] = {}
        self.created: set[str] = set()
        self.native_targets: dict[str, int] = {}
        self.strip = strip if strip is not None else TabStrip()
        self.lock = threading.RLock()

    @property
    def connection(self) -> Cdp | None:
        return self._live.connection if self._live is not None else None

    @property
    def owner(self) -> Owner:
        return self._live.owner if self._live is not None else Stopped.BROWSER

    @property
    def process(self) -> Process | None:
        return self.owner.process if isinstance(self.owner, Managed) else None

    @property
    def visible(self) -> bool:
        return isinstance(self.owner, Managed) and self.owner.visible

    def select(self, source: Source) -> None:
        if source != self.source:
            self.close()
            self.source = source

    def _attach(self, product: Product, cancel: threading.Event) -> None:
        if self.owner == Attached(product) and self.connection is not None:
            return
        self.close()
        self._live = self._opener.open(product, cancel)

    def _call(
        self, method: str, params: dict[str, Json], cancel: threading.Event, *, session: str = "", timeout: float = 15,
    ) -> dict[str, Json]:
        if self.connection is None:
            raise BrowserError("The automation browser is stopped. Use browser_open first.")
        return self.connection.call(method, params, cancel, session=session, timeout=timeout)

    def connect(self, cancel: threading.Event, *, visible: bool = False) -> None:
        """Connect the selected source without reading tabs or navigating a page."""
        with self.lock:
            if cancel.is_set():
                raise InterruptedError("Stopped before browser automation.")
            if isinstance(self.source, Product):
                self._attach(self.source, cancel)
                return
            if self.process is not None and self.process.poll() is None and self.visible == visible and self.connection is not None:
                return
            self.close()
            self._live = self._opener.open(self.source, cancel, visible=visible)

    def close(self) -> None:
        """Detach personal browsers. Only terminate a process Toolkit launched."""
        with self.lock:
            live, self._live = self._live, None
            try:
                if live is not None:
                    live.close()
            finally:
                self.sessions.clear()
                self.created.clear()
                self.native_targets.clear()
                self.strip.control = None

    def _tabs(self, cancel: threading.Event) -> tuple[Page, ...]:
        if self.connection is None:
            return ()
        targets = self._call("Target.getTargets", {}, cancel).get("targetInfos", [])
        if not isinstance(targets, list):
            raise BrowserError("Edge did not provide its page list.")
        return tuple(page_of(item) for item in targets if isinstance(item, dict) and item.get("type") == "page")

    def _session(self, target: str, cancel: threading.Event) -> str:
        if not target or not any(tab.id == target for tab in self._tabs(cancel)):
            raise BrowserError("That page is closed or is not in the connected browser. Call browser_tabs for current ids.")
        if target not in self.sessions:
            self.sessions[target] = text(self._call("Target.attachToTarget", {"targetId": target, "flatten": True}, cancel), "sessionId")
        return self.sessions[target]

    def _reset_capture(self, session: str, cancel: threading.Event) -> None:
        try:
            self._call("Emulation.setFocusEmulationEnabled", {"enabled": False}, cancel, session=session)
        except BaseException as error:
            # Detaching also releases the browser's temporary visible capturer.
            try:
                self.close()
            except Exception as cleanup_error:
                error.add_note(f"Browser session cleanup also failed: {cleanup_error}")
            raise

    def _capture(self, session: str, cancel: threading.Event) -> dict[str, Json]:
        try:
            self._call("Emulation.setFocusEmulationEnabled", {"enabled": True}, cancel, session=session)
            result = self._call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, cancel, session=session)
        except BaseException as error:
            try:
                self._reset_capture(session, cancel)
            except Exception as cleanup_error:
                error.add_note(f"Browser capture cleanup also failed: {cleanup_error}")
            raise
        self._reset_capture(session, cancel)
        return result

    def _world(self, session: str, frame: str, cancel: threading.Event) -> tuple[int, dict[str, Json]]:
        tree = mapping(self._call("Page.getFrameTree", {}, cancel, session=session).get("frameTree", {}))
        if not frame:
            frame = text(mapping(tree.get("frame", {})), "id")
        world = self._call("Page.createIsolatedWorld", {"frameId": frame, "worldName": "toolkit"}, cancel, session=session)
        return number(world, "executionContextId", 0, 1, 2**53), tree

    def _evaluate(self, expression: str, session: str, context: int, cancel: threading.Event, *, timeout: float = 15) -> Json:
        result = self._call("Runtime.evaluate", {
            "expression": expression, "contextId": context, "awaitPromise": True, "returnByValue": True,
        }, cancel, session=session, timeout=timeout)
        if "exceptionDetails" in result:
            details = mapping(result["exceptionDetails"])
            exception = details.get("exception")
            message = text(exception, "description") if isinstance(exception, dict) else text(details, "text", "JavaScript failed.")
            raise BrowserError(message[:700])
        return mapping(result.get("result", {})).get("value")

    def _snapshot(self, target: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        session = self._session(target, cancel)
        context, tree = self._world(session, text(args, "frame_id"), cancel)
        options: dict[str, Json] = {
            "prefix": uuid.uuid4().hex[:10], "offset": number(args, "offset", 0, 0, 20000),
            "limit": number(args, "limit", 60, 1, 100), "text_offset": number(args, "text_offset", 0, 0, 10_000_000),
        }
        result = mapping(self._evaluate(f"{browser_dom.SNAPSHOT}({json.dumps(options)})", session, context, cancel))
        result["target_id"] = target
        if target in self.native_targets:
            result["tab_id"] = self.native_targets[target]
        frames: list[Json] = []
        pending = [tree]
        while pending and len(frames) < 40:
            branch = pending.pop(0)
            frame = mapping(branch.get("frame", {}))
            frames.append({"id": frame.get("id"), "url": text(frame, "url")[:300], "name": text(frame, "name")[:100]})
            children = branch.get("childFrames", [])
            if isinstance(children, list):
                pending.extend(child for child in children if isinstance(child, dict))
        frame_offset = number(args, "frame_offset", 0, 0, 20000)
        result["frames"] = frames[frame_offset:frame_offset + 8]
        result["frame_offset"] = frame_offset
        result["frame_total"] = len(frames)
        if frame_offset + 8 < len(frames):
            result["next_frame_offset"] = frame_offset + 8
        controls = result.get("controls", [])
        encoded = json.dumps(result, ensure_ascii=False)
        while len(encoded) > MAX_OUTPUT - 200 and isinstance(controls, list) and len(controls) > 1:
            controls.pop()
            result["more_controls"] = True
            encoded = json.dumps(result, ensure_ascii=False)
        while len(encoded) > MAX_OUTPUT - 200:
            page_text = text(result, "text")
            if not page_text:
                raise BrowserError("The page snapshot exceeded the output limit. Request a smaller page section.")
            result["text"] = page_text[:len(page_text) // 2]
            encoded = json.dumps(result, ensure_ascii=False)
        if isinstance(controls, list) and result.get("more_controls"):
            result["next_control_offset"] = options["offset"] + len(controls) if isinstance(options["offset"], int) else len(controls)
        next_text = number(args, "text_offset", 0, 0, 10_000_000) + len(text(result, "text"))
        if isinstance(total := result.get("text_total"), int) and next_text < total:
            result["next_text_offset"] = next_text
        return BrowserResult(json.dumps(result, ensure_ascii=False))

    def _native_target(self, placement: Destination, window: int | None, cancel: threading.Event) -> str:
        self._require_strip()
        plan = opening(self.strip.snapshot(self._call, cancel), placement, window)
        marker = "about:blank#toolkit-" + uuid.uuid4().hex
        native = self.strip.open(self._call, marker, plan, cancel)
        deadline = time.monotonic() + 5
        while True:
            target = next((tab.id for tab in self._tabs(cancel) if tab.url == marker), "")
            if target:
                self.native_targets[target] = native.id
                self.created.add(target)
                return target
            if time.monotonic() >= deadline:
                raise BrowserError(f"Created tab {native.id}, but its page is not available yet. "
                                   "Inspect browser_tabs with view=strip before opening another tab.")
            if cancel.wait(.05):
                raise InterruptedError(f"Stopped after creating tab {native.id}. The tab remains in its group.")

    def _open(self, args: dict[str, Json], cancel: threading.Event, *, visible: bool) -> BrowserResult:
        url = web_url(text(args, "url", "about:blank"))
        requested = text(args, "target_id")
        placement = destination(args)
        window = number(args, "window_id", -1, 0, MAX_ID) if "window_id" in args else None
        if requested and ("group_id" in args or window is not None):
            raise BrowserError("group_id and window_id apply to new tabs. Use browser_group or browser_tab to move an existing tab.")
        previous_source = self.source
        if visible:
            self.select(Separate.PROFILE)
        elif "browser" in args:
            self.select(source_of(text(args, "browser")))
        switching_mode = self.source != previous_source or (self.connection is not None and self.visible != visible)
        self.connect(cancel, visible=visible)
        target = "" if switching_mode else requested
        tabs = self._tabs(cancel)
        self.created.intersection_update(tab.id for tab in tabs)
        if not target:
            grouped = isinstance(self.owner, Attached) and self.owner.product is Product.EDGE and placement is not Ungrouped.TABS
            if not visible and (grouped or isinstance(placement, InGroup) or window is not None):
                target = self._native_target(placement, window, cancel)
            elif isinstance(self.owner, Managed):
                target = next((tab.id for tab in tabs if tab.url == "about:blank"), "")
            if not target:
                target = text(self._call("Target.createTarget", {"url": "about:blank", "background": not visible}, cancel), "targetId")
                self.created.add(target)
        session = self._session(target, cancel)
        navigation = self._call("Page.navigate", {"url": url}, cancel, session=session)
        if navigation.get("errorText"):
            raise BrowserError(text(navigation, "errorText"))
        if visible:
            result: dict[str, Json] = {
                "target_id": target, "mode": "sign_in", "url": url,
                "message": "Sign in in the separate Edge window. After the user finishes, browser_open resumes background work with this profile.",
            }
            return BrowserResult(json.dumps(result))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancel.wait(.1):
                raise InterruptedError("Stopped while navigating. The page may already have loaded.")
            try:
                context, tree = self._world(session, "", cancel)
                if navigation.get("loaderId") and mapping(tree.get("frame", {})).get("loaderId") != navigation["loaderId"]:
                    continue
                if self._evaluate("document.readyState", session, context, cancel) in ("interactive", "complete"):
                    return self._snapshot(target, args, cancel)
            except InterruptedError:
                raise
            except BrowserError:
                continue  # Navigating briefly destroys the previous execution context.
        raise BrowserError(f"Navigation is still loading. Inspect browser tab {target} before retrying.")

    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        with self.lock:
            try:
                return self._request(operation, args, cancel)
            except (InterruptedError, DisconnectedError):
                self.close()
                raise

    def _status(self, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        view = text(args, "view", "pages")
        if view not in ("pages", "strip"):
            raise BrowserError("view must be pages or strip.")
        if view == "pages" and ("group_id" in args or "window_id" in args):
            raise BrowserError("Tab and window filters require view=strip.")
        if view == "strip" and self.connection is not None:
            return self._strip_status(args, cancel)
        tabs = self._tabs(cancel)
        offset = number(args, "offset", 0, 0, 20000)
        limit = number(args, "limit", 20, 1, 40)
        page = [tab.data() for tab in tabs[offset:offset + limit]]
        for row in page:
            if isinstance(self.owner, Attached):
                row["created_in_session"] = row["id"] in self.created
            if row["id"] in self.native_targets:
                row["tab_id"] = self.native_targets[row["id"]]
        state: BrowserStatus = {
            "mode": "stopped" if self.connection is None else "attached" if isinstance(self.owner, Attached)
            else "sign_in" if self.visible else "background",
            "tabs": page,
        }
        if isinstance(self.source, Product):
            state["browser"] = self.source.value
            if self.connection is None:
                # Reading status never connects or triggers browser permission.
                state["setup"] = setup_message(self.source)
            state["total"] = len(tabs)
            state["offset"] = offset
        while len(json.dumps(state, ensure_ascii=False)) > MAX_OUTPUT - 100 and len(page) > 1:
            page.pop()
        if offset + len(page) < len(tabs):
            state["next_offset"] = offset + len(page)
        return BrowserResult(json.dumps(state, ensure_ascii=False))

    def _require_strip(self) -> None:
        if self.connection is None:
            raise BrowserError("Connect with browser_attach before managing tabs and groups.")
        if self.source is Product.CHROME:
            raise BrowserError("Native tab/group controls currently require Edge. Chrome supports the page tools and closing by target_id.")

    def _strip_status(self, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        query = strip_query(args)
        self._require_strip()
        state = self.strip.snapshot(self._call, cancel)
        result = strip_page(state, query, "attached" if isinstance(self.owner, Attached) else "sign_in" if self.visible else "background",
                            self.native_targets, maximum=MAX_OUTPUT)
        return BrowserResult(json.dumps(result, ensure_ascii=False))

    def _tab_operation(self, request: TabEdit, cancel: threading.Event) -> BrowserResult:
        self._require_strip()
        value: GroupData | TabData | UngroupedData
        match request:
            case CreateGroup() | EditGroup():
                value = self.strip.group(self._call, request, cancel).data()
            case ChangeTab() | MoveTab():
                value = self.strip.tab(self._call, request, cancel).data()
            case UngroupTabs(identities):
                value = self.strip.ungroup(self._call, identities, cancel).data()
            case _:
                assert_never(request)
        return BrowserResult(json.dumps(value, ensure_ascii=False))

    def _close(self, request: Closure, cancel: threading.Event) -> BrowserResult:
        match request:
            case ClosePage():
                return self._close_page(request, cancel)
            case CloseTabs() | CloseGroup():
                self._require_strip()
                closed = self.strip.close(self._call, request, cancel)
                for target, identity in tuple(self.native_targets.items()):
                    if identity in closed.tab_ids:
                        self.native_targets.pop(target)
                        self.sessions.pop(target, None)
                        self.created.discard(target)
                return BrowserResult(json.dumps(closed.data()))
            case Disconnect.BROWSER:
                attached = isinstance(self.owner, Attached)
                self.close()
                return BrowserResult("Disconnected. Your browser and its tabs remain open." if attached
                                     else "Closed. The dedicated sign-in profile is retained.")
            case _:
                assert_never(request)

    def _close_page(self, request: ClosePage, cancel: threading.Event) -> BrowserResult:
        target = request.target_id
        if not any(tab.id == target for tab in self._tabs(cancel)):
            raise BrowserError("That page is closed or is not in the connected browser. Call browser_tabs for current ids.")
        result = self._call("Target.closeTarget", {"targetId": target}, cancel)
        if result.get("success") is not True:
            raise BrowserError("The browser did not accept the tab's close request. Inspect browser_tabs before retrying.")
        deadline = time.monotonic() + 3
        while any(tab.id == target for tab in self._tabs(cancel)):
            if time.monotonic() >= deadline:
                return BrowserResult(json.dumps(PageClosure(target, CloseState.REQUESTED).data()))
            if cancel.wait(.05):
                raise InterruptedError("Stopped while waiting for the tab to close. Inspect browser_tabs before retrying.")
        self.sessions.pop(target, None)
        self.created.discard(target)
        self.native_targets.pop(target, None)
        return BrowserResult(json.dumps(PageClosure(target, CloseState.CLOSED).data()))

    def _attach_request(self, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        source = source_of(text(args, "browser", self.source.value if isinstance(self.source, Product) else "edge"))
        if not isinstance(source, Product):
            raise BrowserError("Choose edge or chrome to attach to your signed-in browser.")
        self.select(source)
        self._attach(source, cancel)
        return self._status(args, cancel)

    def _request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> BrowserResult:
        if cancel.is_set():
            raise InterruptedError("Stopped before browser automation.")
        if operation in ("open", "sign_in"):
            return self._open(args, cancel, visible=operation == "sign_in")
        if operation == "attach":
            return self._attach_request(args, cancel)
        if operation == "tabs":
            return self._status(args, cancel)
        if operation in ("group", "ungroup", "tab"):
            return self._tab_operation(tab_edit(operation, args), cancel)
        if operation == "close":
            return self._close(closure(args), cancel)
        target = text(args, "target_id")
        if operation == "snapshot":
            return self._snapshot(target, args, cancel)
        session = self._session(target, cancel)
        if operation == "screenshot":
            result = self._capture(session, cancel)
            try:
                image = base64.b64decode(text(result, "data"), validate=True)
                if len(image) < 33 or image[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
                    raise ValueError("Invalid PNG header")
                width, height = int.from_bytes(image[16:20], "big"), int.from_bytes(image[20:24], "big")
                if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > 16_000_000:
                    raise ValueError("Oversized PNG")
            except (binascii.Error, ValueError) as error:
                raise BrowserError("Edge returned an invalid or oversized viewport image.") from error
            return BrowserResult("Browser viewport screenshot. Use browser controls; these are not desktop mouse coordinates.", image)
        context, _tree = self._world(session, text(args, "frame_id"), cancel)
        if operation == "evaluate":
            expression = text(args, "expression")
            if not expression.strip() or len(expression) > 60_000:
                raise BrowserError("Provide a JavaScript expression of at most 60,000 characters.")
            wrapped = (
                "(async () => { const value = await (" + expression + "); const text = JSON.stringify(value ?? null) ?? String(value); "
                f"return text.length > {MAX_OUTPUT} ? text.slice(0, {MAX_OUTPUT - 30}) + '\\n[output truncated]' : text;" + "})()"
            )
            value = self._evaluate(wrapped, session, context, cancel, timeout=number(args, "timeout_seconds", 15, 1, 30))
            return BrowserResult(value if isinstance(value, str) else json.dumps(value))
        if operation == "action":
            action = text(args, "action")
            if action not in ("click", "fill", "select", "check"):
                raise BrowserError("Use click, fill, select or check with an id from the latest browser snapshot.")
            options: dict[str, Json] = {
                "id": text(args, "id"), "action": action, "value": text(args, "value"), "checked": args.get("checked", True),
                "dom_click": bool(text(args, "frame_id")),
            }
            if not isinstance(options["checked"], bool) or len(text(options, "value")) > 60_000:
                raise BrowserError("checked must be a boolean and field values may contain at most 60,000 characters.")
            result = mapping(self._evaluate(f"{browser_dom.ACTION}({json.dumps(options)})", session, context, cancel))
            if action == "click" and not options["dom_click"]:
                for kind in ("mousePressed", "mouseReleased"):
                    self._call("Input.dispatchMouseEvent", {
                        "type": kind, "x": result.get("x"), "y": result.get("y"), "button": "left", "clickCount": 1,
                    }, cancel, session=session)
            return BrowserResult("Browser action completed. Read a fresh browser_snapshot to verify the result.")
        raise BrowserError("Unknown browser operation.")
