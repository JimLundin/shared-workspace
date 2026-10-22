"""A local stand-in for an OpenAI-style endpoint: it records each request and
answers with the replies it's given, in order (the last one repeats)."""

import threading
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True, slots=True)
class Received:
    path: str
    authorization: str
    content_type: str
    body: bytes


@dataclass(frozen=True, slots=True)
class Reply:
    body: bytes = b'{"text": "hello there"}'
    status: int = 200
    content_type: str = "application/json"
    content_length: int | None = None


def sse(*events: str) -> Reply:
    """A streamed reply: each event is a `data:` line, then `[DONE]`."""
    lines = [f"data: {event}\n\n" for event in (*events, "[DONE]")]
    return Reply("".join(lines).encode(), content_type="text/event-stream")


@dataclass(slots=True)
class FakeEndpoint:
    url: str
    received: list[Received] = field(default_factory=list[Received])
    replies: list[Reply] = field(default_factory=lambda: [Reply()])
    respond: Callable[[Received], Reply] | None = None
    stream: Callable[[Received], Iterator[bytes]] | None = None

    @property
    def last(self) -> Received:
        return self.received[-1]


@contextmanager
def fake_endpoint() -> Generator[FakeEndpoint]:
    endpoint = FakeEndpoint("")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            request = Received(self.path, self.headers.get("Authorization", ""), self.headers.get("Content-Type", ""), self.rfile.read(length))
            endpoint.received.append(request)
            if endpoint.stream is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for chunk in endpoint.stream(request):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Stopping a streamed request closes its socket.
                return
            if endpoint.respond is not None:
                reply = endpoint.respond(request)
            else:
                reply = endpoint.replies.pop(0) if len(endpoint.replies) > 1 else endpoint.replies[0]
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Content-Length", str(len(reply.body) if reply.content_length is None else reply.content_length))
            self.end_headers()
            self.wfile.write(reply.body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint.url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()
