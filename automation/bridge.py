"""Authenticated, deadline-bounded local exchanges and a volatile event outbox.

The worker owns one listening thread. Each connection performs one byte-framed
JSON request/reply; neither process owns a permanently blocked writer. The
discovery file contains endpoint metadata only, never context or model output.
"""

import contextlib
import hashlib
import hmac
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Generator
from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass, replace
from multiprocessing.connection import Listener
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Final, Protocol, assert_never

from assistant.activity import ContextSnapshot
from assistant.agent_session import Answer, Progress, ReasoningSummary, StreamState, ToolActivity, ToolStatus
from assistant.browser_surface_operations import AppCancelled, AppFailed, AppOperation, AppResult

from . import ipc
from .task import RunRef, digest, identifier, integer

if sys.platform == "win32":
    import _winapi
    import msvcrt
    from multiprocessing.connection import PipeConnection
else:
    import fcntl
    from multiprocessing.connection import Connection

MAX_BUFFER_BYTES: Final = 4 * ipc.MAX_FRAME
MAX_REGISTRATIONS: Final = 128
CLIENT_FRESH_SECONDS: Final = 10


class Channel(Protocol):
    def send_bytes(self, buf: bytes) -> None: ...
    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...
    def fileno(self) -> int: ...
    def close(self) -> None: ...


class LocalChannel:
    """Serialize close/cancellation so a recycled native handle is never closed twice."""

    __slots__ = ("_closed", "_lock", "connection")

    def __init__(self, connection: Channel) -> None:
        self.connection = connection
        self._lock, self._closed = threading.Lock(), False

    def send_bytes(self, buf: bytes) -> None:
        self.connection.send_bytes(buf)

    def recv_bytes(self, maxlength: int | None = None) -> bytes:
        return self.connection.recv_bytes(maxlength)

    def fileno(self) -> int:
        return self.connection.fileno()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # A Unix close alone does not wake another thread blocked in read().
            if sys.platform != "win32":
                with contextlib.suppress(OSError, ValueError):
                    duplicate = os.dup(self.connection.fileno())
                    with socket.socket(fileno=duplicate) as stream:
                        stream.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError, ValueError):
                self.connection.close()


class ProfileLease:
    """One OS-released owner, acquired before opening/recovering the task store."""

    __slots__ = ("_file", "folder")

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._file: BinaryIO | None = None

    def __enter__(self) -> ProfileLease:
        if self._file is not None:
            raise RuntimeError("The worker ownership lease is already held.")
        self.folder.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.folder / "worker.lock", os.O_RDWR | os.O_CREAT, 0o600)
        file = os.fdopen(descriptor, "r+b")
        try:
            if not file.read(1):
                file.write(b"0")
                file.flush()
            file.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            file.close()
            raise
        self._file = file
        return self

    def close(self) -> None:
        file, self._file = self._file, None
        if file is not None:
            try:
                file.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(file, fcntl.LOCK_UN)
            finally:
                file.close()

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class Discovery:
    epoch: str
    pid: int
    address: str
    key: bytes


def _address(folder: Path, epoch: str) -> str:
    profile = hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:16]
    name = f"toolkit-work-{profile}-{epoch}"
    return rf"\\.\pipe\{name}" if sys.platform == "win32" else str(Path(tempfile.gettempdir()) / f"{name}.sock")


def read_discovery(folder: Path) -> Discovery:
    """Read and constrain a local endpoint without exposing its authentication key."""
    with (folder / "worker.json").open("rb") as file:
        frame = file.read(4097)
    if len(frame) > 4096:
        raise ValueError("Worker discovery is too large.")
    data = ipc.record(ipc.unpack(frame), "version epoch pid address key")
    if integer(data["version"], "discovery version") != ipc.VERSION:
        raise ValueError("Unsupported worker discovery version.")
    epoch = identifier(data["epoch"])
    if len(epoch) != 32 or any(char not in "0123456789abcdef" for char in epoch):
        raise ValueError("Invalid worker epoch.")
    address = _address(folder, epoch)
    if data["address"] != address:
        raise ValueError("Worker discovery does not identify this local profile.")
    return Discovery(epoch, integer(data["pid"], "worker PID", minimum=1, maximum=2**32 - 1), address,
                     bytes.fromhex(digest(data["key"], "worker authentication key")))


def _publish_discovery(folder: Path, discovery: Discovery) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    frame = ipc.pack({"version": ipc.VERSION, "epoch": discovery.epoch, "pid": discovery.pid,
                      "address": discovery.address, "key": discovery.key.hex()})
    descriptor, temporary = tempfile.mkstemp(prefix=".worker-", dir=folder)
    path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(frame)
            file.flush()
            os.fsync(file.fileno())
        path.replace(folder / "worker.json")
    finally:
        path.unlink(missing_ok=True)


def _connect_raw(address: str, timeout_s: float) -> Channel:
    if sys.platform == "win32":
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The local work process did not accept a connection.")
            try:
                _winapi.WaitNamedPipe(address, max(1, min(50, int(remaining * 1000))))
                handle = _winapi.CreateFile(address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                                            0, 0, _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, 0)
            except OSError as error:
                if error.winerror in (_winapi.ERROR_SEM_TIMEOUT, _winapi.ERROR_PIPE_BUSY):
                    continue
                raise
            try:
                _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
            except BaseException:
                _winapi.CloseHandle(handle)
                raise
            connection: PipeConnection[object, object] = PipeConnection(handle)
            return LocalChannel(connection)
    else:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(timeout_s)
            stream.connect(address)
            stream.settimeout(None)
            connection: Connection[object, object] = Connection(stream.detach())
            return LocalChannel(connection)


def _abort(channel: Channel) -> None:
    with contextlib.suppress(OSError, ValueError):
        channel.close()


@contextlib.contextmanager
def _deadline(channel: Channel, seconds: float) -> Generator[Channel]:
    timer = threading.Timer(seconds, _abort, (channel,))
    timer.name, timer.daemon = "work IPC deadline", True
    timer.start()
    try:
        yield channel
    finally:
        timer.cancel()
        timer.join()
        _abort(channel)


def _server_auth(channel: Channel, key: bytes) -> None:
    nonce = secrets.token_bytes(32)
    channel.send_bytes(b"Toolkit1:" + nonce)
    answer = channel.recv_bytes(128)
    if len(answer) != 64 or not hmac.compare_digest(answer[32:], hmac.digest(key, b"client:" + nonce + answer[:32], "sha256")):
        raise OSError("Work IPC authentication failed.")
    channel.send_bytes(hmac.digest(key, b"server:" + nonce + answer[:32], "sha256"))


def _client_auth(channel: Channel, key: bytes) -> None:
    challenge = channel.recv_bytes(128)
    if len(challenge) != 41 or not challenge.startswith(b"Toolkit1:"):
        raise OSError("Work IPC authentication failed.")
    nonce = secrets.token_bytes(32)
    channel.send_bytes(nonce + hmac.digest(key, b"client:" + challenge[9:] + nonce, "sha256"))
    answer = channel.recv_bytes(128)
    if not hmac.compare_digest(answer, hmac.digest(key, b"server:" + challenge[9:] + nonce, "sha256")):
        raise OSError("Work IPC authentication failed.")


@dataclass(frozen=True, slots=True)
class Queued:
    envelope: ipc.Envelope
    size: int


@dataclass(frozen=True, slots=True)
class Outbox:
    sequence: int = 0
    acknowledged: int = 0
    entries: tuple[Queued, ...] = ()


def _update_key(payload: ipc.BridgeEvent) -> tuple[RunRef, str, str] | None:
    match payload:
        case ipc.LogEvent(run, Progress()):
            return run, "progress", ""
        case ipc.LogEvent(run, Answer(identity)):
            return run, "answer", identity
        case ipc.LogEvent(run, ReasoningSummary(identity)):
            return run, "reasoning", identity
        case ipc.LogEvent(run, ToolActivity(identity)):
            return run, "tool", identity
        case _:
            return None


def _replaceable(payload: ipc.BridgeEvent) -> bool:
    match payload:
        case ipc.LogEvent(event=Progress() | Answer(state=StreamState.RUNNING) | ReasoningSummary(state=StreamState.RUNNING)):
            return True
        case ipc.LogEvent(event=ToolActivity(outcome=ToolStatus.RUNNING)):
            return True
        case _:
            return False


def enqueue(state: Outbox, epoch: str, payload: ipc.BridgeEvent) -> tuple[Outbox, bool]:
    """Coalesce unfinished updates; explicitly reset after any other data loss."""
    message = ipc.Envelope(epoch, state.sequence + 1, payload)
    data = ipc.event_data(message)
    ipc.decode_event(data)
    size = len(ipc.pack(data))
    if size > ipc.MAX_FRAME - 4096:
        raise ValueError("The event is too large for a bounded IPC reply.")
    key = _update_key(payload)
    entries = tuple(item for item in state.entries if not (
        key is not None and _replaceable(item.envelope.payload) and _update_key(item.envelope.payload) == key
    ))
    lost = len(entries) >= ipc.MAX_EVENTS or sum(item.size for item in entries) + size > MAX_BUFFER_BYTES
    if lost:
        resync = ipc.Envelope(epoch, state.sequence + 1, ipc.Resync("Live updates were discarded; reload work and request fresh approval previews."))
        entries = (Queued(resync, len(ipc.pack(ipc.event_data(resync)))),)
        message = replace(message, sequence=state.sequence + 2)
    return Outbox(message.sequence, state.acknowledged, (*entries, Queued(message, size))), lost


@dataclass(slots=True)
class PendingMeeting:
    run: RunRef
    client: str
    result: Future[ipc.MeetingResult]


class StopSignal(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class PendingBrowserApp:
    run: RunRef
    client: str
    result: Future[AppResult]
    stop: StopSignal
    expires_at: float


class BridgeServer:
    """One worker's bounded listener, context cache, and UI-owned requests."""

    __slots__ = (
        "_active", "_assistant_pid", "_browser_pending", "_client", "_closed", "_context", "_finished", "_io_lock", "_last_poll", "_listener",
        "_lock", "_outbox", "_pending", "_registrations", "_started", "discovery", "folder", "owns", "timeout_s", "wake",
    )

    def __init__(
        self, folder: Path, *, epoch: str, wake: Callable[[], None], owns: Callable[[RunRef], bool], timeout_s: float = 2,
    ) -> None:
        if len(epoch) != 32 or any(char not in "0123456789abcdef" for char in epoch):
            raise ValueError("Worker epochs must be fresh UUID hex values.")
        if not 0 < timeout_s <= 30:
            raise ValueError("IPC deadlines must be between zero and thirty seconds.")
        self.folder, self.wake, self.owns, self.timeout_s = folder, wake, owns, timeout_s
        self.discovery = Discovery(epoch, os.getpid(), _address(folder, epoch), secrets.token_bytes(32))
        self._lock, self._io_lock = threading.Lock(), threading.Lock()
        self._closed, self._finished = threading.Event(), threading.Event()
        self._active: Channel | None = None
        self._listener: Listener | None = None
        self._started = False
        self._client, self._assistant_pid, self._last_poll = "", 0, 0.0
        self._context = ContextSnapshot(0, "unavailable")
        self._registrations: dict[str, ContextSnapshot | None] = {}
        self._pending: dict[str, PendingMeeting] = {}
        self._browser_pending: dict[str, PendingBrowserApp] = {}
        self._outbox = Outbox()

    def start(self) -> None:
        if self._started or self._closed.is_set():
            raise RuntimeError("A work bridge can only be started once.")
        self._started = True
        listener = Listener(self.discovery.address, family="AF_PIPE" if sys.platform == "win32" else "AF_UNIX", authkey=None)
        self._listener = listener
        try:
            if sys.platform != "win32":
                os.chmod(self.discovery.address, 0o600)
            _publish_discovery(self.folder, self.discovery)
            threading.Thread(target=self._serve, name="work IPC", daemon=True).start()
        except BaseException:
            listener.close()
            self._remove_discovery()
            self._finished.set()
            raise

    def _serve(self) -> None:
        listener = self._listener
        assert listener is not None
        try:
            while not self._closed.is_set():
                try:
                    accepted: Channel = listener.accept()
                    channel: Channel = LocalChannel(accepted)
                except (OSError, ValueError):
                    if self._closed.is_set():
                        break
                    raise
                with self._io_lock:
                    self._active = channel
                    if self._closed.is_set():
                        _abort(channel)
                try:
                    with _deadline(channel, self.timeout_s):
                        _server_auth(channel, self.discovery.key)
                        request = ipc.decode_request(channel.recv_bytes(ipc.MAX_FRAME))
                        reply = self._handle(request)
                        channel.send_bytes(ipc.encode_reply(reply))
                except (OSError, EOFError, ValueError, RuntimeError):
                    # Neither malformed JSON nor challenge material belongs in a log.
                    pass
                finally:
                    with self._io_lock:
                        self._active = None
        finally:
            self._closed.set()
            with self._lock:
                self._discard_pending("The work connection stopped.")
            listener.close()
            self._remove_discovery()
            self._finished.set()

    def _remove_discovery(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            if read_discovery(self.folder).epoch == self.discovery.epoch:
                (self.folder / "worker.json").unlink(missing_ok=True)

    def _discard_pending(self, reason: str) -> None:
        for pending in self._pending.values():
            if not pending.result.done():
                pending.result.set_result(ipc.MeetingFailed(reason))
        self._pending.clear()
        for app_pending in self._browser_pending.values():
            if not app_pending.result.done():
                app_pending.result.set_result(AppFailed(reason))
        self._browser_pending.clear()

    def _publish(self, payload: ipc.BridgeEvent) -> None:
        self._outbox, lost = enqueue(self._outbox, self.discovery.epoch, payload)
        if lost:
            self._discard_pending("The live owner request was lost. Inspect its result before trying again.")

    def _handle(self, request: ipc.Request) -> ipc.Reply:
        if request.epoch != self.discovery.epoch:
            return ipc.Rejected("This message belongs to an earlier work process.")
        operation = request.operation
        if isinstance(operation, (ipc.MeetingResponse, ipc.BrowserAppResponse)) and not self.owns(operation.run):
            return ipc.Accepted(self.discovery.epoch, False)
        with self._lock:
            if self._closed.is_set():
                return ipc.Rejected("The work process is stopping.")
            if isinstance(operation, ipc.Hello):
                if request.client != self._client:
                    self._discard_pending("The assistant connection changed. Inspect the result before trying again.")
                    self._outbox = Outbox(self._outbox.sequence, self._outbox.sequence)
                    self._publish(ipc.Resync("The assistant connected; reload durable work and refresh any missing approval preview."))
                    self._client = request.client
                self._assistant_pid = operation.assistant_pid
                self._last_poll = time.monotonic()
                return ipc.Accepted(self.discovery.epoch, True)
            if request.client != self._client:
                return ipc.Rejected("This assistant connection was replaced.")
            match operation:
                case ipc.Poll(after, limit):
                    return self._poll(after, limit)
                case ipc.Register(task_id, context, assistant_pid):
                    if assistant_pid != self._assistant_pid:
                        return ipc.Rejected("The invocation came from a different assistant.")
                    self._registrations.pop(task_id, None)
                    if len(self._registrations) >= MAX_REGISTRATIONS:
                        self._registrations.pop(next(iter(self._registrations)))
                    self._registrations[task_id] = context
                case ipc.PublishContext(context, assistant_pid):
                    if assistant_pid != self._assistant_pid:
                        return ipc.Rejected("Context came from a different assistant.")
                    self._context = context
                case ipc.MeetingResponse(run, request_id, result):
                    pending = self._pending.get(request_id)
                    if pending is None or pending.run != run or pending.client != request.client or pending.result.done():
                        return ipc.Accepted(self.discovery.epoch, False)
                    pending.result.set_result(result)
                    return ipc.Accepted(self.discovery.epoch, True)
                case ipc.BrowserAppResponse(run, request_id, app_result):
                    app_pending = self._browser_pending.get(request_id)
                    if (app_pending is None or app_pending.run != run or app_pending.client != request.client or app_pending.result.done()
                            or app_pending.stop.is_set() or time.time() >= app_pending.expires_at):
                        return ipc.Accepted(self.discovery.epoch, False)
                    app_pending.result.set_result(app_result)
                    return ipc.Accepted(self.discovery.epoch, True)
                case ipc.Command.WAKE:
                    pass
                case _:
                    assert_never(operation)
        self.wake()
        return ipc.Accepted(self.discovery.epoch, True)

    def _poll(self, after: int, limit: int) -> ipc.Reply:
        state = self._outbox
        if after > state.sequence:
            return ipc.Rejected("The event cursor is ahead of this work process.")
        if after < state.acknowledged:
            self._discard_pending("The assistant lost its live cursor. Inspect the result before trying again.")
            state = Outbox(state.sequence, state.sequence)
            self._outbox, _ = enqueue(state, self.discovery.epoch, ipc.Resync("Live event history is unavailable; reload durable work."))
        else:
            self._outbox = replace(state, acknowledged=after, entries=tuple(item for item in state.entries if item.envelope.sequence > after))
        self._last_poll = time.monotonic()
        messages: list[ipc.Envelope] = []
        size = 256
        for item in self._outbox.entries[:limit]:
            if size + item.size + 1 > ipc.MAX_FRAME:
                break
            size += item.size + 1
            messages.append(item.envelope)
        return ipc.Batch(self.discovery.epoch, tuple(messages))

    def publish(self, payload: ipc.BridgeEvent) -> bool:
        """Nonblocking publication; False means no live preview can be offered."""
        with self._lock:
            if self._closed.is_set():
                return False
            if isinstance(payload, ipc.ReviewEvent) and (not self._client or time.monotonic() - self._last_poll > CLIENT_FRESH_SECONDS):
                return False
            self._publish(payload)
            return True

    def invocation(self, task_id: str) -> ContextSnapshot | None:
        with self._lock:
            return self._registrations.get(task_id)

    def context(self) -> ContextSnapshot:
        with self._lock:
            return self._context

    @property
    def assistant_pid(self) -> int:
        with self._lock:
            return self._assistant_pid

    def meeting(self, run: RunRef, operation: ipc.MeetingOperation, stop: threading.Event, *, timeout_s: float = 30) -> str:
        """Wait for this run's exact volatile response; never replay a lost effect."""
        if not 0 < timeout_s <= 120:
            raise ValueError("Invalid meeting response deadline.")
        if stop.is_set() or not self.owns(run):
            raise OSError("This work run no longer owns the meeting request.")
        identity = uuid.uuid4().hex
        result: Future[ipc.MeetingResult] = Future()
        with self._lock:
            if self._closed.is_set() or not self._client or time.monotonic() - self._last_poll > CLIENT_FRESH_SECONDS:
                raise OSError("The assistant is unavailable for meeting capture.")
            if self._pending:
                raise OSError("Another meeting request is already awaiting its owner.")
            self._publish(ipc.MeetingRequest(run, identity, operation))
            self._pending[identity] = PendingMeeting(run, self._client, result)
        deadline = time.monotonic() + timeout_s
        try:
            while not stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OSError("The meeting owner did not respond. Inspect meeting status before trying again.")
                try:
                    answer = result.result(timeout=min(remaining, .1))
                except TimeoutError:
                    continue
                match answer:
                    case ipc.MeetingSucceeded(message):
                        return message
                    case ipc.MeetingFailed(reason):
                        raise OSError(reason)
                    case _:
                        assert_never(answer)
            raise OSError("The work run stopped before its meeting request completed.")
        finally:
            with self._lock:
                self._pending.pop(identity, None)

    def browser_app(self, run: RunRef, operation: AppOperation, stop: StopSignal, *, timeout_s: float = 20) -> AppResult:
        """One exact UI-owned app request; cancellation never replays its effect."""
        if isinstance(timeout_s, bool) or not 0 < timeout_s <= 120:
            raise ValueError("Invalid browser app response deadline.")
        if stop.is_set() or not self.owns(run):
            return AppCancelled.REQUEST
        identity = uuid.uuid4().hex
        expires_at = time.time() + timeout_s
        result: Future[AppResult] = Future()
        with self._lock:
            if self._closed.is_set() or not self._client or time.monotonic() - self._last_poll > CLIENT_FRESH_SECONDS:
                return AppFailed("The assistant is unavailable for browser apps.")
            if self._browser_pending:
                return AppFailed("Another browser app request is already awaiting its owner.")
            if stop.is_set():
                return AppCancelled.REQUEST
            self._publish(ipc.BrowserAppRequest(run, identity, operation, expires_at))
            self._browser_pending[identity] = PendingBrowserApp(run, self._client, result, stop, expires_at)
        deadline = time.monotonic() + timeout_s
        completed = False
        try:
            while True:
                if stop.is_set() or not self.owns(run):
                    return AppCancelled.REQUEST
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return AppFailed("The browser app owner did not respond. Inspect the card before retrying; its action may have completed.")
                try:
                    answer = result.result(timeout=min(remaining, .05))
                except TimeoutError:
                    continue
                if stop.is_set() or not self.owns(run):
                    return AppCancelled.REQUEST
                if time.monotonic() >= deadline:
                    return AppFailed("The browser app response arrived after its deadline. Inspect the card before retrying.")
                completed = True
                return answer
        finally:
            with self._lock:
                pending = self._browser_pending.pop(identity, None)
                if pending is not None and not completed and not self._closed.is_set():
                    self._publish(ipc.BrowserAppCancelled(run, identity))

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            self._discard_pending("The work process is stopping.")
            self._registrations.clear()
            self._context = ContextSnapshot(0, "disabled")
            self._outbox = Outbox()
        if self._listener is None:
            return
        with self._io_lock:
            if self._active is not None:
                _abort(self._active)
        # Wake a native accept before closing its listener. This is required
        # for both AF_UNIX accept() and an overlapped Windows pipe accept.
        with contextlib.suppress(OSError, ValueError):
            _connect_raw(self.discovery.address, min(.2, self.timeout_s)).close()
        if not self._finished.wait(self.timeout_s + 1):
            raise RuntimeError("The work IPC listener did not release its native handles.")

    def wait(self, timeout: float | None = None) -> bool:
        """Observe listener exit without inspecting its thread or native handles."""
        return self._finished.wait(timeout)


class BridgeClient:
    """Assistant-owned client; serialize calls on its observer/command thread."""

    __slots__ = (
        "_active", "_client", "_closed", "_context", "_cursor", "_discovery", "_gate", "_io_lock", "_pid", "_registrations",
        "folder", "timeout_s",
    )

    def __init__(self, folder: Path, *, timeout_s: float = 2) -> None:
        if not 0 < timeout_s <= 30:
            raise ValueError("IPC deadlines must be between zero and thirty seconds.")
        self.folder, self.timeout_s = folder, timeout_s
        self._client, self._pid, self._cursor = uuid.uuid4().hex, os.getpid(), 0
        self._gate, self._io_lock, self._closed = threading.Lock(), threading.Lock(), threading.Event()
        self._active: Channel | None = None
        self._discovery: Discovery | None = None
        self._context: ipc.PublishContext | None = None
        self._registrations: dict[str, ipc.Register] = {}

    def _exchange(self, discovery: Discovery, operation: ipc.Operation) -> ipc.Reply:
        if self._closed.is_set():
            raise OSError("The work bridge is closed.")
        frame = ipc.encode_request(ipc.Request(discovery.epoch, self._client, operation))
        try:
            channel = _connect_raw(discovery.address, self.timeout_s)
            with self._io_lock:
                self._active = channel
                if self._closed.is_set():
                    _abort(channel)
            with _deadline(channel, self.timeout_s):
                _client_auth(channel, discovery.key)
                channel.send_bytes(frame)
                reply = ipc.decode_reply(channel.recv_bytes(ipc.MAX_FRAME))
            if isinstance(reply, ipc.Rejected):
                raise OSError(reply.reason)
            if reply.epoch != discovery.epoch:
                raise OSError("The work process changed during this exchange.")
            return reply
        except (EOFError, RuntimeError, ValueError) as error:
            self._discovery = None
            raise OSError("The local work exchange ended without a valid reply.") from error
        except OSError:
            self._discovery = None
            raise
        finally:
            with self._io_lock:
                self._active = None

    def _send(self, discovery: Discovery, operation: ipc.Operation) -> bool:
        reply = self._exchange(discovery, operation)
        if not isinstance(reply, ipc.Accepted):
            raise OSError("The work process returned an unexpected reply.")
        return reply.accepted

    def _connect(self) -> Discovery:
        if self._closed.is_set():
            raise OSError("The work bridge is closed.")
        discovery = read_discovery(self.folder)
        if self._discovery == discovery:
            return discovery
        self._discovery = None
        if not self._send(discovery, ipc.Hello(self._pid)):
            raise OSError("The work process did not accept this assistant.")
        self._cursor = 0
        for registration in self._registrations.values():
            self._send(discovery, registration)
        if self._context is not None:
            self._send(discovery, self._context)
        self._discovery = discovery
        return discovery

    def connect(self) -> str:
        with self._gate:
            return self._connect().epoch

    def poll(self, limit: int = 64) -> tuple[ipc.Envelope, ...]:
        integer(limit, "poll limit", minimum=1, maximum=ipc.MAX_EVENTS)
        with self._gate:
            discovery = self._connect()
            reply = self._exchange(discovery, ipc.Poll(self._cursor, limit))
            if not isinstance(reply, ipc.Batch):
                raise OSError("The work process returned an unexpected event reply.")
            if reply.events and reply.events[0].sequence <= self._cursor:
                raise OSError("The work process returned an old event sequence.")
            if reply.events:
                self._cursor = reply.events[-1].sequence
            return reply.events

    def register(self, task_id: str, context: ContextSnapshot | None, *, assistant_pid: int) -> None:
        registration = ipc.Register(task_id, context, assistant_pid)
        ipc.encode_request(ipc.Request("validation", self._client, registration))
        with self._gate:
            # Store before I/O so a request submitted during a worker outage can
            # recover its invocation context when this same client reconnects.
            self._pid = assistant_pid
            self._registrations.pop(task_id, None)
            if len(self._registrations) >= MAX_REGISTRATIONS:
                self._registrations.pop(next(iter(self._registrations)))
            self._registrations[task_id] = registration
            self._send(self._connect(), registration)

    def publish_context(self, context: ContextSnapshot, *, assistant_pid: int) -> None:
        update = ipc.PublishContext(context, assistant_pid)
        ipc.encode_request(ipc.Request("validation", self._client, update))
        with self._gate:
            self._pid, self._context = assistant_pid, update
            self._send(self._connect(), update)

    def wake(self) -> None:
        with self._gate:
            self._send(self._connect(), ipc.Command.WAKE)

    def respond(self, message: ipc.Envelope, result: ipc.MeetingResult) -> bool:
        if not isinstance(message.payload, ipc.MeetingRequest):
            raise ValueError("Only a meeting request can receive a meeting response.")
        with self._gate:
            discovery = self._connect()
            if discovery.epoch != message.epoch:
                return False
            request = message.payload
            return self._send(discovery, ipc.MeetingResponse(request.run, request.request_id, result))

    def respond_browser_app(self, message: ipc.Envelope, result: AppResult) -> bool:
        if not isinstance(message.payload, ipc.BrowserAppRequest):
            raise ValueError("Only a browser app request can receive an app response.")
        with self._gate:
            discovery = self._connect()
            if discovery.epoch != message.epoch:
                return False
            request = message.payload
            return self._send(discovery, ipc.BrowserAppResponse(request.run, request.request_id, result))

    def close(self) -> None:
        self._closed.set()
        with self._io_lock:
            if self._active is not None:
                _abort(self._active)
        with self._gate:
            self._registrations.clear()
            self._context = self._discovery = None
