"""Browser discovery, consent, and ownership of one live CDP session."""

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Protocol, assert_never, cast
from urllib.parse import urlsplit

from .browser_values import BrowserError, DisconnectedError, InterruptedError, mapping, text
from .llm import Json

MAX_RESPONSE = 16_000_000


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
    try:
        with (root / "DevToolsActivePort").open("rb") as stream:
            return endpoint(stream.read(513))
    except (FileNotFoundError, PermissionError) as error:
        # Edge can replace or briefly lock this file while publishing its port.
        # Startup already retries BrowserError under its deadline and Stop check.
        raise BrowserError("The browser debugging endpoint is not readable yet.") from error


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


def _close_process(owner: Managed) -> None:
    process, job = owner.process, owner.job
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)
    if job is not None:
        job.close()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


@dataclass(frozen=True, slots=True)
class BrowserSession:
    """A connection with its ownership; only a managed session may close a browser."""

    connection: Cdp
    owner: Managed | Attached

    def close(self) -> None:
        if isinstance(self.owner, Managed):
            with contextlib.suppress(OSError):
                self.connection.call("Browser.close", {}, threading.Event(), timeout=1)
        with contextlib.suppress(OSError):
            self.connection.close()
        if isinstance(self.owner, Managed):
            _close_process(self.owner)


class SessionOpener(Protocol):
    def open(self, source: Source, cancel: threading.Event, *, visible: bool = False) -> BrowserSession: ...


class Connector(Protocol):
    def __call__(self, uri: str, cancel: threading.Event, *, timeout: float = 2) -> Cdp: ...


class NativeSessions:
    __slots__ = ("connect", "executable", "profile", "profiles")

    def __init__(
        self, profile: Path, *, executable: Path | None = None, profiles: Callable[[Product], Path] = user_data_root,
        connect: Connector = Cdp.connect,
    ) -> None:
        self.profile, self.executable, self.profiles, self.connect = profile, executable, profiles, connect

    def open(self, source: Source, cancel: threading.Event, *, visible: bool = False) -> BrowserSession:
        match source:
            case Product():
                try:
                    connection = self.connect(read_endpoint(self.profiles(source)), cancel, timeout=60)
                except InterruptedError:
                    raise
                except OSError as error:
                    raise BrowserError(f"Could not attach to {source.value.title()}. {setup_message(source)}") from error
                return BrowserSession(connection, Attached(source))
            case Separate.PROFILE:
                return self._launch(cancel, visible=visible)
            case _:
                assert_never(source)

    def _launch(self, cancel: threading.Event, *, visible: bool) -> BrowserSession:
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
        owner: Managed | None = None
        try:
            if sys.platform == "win32":
                from winapi.processes import ProcessJob  # noqa: PLC0415

                job = ProcessJob()
                process: Process = job.start(arguments)
            else:
                process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            owner = Managed(process, job, visible)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if cancel.wait(.05):
                    raise InterruptedError("Stopped while starting the browser.")
                if process.poll() is not None:
                    raise BrowserError("Edge exited before automation started. Its dedicated profile may already be open or blocked by policy.")
                try:
                    uri = read_endpoint(self.profile)
                except BrowserError:
                    continue
                return BrowserSession(self.connect(uri, cancel), owner)
            raise BrowserError("Edge did not expose its debugging endpoint in time. Browser policy may prevent automation.")
        except BaseException:
            if owner is not None:
                _close_process(owner)
            elif job is not None:
                job.close()
            raise

