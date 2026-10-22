"""Background DOM automation in an attached browser or Toolkit's own Edge."""

import base64
import binascii
import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Protocol, assert_never, cast
from urllib.parse import urlsplit

from . import browser_dom
from .llm import Json

MAX_OUTPUT = 20_000
MAX_RESPONSE = 16_000_000


class BrowserError(OSError):
    pass


class InterruptedError(BrowserError):
    """An operation must stop, including JavaScript still running in the browser."""


class DisconnectedError(BrowserError):
    """The transport ended; an interrupted action must not be replayed."""


class Product(Enum):
    EDGE = "edge"
    CHROME = "chrome"


class Separate(Enum):
    PROFILE = auto()


type Source = Product | Separate


def source_of(value: str) -> Source:
    match value:
        case "edge":
            return Product.EDGE
        case "chrome":
            return Product.CHROME
        case "separate":
            return Separate.PROFILE
        case _:
            raise BrowserError("browser must be edge, chrome or separate.")


def user_data_root(product: Product) -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise BrowserError("The Windows browser profile location is unavailable.")
    match product:
        case Product.EDGE:
            return Path(local) / "Microsoft/Edge/User Data"
        case Product.CHROME:
            return Path(local) / "Google/Chrome/User Data"
        case _:
            assert_never(product)


def setup_message(product: Product) -> str:
    return (f"Open your signed-in {product.value.title()} and {product.value}://inspect/#remote-debugging, "
            "enable 'Allow remote debugging for this browser instance', then approve its connection dialog when Toolkit connects. "
            "If your organization disables this setting, its browser policy must be respected.")


def endpoint(data: bytes) -> str:
    """Validate the browser-owned discovery file without reading profile data."""
    if len(data) > 512:
        raise BrowserError("The browser published an invalid debugging endpoint.")
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise BrowserError("The browser published an invalid debugging endpoint.") from error
    if (len(lines) != 2 or re.fullmatch(r"[0-9]{1,5}", lines[0]) is None
            or not 1 <= int(lines[0]) <= 65535 or re.fullmatch(r"/devtools/browser/[a-zA-Z0-9-]{1,128}", lines[1]) is None):
        raise BrowserError("The browser published an invalid debugging endpoint.")
    return f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"


def read_endpoint(root: Path) -> str:
    with (root / "DevToolsActivePort").open("rb") as stream:
        return endpoint(stream.read(513))


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


def edge_path() -> Path | None:
    roots = (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA"))
    candidates = [Path(root) / "Microsoft/Edge/Application/msedge.exe" for root in roots if root]
    return next((path for path in candidates if path.is_file()), None)


class Channel(Protocol):
    def send(self, message: str) -> None: ...
    def recv(self, timeout: float) -> str | bytes: ...
    def close(self) -> None: ...


class Job(Protocol):
    def close(self) -> None: ...


class Process(Protocol):
    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float) -> int: ...
    def kill(self) -> None: ...


class Cdp:
    """One serialized connection; events never get mistaken for command replies."""

    __slots__ = ("channel", "sequence")

    def __init__(self, channel: Channel) -> None:
        self.channel, self.sequence = channel, 0

    @classmethod
    def connect(cls, uri: str, cancel: threading.Event | None = None, *, timeout: float = 2) -> Cdp:
        from websockets.exceptions import WebSocketException  # noqa: PLC0415
        from websockets.sync.client import connect  # noqa: PLC0415

        try:
            parsed = urlsplit(uri)
            port = parsed.port
        except ValueError as error:
            raise BrowserError("Use a valid loopback browser debugging address.") from error
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or port is None:
            raise BrowserError("Browser debugging must stay on this computer's loopback interface.")
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Stopped before connecting to the browser.")
        transport: socket.socket | None = None
        done = threading.Event()
        watcher: threading.Thread | None = None
        try:
            if cancel is not None:
                # Approval happens before the WebSocket upgrade. Own the pending
                # socket so Stop can interrupt that wait without another attempt.
                transport = socket.create_connection(("127.0.0.1", port), timeout=min(2, timeout))
                # websockets leaves a supplied socket's timeout unchanged. Clear
                # the TCP deadline so it cannot kill approval or an idle session;
                # open_timeout and the cancellation watcher bound the handshake.
                transport.settimeout(None)
                watcher = threading.Thread(target=cancel_connection, args=(transport, cancel, done), name="browser connection", daemon=True)
                watcher.start()
            channel = connect(uri, sock=transport, proxy=None, open_timeout=timeout, close_timeout=.5,
                              max_size=MAX_RESPONSE, ping_interval=None)
            if cancel is not None and cancel.is_set():
                channel.close()
                raise InterruptedError("Stopped while waiting for the browser connection.")
        except InterruptedError:
            if transport is not None:
                transport.close()
            raise
        except (OSError, TimeoutError, WebSocketException) as error:
            if transport is not None:
                transport.close()
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Stopped while waiting for the browser connection.") from error
            raise BrowserError("The browser connection was refused, ended, or was not approved in time.") from error
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=.5)
        return cls(channel)

    def call(
        self, method: str, params: dict[str, Json], cancel: threading.Event, *, session: str = "", timeout: float = 15,
    ) -> dict[str, Json]:
        from websockets.exceptions import WebSocketException  # noqa: PLC0415

        if cancel.is_set():
            raise InterruptedError("Stopped. Inspect the page before retrying an action that may already have completed.")
        self.sequence += 1
        identity = self.sequence
        request: dict[str, Json] = {"id": identity, "method": method, "params": params}
        if session:
            request["sessionId"] = session
        deadline = time.monotonic() + timeout
        try:
            self.channel.send(json.dumps(request))
            while True:
                if cancel.is_set():
                    raise InterruptedError("Stopped. Inspect the page before retrying an action that may already have completed.")
                if time.monotonic() >= deadline:
                    raise InterruptedError("Browser operation timed out. Inspect its result before retrying; it may already have completed.")
                try:
                    raw = self.channel.recv(timeout=min(.1, max(.001, deadline - time.monotonic())))
                except TimeoutError:
                    continue
                if len(raw) > MAX_RESPONSE:
                    raise BrowserError("The browser response exceeded the size limit.")
                message = mapping(cast(Json, json.loads(raw)))
                if message.get("id") != identity:
                    continue
                if "error" in message:
                    raise BrowserError(text(mapping(message["error"]), "message", "Browser command failed.")[:500])
                return mapping(message.get("result", {}))
        except BrowserError:
            raise
        except (WebSocketException, OSError) as error:
            raise DisconnectedError("The browser connection ended. Inspect the page before retrying; "
                                    "the action may already have completed.") from error
        except ValueError as error:
            raise BrowserError("The browser returned invalid data.") from error

    def close(self) -> None:
        self.channel.close()


def cancel_connection(transport: socket.socket, cancel: threading.Event, done: threading.Event) -> None:
    while not done.wait(.05):
        if cancel.is_set():
            with contextlib.suppress(OSError):
                transport.shutdown(socket.SHUT_RDWR)
            transport.close()
            return


class Stopped(Enum):
    BROWSER = auto()


@dataclass(frozen=True, slots=True)
class Managed:
    process: Process
    job: Job | None
    visible: bool


@dataclass(frozen=True, slots=True)
class Attached:
    product: Product


type Owner = Stopped | Managed | Attached


@dataclass(frozen=True, slots=True)
class BrowserResult:
    text: str
    image: bytes | None = None


class Browser:
    """Own launched processes; borrow a personal browser through its opt-in CDP."""

    __slots__ = ("connection", "created", "executable", "lock", "owner", "profile", "sessions", "source")

    def __init__(self, profile: Path, *, source: Source = Separate.PROFILE, executable: Path | None = None) -> None:
        self.profile, self.executable = profile, executable
        self.source = source
        self.owner: Owner = Stopped.BROWSER
        self.connection: Cdp | None = None
        self.sessions: dict[str, str] = {}
        self.created: set[str] = set()
        self.lock = threading.RLock()

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
        try:
            uri = read_endpoint(user_data_root(product))
            connection = Cdp.connect(uri, cancel, timeout=60)
        except InterruptedError:
            raise
        except OSError as error:
            raise BrowserError(f"Could not attach to {product.value.title()}. {setup_message(product)}") from error
        self.owner, self.connection = Attached(product), connection

    def _call(
        self, method: str, params: dict[str, Json], cancel: threading.Event, *, session: str = "", timeout: float = 15,
    ) -> dict[str, Json]:
        if self.connection is None:
            raise BrowserError("The automation browser is stopped. Use browser_open first.")
        return self.connection.call(method, params, cancel, session=session, timeout=timeout)

    def _start(self, cancel: threading.Event, *, visible: bool = False) -> None:
        if isinstance(self.source, Product):
            self._attach(self.source, cancel)
            return
        if self.process is not None and self.process.poll() is None and self.visible == visible and self.connection is not None:
            return
        self.close()
        executable = self.executable or edge_path()
        if executable is None:
            raise BrowserError("Microsoft Edge is not installed in a standard location.")
        if cancel.is_set():
            raise InterruptedError("Stopped before starting the browser.")
        self.profile.mkdir(parents=True, exist_ok=True)
        port_file = self.profile / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        arguments = [
            str(executable), f"--user-data-dir={self.profile}", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", "--disable-background-mode", "--window-size=1280,900",
        ]
        if not visible:
            arguments.append("--headless=new")
        arguments.extend(["--new-window", "about:blank"])
        job: Job | None = None
        try:
            if sys.platform == "win32":
                from winapi.processes import ProcessJob  # noqa: PLC0415

                job = ProcessJob()
                process: Process = job.start(arguments)
            else:
                process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.owner = Managed(process, job, visible)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if cancel.wait(.05):
                    raise InterruptedError("Stopped while starting the browser.")
                if process.poll() is not None:
                    raise BrowserError("Edge exited before automation started. Its dedicated profile may already be open or blocked by policy.")
                if not port_file.is_file():
                    continue
                try:
                    uri = read_endpoint(self.profile)
                except BrowserError:
                    continue
                self.connection = Cdp.connect(uri, cancel)
                return
            raise BrowserError("Edge did not expose its debugging endpoint in time. Browser policy may prevent automation.")
        except BaseException:
            if self.owner is Stopped.BROWSER and job is not None:
                job.close()
            self.close()
            raise

    def close(self) -> None:
        """Detach personal browsers. Only terminate a process Toolkit launched."""
        with self.lock:
            if self.connection is not None:
                if isinstance(self.owner, Managed):
                    with contextlib.suppress(OSError):
                        self.connection.call("Browser.close", {}, threading.Event(), timeout=1)
                with contextlib.suppress(OSError):
                    self.connection.close()
                self.connection = None
            if isinstance(self.owner, Managed):
                process, job = self.owner.process, self.owner.job
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=2)
                if job is not None:
                    job.close()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            self.sessions.clear()
            self.created.clear()
            self.owner = Stopped.BROWSER

    def _tabs(self, cancel: threading.Event) -> list[dict[str, Json]]:
        if self.connection is None:
            return []
        targets = self._call("Target.getTargets", {}, cancel).get("targetInfos", [])
        if not isinstance(targets, list):
            raise BrowserError("Edge did not provide its page list.")
        return [
            {"id": item.get("targetId"), "title": text(item, "title")[:300], "url": text(item, "url")[:1000]}
            for item in targets if isinstance(item, dict) and item.get("type") == "page"
        ]

    def _session(self, target: str, cancel: threading.Event) -> str:
        if not target or not any(tab.get("id") == target for tab in self._tabs(cancel)):
            raise BrowserError("That page is closed or is not in the connected browser. Call browser_tabs for current ids.")
        if target not in self.sessions:
            self.sessions[target] = text(self._call("Target.attachToTarget", {"targetId": target, "flatten": True}, cancel), "sessionId")
            self._call("Page.enable", {}, cancel, session=self.sessions[target])
        return self.sessions[target]

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

    def _open(self, args: dict[str, Json], cancel: threading.Event, *, visible: bool) -> BrowserResult:
        url = web_url(text(args, "url", "about:blank"))
        if visible:
            self.select(Separate.PROFILE)
        elif "browser" in args:
            self.select(source_of(text(args, "browser")))
        switching_mode = self.connection is not None and self.visible != visible
        self._start(cancel, visible=visible)
        target = "" if switching_mode else text(args, "target_id")
        tabs = self._tabs(cancel)
        self.created.intersection_update(text(tab, "id") for tab in tabs)
        if not target:
            if isinstance(self.owner, Managed):
                target = next((text(tab, "id") for tab in tabs if tab.get("url") == "about:blank"), "")
            if not target:
                if (len(self.created) if isinstance(self.owner, Attached) else len(tabs)) >= 20:
                    raise BrowserError("Close an unused automation tab before opening another; the limit is 20.")
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
        tabs = self._tabs(cancel)
        offset = number(args, "offset", 0, 0, 20000)
        limit = number(args, "limit", 20, 1, 40)
        page: list[Json] = list(tabs[offset:offset + limit])
        if isinstance(self.owner, Attached):
            page = [{**tab, "created_in_session": text(tab, "id") in self.created} for tab in tabs[offset:offset + limit]]
        state: dict[str, Json] = {
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
        target = text(args, "target_id")
        if operation == "close":
            if target:
                self._session(target, cancel)
                self._call("Target.closeTarget", {"targetId": target}, cancel)
                self.sessions.pop(target, None)
                self.created.discard(target)
                return BrowserResult("Closed the specified page.")
            attached = isinstance(self.owner, Attached)
            self.close()
            if attached:
                return BrowserResult("Disconnected. Your browser and its tabs remain open.")
            return BrowserResult("Closed. The dedicated sign-in profile is retained.")
        if operation == "snapshot":
            return self._snapshot(target, args, cancel)
        session = self._session(target, cancel)
        if operation == "screenshot":
            result = self._call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, cancel, session=session)
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
