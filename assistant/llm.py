"""The AI endpoint: settings, transcription, and chat (plain, streamed, with tools
and images), against any OpenAI-style API. Uses only the standard library, so
there is nothing to install. Every call returns its result or a `Failure`; none
raise for network or server problems."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
import uuid
import wave
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Final, Literal, TypeAlias, cast

RATE: Final = 16_000
"""Samples per second. Speech models work at 16 kHz; more only makes uploads bigger."""
MAX_SECONDS: Final = 300
"""Longest recording; the buffer for it (9.6 MB) is allocated once, at start."""
MIN_SECONDS: Final = 0.3
"""Shorter recordings are accidental taps, and aren't sent."""

Json: TypeAlias = "str | int | float | bool | list[Json] | dict[str, Json] | None"


# ---- Settings --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    """The endpoint's base, as OpenAI's own libraries take it: `https://.../v1`."""
    api_key: str
    transcribe_model: str
    chat_model: str
    """For quick jobs: tidying dictation, rewrites, questions, screenshots."""
    agent_model: str
    vocabulary: str
    """Names and terms to expect when transcribing; helps with jargon. Empty: none."""
    language: str
    """An ISO code such as `sv` or `en` for transcription. Empty: detected."""


@dataclass(frozen=True, slots=True)
class Missing:
    """Required environment variables that aren't set."""

    names: tuple[str, ...]


def settings_from(env: Mapping[str, str]) -> Settings | Missing:
    """Read from environment variables. The endpoint's are named as OpenAI's own tools name them."""
    required = ("OPENAI_BASE_URL", "OPENAI_API_KEY")
    if missing := tuple(name for name in required if not env.get(name)):
        return Missing(missing)
    chat_model = env.get("TOOLKIT_CHAT_MODEL", "gpt-5.4-mini")
    return Settings(
        base_url=env["OPENAI_BASE_URL"].rstrip("/"),
        api_key=env["OPENAI_API_KEY"],
        transcribe_model=env.get("TOOLKIT_TRANSCRIBE_MODEL", "gpt-4o-transcribe"),
        chat_model=chat_model,
        agent_model=env.get("TOOLKIT_AGENT_MODEL", chat_model),
        vocabulary=env.get("TOOLKIT_VOCABULARY", ""),
        language=env.get("TOOLKIT_LANGUAGE", ""),
    )


# ---- Audio -----------------------------------------------------------------------------


def long_enough(pcm: bytes) -> bool:
    """16-bit mono at `RATE`: two bytes per sample."""
    return len(pcm) >= 2 * RATE * MIN_SECONDS


def wav(pcm: bytes, rate: int) -> bytes:
    """16-bit mono PCM as a WAV file."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


# ---- Messages --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Text:
    text: str


@dataclass(frozen=True, slots=True)
class Image:
    png: bytes


Part: TypeAlias = Text | Image


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    """JSON, as the model wrote it (it may not be valid)."""


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | tuple[Part, ...]
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""


def system_message(text: str) -> Message:
    return Message("system", text)


def user_message(content: str | Sequence[Part]) -> Message:
    return Message("user", content if isinstance(content, str) else tuple(content))


def assistant_message(text: str, tool_calls: tuple[ToolCall, ...] = ()) -> Message:
    return Message("assistant", text, tool_calls)


def tool_result(call_id: str, text: str) -> Message:
    return Message("tool", text, tool_call_id=call_id)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A function the model may call."""

    name: str
    description: str
    parameters: dict[str, Json]
    """A JSON schema for the arguments."""


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True, slots=True)
class Failure:
    message: str


def _part(part: Part) -> Json:
    match part:
        case Text(text):
            return {"type": "text", "text": text}
        case Image(png):
            return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png).decode()}"}}


def _message(m: Message) -> Json:
    content: Json = m.content if isinstance(m.content, str) else [_part(p) for p in m.content]
    out: dict[str, Json] = {"role": m.role}
    if m.role == "tool":
        out["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        out["content"] = content or None
        out["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls]
    else:
        out["content"] = content
    return out


def _tool(spec: ToolSpec) -> Json:
    return {"type": "function", "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters}}


# ---- Reading JSON replies --------------------------------------------------------------


def _json(body: bytes) -> object:
    try:
        data: object = json.loads(body)
    except ValueError:
        return None
    return data


def _get(data: object, *path: str | int) -> object:
    """`data[a][b]...`, or None where the path doesn't exist."""
    for step in path:
        if isinstance(step, str) and isinstance(data, dict):
            data = cast("dict[object, object]", data).get(step)
        elif isinstance(step, int) and isinstance(data, list) and -len(data) <= step < len(data):  # pyright: ignore[reportUnknownArgumentType]
            data = cast("list[object]", data)[step]
        else:
            return None
    return data


def _str(data: object, *path: str | int) -> str:
    value = _get(data, *path)
    return value if isinstance(value, str) else ""


def _items(data: object) -> list[object]:
    return cast("list[object]", data) if isinstance(data, list) else []


def _unexpected(body: bytes) -> Failure:
    return Failure(f"unexpected reply: {body[:200].decode(errors='replace')}")


# ---- Requests --------------------------------------------------------------------------


def _request(settings: Settings, path: str, body: bytes, content_type: str) -> urllib.request.Request:
    return urllib.request.Request(
        f"{settings.base_url}{path}",
        data=body,
        headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": content_type},
        method="POST",
    )


def _open(request: urllib.request.Request, timeout_s: float) -> HTTPResponse | Failure:
    """Goes through the system's proxy settings, and trusts the certificates the
    system trusts (so company TLS inspection works)."""
    try:
        response: HTTPResponse = urllib.request.urlopen(request, timeout=timeout_s)
    except urllib.error.HTTPError as error:
        detail = _get(_json(error.read()), "error", "message")
        return Failure(f"HTTP {error.code}: {detail if isinstance(detail, str) else error.reason}")
    except urllib.error.URLError as error:
        return Failure(f"can't reach {request.host}: {error.reason}")
    except OSError as error:  # timeouts and dropped connections
        return Failure(f"can't reach {request.host}: {error}")
    return response


def _post(request: urllib.request.Request, timeout_s: float) -> bytes | Failure:
    response = _open(request, timeout_s)
    if isinstance(response, Failure):
        return response
    try:
        with response:
            return response.read()
    except OSError as error:
        return Failure(f"connection dropped: {error}")


def _multipart(fields: Mapping[str, str], audio: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body with text fields and the audio file; and its content type."""
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts.append(audio + f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(settings: Settings, audio: bytes, *, timeout_s: float = 60) -> str | Failure:
    """The text spoken in a WAV file."""
    fields = {"model": settings.transcribe_model, "response_format": "json"}
    if settings.vocabulary:
        fields["prompt"] = settings.vocabulary
    if settings.language:
        fields["language"] = settings.language
    body, content_type = _multipart(fields, audio)
    reply = _post(_request(settings, "/audio/transcriptions", body, content_type), timeout_s)
    if isinstance(reply, Failure):
        return reply
    text = _get(_json(reply), "text")
    return text if isinstance(text, str) else _unexpected(reply)


def _chat_body(settings: Settings, messages: Sequence[Message], model: str | None, tools: Sequence[ToolSpec], streaming: bool) -> bytes:
    body: dict[str, Json] = {"model": model or settings.chat_model, "messages": [_message(m) for m in messages]}
    if tools:
        body["tools"] = [_tool(t) for t in tools]
    if streaming:
        body["stream"] = True
    return json.dumps(body).encode()


def chat(
    settings: Settings,
    messages: Sequence[Message],
    *,
    model: str | None = None,
    tools: Sequence[ToolSpec] = (),
    timeout_s: float = 120,
) -> Reply | Failure:
    """The model's answer: text, or calls to the tools it was offered."""
    body = _chat_body(settings, messages, model, tools, streaming=False)
    reply = _post(_request(settings, "/chat/completions", body, "application/json"), timeout_s)
    if isinstance(reply, Failure):
        return reply
    message = _get(_json(reply), "choices", 0, "message")
    if _get(message, "role") is None and _get(message, "content") is None:
        return _unexpected(reply)
    calls = tuple(ToolCall(_str(c, "id"), _str(c, "function", "name"), _str(c, "function", "arguments")) for c in _items(_get(message, "tool_calls")))
    return Reply(_str(message, "content"), calls)


def _events(response: HTTPResponse) -> Iterator[object]:
    """The JSON payloads of a server-sent event stream, up to `[DONE]`."""
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            return
        yield _json(data.encode())


def stream(
    settings: Settings,
    messages: Sequence[Message],
    on_text: Callable[[str], object],
    *,
    model: str | None = None,
    timeout_s: float = 120,
) -> str | Failure:
    """The model's answer, passed to `on_text` piece by piece as it's written; all of it at the end."""
    body = _chat_body(settings, messages, model, (), streaming=True)
    response = _open(_request(settings, "/chat/completions", body, "application/json"), timeout_s)
    if isinstance(response, Failure):
        return response
    pieces: list[str] = []
    try:
        with response:
            for event in _events(response):
                if piece := _str(event, "choices", 0, "delta", "content"):
                    pieces.append(piece)
                    on_text(piece)
    except OSError as error:
        return Failure(f"connection dropped: {error}")
    return "".join(pieces)


def ask(settings: Settings, instructions: str, content: str | Sequence[Part], *, model: str | None = None) -> str | Failure:
    """One question, one answer."""
    reply = chat(settings, [system_message(instructions), user_message(content)], model=model)
    return reply if isinstance(reply, Failure) else reply.text
