"""Real local transports, bounded public events, and worker/run/request fencing."""

import contextlib
import hmac
import itertools
import os
import sys
import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from multiprocessing.connection import Client
from pathlib import Path
from unittest import TestCase

from assistant.activity import Activity, ContextSnapshot
from assistant.agent_session import Answer, Done, Progress, StreamState
from automation import ipc
from automation.bridge import BridgeClient, BridgeServer, Channel, LocalChannel, ProfileLease, read_discovery
from automation.execution import ApprovalReview
from automation.task import Approval, RunRef
from winapi.shapes import Window

RUN = RunRef("notes", "first-run")


@contextlib.contextmanager
def bridge(folder: Path, *, owns: Callable[[RunRef], bool] | None = None) -> Generator[tuple[BridgeServer, BridgeClient]]:
    with ProfileLease(folder):
        server = BridgeServer(folder, epoch="a" * 32, wake=lambda: None, owns=owns or (lambda run: run == RUN), timeout_s=.5)
        client = BridgeClient(folder, timeout_s=1)
        server.start()
        try:
            client.connect()
            client.poll()
            yield server, client
        finally:
            client.close()
            server.close()


def read_until(client: BridgeClient, accept: Callable[[ipc.Envelope], bool]) -> ipc.Envelope:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        for message in client.poll():
            if accept(message):
                return message
    raise AssertionError("The expected worker event did not arrive.")


def drain(client: BridgeClient) -> tuple[ipc.Envelope, ...]:
    received: list[ipc.Envelope] = []
    for _ in range(ipc.MAX_EVENTS + 1):
        batch = client.poll()
        if not batch:
            return tuple(received)
        assert len(ipc.encode_reply(ipc.Batch(batch[0].epoch, batch))) <= ipc.MAX_FRAME
        received.extend(batch)
    raise AssertionError("The bounded outbox did not drain.")


def raw_peer(server: BridgeServer) -> LocalChannel:
    connection: Channel = Client(server.discovery.address, family="AF_PIPE" if sys.platform == "win32" else "AF_UNIX", authkey=None)
    return LocalChannel(connection)


def authenticate(peer: Channel, key: bytes) -> None:
    """Implement the documented challenge independently of the client adapter."""
    challenge = peer.recv_bytes(128)
    assert len(challenge) == 41 and challenge[:9] == b"Toolkit1:"
    nonce = b"test-client-nonce".ljust(32, b".")
    peer.send_bytes(nonce + hmac.digest(key, b"client:" + challenge[9:] + nonce, "sha256"))
    assert hmac.compare_digest(peer.recv_bytes(128), hmac.digest(key, b"server:" + challenge[9:] + nonce, "sha256"))


def disconnected(read: Future[bytes]) -> None:
    try:
        read.result(timeout=3)
    except TimeoutError as error:
        raise AssertionError("The peer did not disconnect before the test deadline.") from error
    except (EOFError, OSError, ValueError, RuntimeError):
        pass
    else:
        raise AssertionError("The peer returned data after a rejected exchange.")


def test_unavailable_worker_retains_invocation_and_replays_after_restart(tmp_path: Path) -> None:
    context = ContextSnapshot(12, "active", (Activity(Window(41, "Private project context", "editor.exe", False), 10, 12, False),))
    client = BridgeClient(tmp_path, timeout_s=.5)
    with TestCase().assertRaises(OSError):
        client.register("notes", context, assistant_pid=os.getpid())
    with TestCase().assertRaises(OSError):
        client.publish_context(context, assistant_pid=os.getpid())
    try:
        with ProfileLease(tmp_path):
            for epoch in ("a" * 32, "b" * 32):
                server = BridgeServer(tmp_path, epoch=epoch, wake=lambda: None, owns=lambda run: True)
                server.start()
                try:
                    assert client.connect() == epoch
                    assert server.invocation("notes") == context
                    assert server.context() == context
                    assert server.assistant_pid == os.getpid()
                    assert any(isinstance(item.payload, ipc.Resync) for item in client.poll())
                    assert read_discovery(tmp_path).epoch == epoch
                    assert b"Private project context" not in (tmp_path / "worker.json").read_bytes()
                finally:
                    server.close()
                assert not (tmp_path / "worker.json").exists()
    finally:
        client.close()


def test_streaming_updates_coalesce_and_terminal_results_survive(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client):
        for index in range(200):
            server.publish(ipc.LogEvent(RUN, Progress(f"Read {index + 1} pages")))
            server.publish(ipc.LogEvent(RUN, Answer("progress", f"Draft {index + 1}", 20)))
        server.publish(ipc.LogEvent(RUN, Answer("progress", "Complete notes", 20, StreamState.COMPLETE)))
        server.publish(ipc.LogEvent(RUN, Done("Saved and checked the notes.", 21)))
        events = drain(client)
        assert [item.payload for item in events] == [
            ipc.LogEvent(RUN, Progress("Read 200 pages")),
            ipc.LogEvent(RUN, Answer("progress", "Complete notes", 20, StreamState.COMPLETE)),
            ipc.LogEvent(RUN, Done("Saved and checked the notes.", 21)),
        ]
        assert all(left.sequence < right.sequence for left, right in itertools.pairwise(events))
        server.publish(ipc.LogEvent(RUN, Progress("A later observation")))
        later = client.poll()
        assert len(later) == 1 and later[0].sequence > events[-1].sequence


def test_overflow_reports_loss_before_new_results_and_stays_within_frame_budget(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client):
        for index in range(200):
            server.publish(ipc.LogEvent(RUN, Done(f"Result {index}", index)))
        events = drain(client)
        assert isinstance(events[0].payload, ipc.Resync)
        assert events[-1].payload == ipc.LogEvent(RUN, Done("Result 199", 199))
        assert len(events) <= ipc.MAX_EVENTS
        # Hit the byte bound independently of the event-count bound.
        for index in range(50):
            server.publish(ipc.LogEvent(RUN, Done(f"{index}:" + "x" * 127_990, index)))
        events = drain(client)
        assert isinstance(events[0].payload, ipc.Resync)
        assert isinstance(events[-1].payload, ipc.LogEvent)
        assert events[-1].payload.event == Done("49:" + "x" * 127_990, 49)


def test_review_requires_a_connected_assistant_and_remains_volatile(tmp_path: Path) -> None:
    fingerprint = "a" * 64
    review = ApprovalReview("notes", 3, Approval(f"approval:3:{fingerprint}:nonce", "Save notes", "Redacted operation"),
                            fingerprint, "Save notes", "Full private preview")
    with ProfileLease(tmp_path):
        server = BridgeServer(tmp_path, epoch="a" * 32, wake=lambda: None, owns=lambda run: True)
        server.start()
        client = BridgeClient(tmp_path)
        try:
            assert not server.publish(ipc.ReviewEvent(RUN, review))
            client.connect()
            client.poll()
            assert server.publish(ipc.ReviewEvent(RUN, review))
            assert [item.payload for item in client.poll()] == [ipc.ReviewEvent(RUN, review)]
            assert b"Full private preview" not in (tmp_path / "worker.json").read_bytes()
        finally:
            client.close()
            server.close()
    assert all(b"Full private preview" not in path.read_bytes() for path in tmp_path.iterdir() if path.is_file())


def test_meeting_response_must_match_worker_run_and_request(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        stop = threading.Event()
        result = pool.submit(server.meeting, RUN, ipc.StartMeeting("microphone", "Planning"), stop)
        request = read_until(client, lambda item: isinstance(item.payload, ipc.MeetingRequest))
        assert isinstance(request.payload, ipc.MeetingRequest)
        assert not client.respond(replace(request, epoch="b" * 32), ipc.MeetingSucceeded("stale worker"))
        assert not client.respond(replace(request, payload=replace(request.payload, run=RunRef("notes", "old-run"))),
                                  ipc.MeetingSucceeded("stale run"))
        assert not client.respond(replace(request, payload=replace(request.payload, request_id="different-request")),
                                  ipc.MeetingSucceeded("stale request"))
        assert client.respond(request, ipc.MeetingSucceeded("Recording Planning from the microphone."))
        assert result.result(timeout=3) == "Recording Planning from the microphone."
        assert not client.respond(request, ipc.MeetingSucceeded("duplicate"))


def test_cancel_and_assistant_replacement_release_meeting_waits(tmp_path: Path) -> None:
    ownership = threading.Event()
    ownership.set()
    with bridge(tmp_path, owns=lambda run: run == RUN and ownership.is_set()) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        stop = threading.Event()
        result = pool.submit(server.meeting, RUN, ipc.MeetingCommand.STATUS, stop)
        request = read_until(client, lambda item: isinstance(item.payload, ipc.MeetingRequest))
        ownership.clear()
        assert not client.respond(request, ipc.MeetingSucceeded("outdated"))
        stop.set()
        with TestCase().assertRaisesRegex(OSError, "stopped"):
            result.result(timeout=3)
        ownership.set()
        result = pool.submit(server.meeting, RUN, ipc.MeetingCommand.STATUS, threading.Event())
        read_until(client, lambda item: isinstance(item.payload, ipc.MeetingRequest))
        replacement = BridgeClient(tmp_path)
        try:
            replacement.connect()
            with TestCase().assertRaisesRegex(OSError, "connection changed"):
                result.result(timeout=3)
        finally:
            replacement.close()


def test_shutdown_releases_stalled_authentication_and_pending_meeting(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(server.meeting, RUN, ipc.MeetingCommand.STATUS, threading.Event())
        read_until(client, lambda item: isinstance(item.payload, ipc.MeetingRequest))
        peer = raw_peer(server)
        try:
            assert peer.recv_bytes(128).startswith(b"Toolkit1:")  # accept is now blocked on authentication
            server.close()
            assert server.wait(0)
            with TestCase().assertRaisesRegex(OSError, "stopping"):
                result.result(timeout=3)
        finally:
            peer.close()


def test_wrong_authentication_is_rejected_before_commands_are_read(tmp_path: Path) -> None:
    wake = threading.Event()
    with ProfileLease(tmp_path), ThreadPoolExecutor(max_workers=1) as pool:
        server = BridgeServer(tmp_path, epoch="a" * 32, wake=wake.set, owns=lambda run: True, timeout_s=.5)
        server.start()
        peer = raw_peer(server)
        client = BridgeClient(tmp_path)
        try:
            challenge = peer.recv_bytes(128)
            nonce = b"incorrect-key-peer".ljust(32, b".")
            peer.send_bytes(nonce + hmac.digest(b"a different key", b"client:" + challenge[9:] + nonce, "sha256"))
            disconnected(pool.submit(peer.recv_bytes, 128))
            assert not wake.is_set()
            client.wake()
            assert wake.is_set(), "Authentication failure must not poison a later authenticated connection."
        finally:
            peer.close()
            client.close()
            server.close()


def test_malformed_and_partial_frames_do_not_hold_the_listener(tmp_path: Path) -> None:
    with bridge(tmp_path) as (server, client), ThreadPoolExecutor(max_workers=1) as pool:
        for frame in (b"{", b"x" * (ipc.MAX_FRAME + 1)):
            peer = raw_peer(server)
            try:
                authenticate(peer, server.discovery.key)
                # The peer can be disconnected while still writing an oversized message.
                with contextlib.suppress(OSError, ValueError):
                    peer.send_bytes(frame)
                disconnected(pool.submit(peer.recv_bytes, 128))
            finally:
                peer.close()
            client.wake()  # A rejected peer must not poison later valid exchanges.
        peer = raw_peer(server)
        try:
            authenticate(peer, server.discovery.key)
            if sys.platform != "win32":
                os.write(peer.fileno(), b"\0\0\0\x20{")  # header promises 32 bytes; body never completes
            disconnected(pool.submit(peer.recv_bytes, 128))
        finally:
            peer.close()
        client.wake()


def test_codec_rejects_unknown_versions_invalid_scalars_and_stale_batches() -> None:
    request = b'{"version":1,"epoch":"worker","client":"assistant","operation":{"kind":"poll","after":0,"limit":64}}'
    assert ipc.decode_request(request).operation == ipc.Poll(0, 64)
    bounded = request.ljust(ipc.MAX_FRAME, b" ")
    assert ipc.decode_request(bounded).operation == ipc.Poll(0, 64)
    for broken in (
        request.replace(b'"version":1', b'"version":2'),
        request.replace(b'"after":0', b'"after":true'),
        request.replace(b'"limit":64', b'"limit":129'),
        request.replace(b'"poll"', b'"execute-private-sdk-object"'),
        bounded + b" ",  # Valid JSON and valid fields; only the byte bound is exceeded.
    ):
        with TestCase().assertRaises(ValueError):
            ipc.decode_request(broken)
    batch = ipc.encode_reply(ipc.Batch("worker", (ipc.Envelope("worker", 1, ipc.LogEvent(RUN, Done("Observed", 20))),)))
    for broken in (
        batch.replace(b'"at":20', b'"at":NaN'),
        batch.replace(b'"at":20', b'"at":-1'),
        batch.replace(b'"sequence":1', b'"sequence":true'),
        batch.replace(b'"sequence":1', b'"sequence":0'),
        batch.replace(b'"epoch":"worker"', b'"epoch":"old-worker"', 1),
    ):
        with TestCase().assertRaises(ValueError):
            ipc.decode_reply(broken)
    ordered = ipc.Batch("worker", (
        ipc.Envelope("worker", 2, ipc.LogEvent(RUN, Progress("Reading the transcript."))),
        ipc.Envelope("worker", 3, ipc.LogEvent(RUN, Done("Notes saved and checked.", 21))),
    ))
    paired = ipc.encode_reply(ordered)
    assert ipc.decode_reply(paired) == ordered
    for replacement in (b'"sequence":2', b'"sequence":1'):
        with TestCase().assertRaises(ValueError):
            ipc.decode_reply(paired.replace(b'"sequence":3', replacement))


def test_profile_ownership_is_exclusive_and_released_for_restart(tmp_path: Path) -> None:
    with ProfileLease(tmp_path), TestCase().assertRaises(OSError), ProfileLease(tmp_path):
        raise AssertionError("A second worker must not recover the active worker's tasks.")
    with ProfileLease(tmp_path):
        assert (tmp_path / "worker.lock").exists()
