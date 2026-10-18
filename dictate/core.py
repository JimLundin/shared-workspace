"""Everything dictation does that isn't Windows: settings, the audio file, the
request to an OpenAI-style transcription endpoint, and the text that gets typed.
Uses only the standard library, so there is nothing to install."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import uuid
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Final, cast

RATE: Final = 16_000
"""Samples per second. Speech models work at 16 kHz; more only makes uploads bigger."""
MAX_SECONDS: Final = 300
"""Longest recording; the buffer for it (9.6 MB) is allocated once, at start."""
MIN_SECONDS: Final = 0.3
"""Shorter recordings are accidental taps, and aren't sent."""


# ---- Settings --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    """The endpoint's base, as OpenAI's own libraries take it: `https://.../v1`."""
    api_key: str
    model: str
    prompt: str
    """Names and terms to expect; helps with jargon. Empty: none."""
    language: str
    """An ISO code such as `sv` or `en`. Empty: detected from the speech."""


@dataclass(frozen=True, slots=True)
class Missing:
    """Required environment variables that aren't set."""

    names: tuple[str, ...]


def settings_from(env: Mapping[str, str]) -> Settings | Missing:
    """Read from environment variables, named as OpenAI's own tools name them."""
    required = ("OPENAI_BASE_URL", "OPENAI_API_KEY")
    if missing := tuple(name for name in required if not env.get(name)):
        return Missing(missing)
    return Settings(
        base_url=env["OPENAI_BASE_URL"].rstrip("/"),
        api_key=env["OPENAI_API_KEY"],
        model=env.get("DICTATE_MODEL", "gpt-4o-transcribe"),
        prompt=env.get("DICTATE_PROMPT", ""),
        language=env.get("DICTATE_LANGUAGE", ""),
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


# ---- Transcription ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Failure:
    message: str


def _multipart(fields: Mapping[str, str], audio: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body with text fields and the audio file; and its content type."""
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts.append(audio + f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _field(data: object, name: str) -> object:
    """`data[name]` if data is a JSON object, else None."""
    return cast("dict[object, object]", data).get(name) if isinstance(data, dict) else None


def _json(body: bytes) -> object:
    try:
        data: object = json.loads(body)
    except ValueError:
        return None
    return data


def transcribe(settings: Settings, audio: bytes, *, timeout_s: float = 60) -> str | Failure:
    """The text spoken in a WAV file. Goes through the system's proxy settings, and
    trusts the certificates Windows trusts (so company TLS inspection works)."""
    fields = {"model": settings.model, "response_format": "json"}
    if settings.prompt:
        fields["prompt"] = settings.prompt
    if settings.language:
        fields["language"] = settings.language
    body, content_type = _multipart(fields, audio)
    request = urllib.request.Request(
        f"{settings.base_url}/audio/transcriptions",
        data=body,
        headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": content_type},
        method="POST",
    )
    try:
        response: HTTPResponse = urllib.request.urlopen(request, timeout=timeout_s)
        with response:
            reply = response.read()
    except urllib.error.HTTPError as error:
        detail = _field(_field(_json(error.read()), "error"), "message")
        return Failure(f"HTTP {error.code}: {detail if isinstance(detail, str) else error.reason}")
    except urllib.error.URLError as error:
        return Failure(f"can't reach {request.host}: {error.reason}")
    except OSError as error:  # timeouts and dropped connections
        return Failure(f"can't reach {request.host}: {error}")
    text = _field(_json(reply), "text")
    return text if isinstance(text, str) else Failure(f"unexpected reply: {reply[:200].decode(errors='replace')}")


# ---- Typing ----------------------------------------------------------------------------


def typed_text(transcript: str) -> str:
    """What to type for a transcript: one line, with a space after it so the next
    dictation doesn't run into this one. Newlines become spaces (a newline would
    press Enter, and in a terminal run the command); control characters go."""
    line = " ".join("".join(c for c in transcript if c.isprintable() or c.isspace()).split())
    return f"{line} " if line else ""


def utf16_units(text: str) -> list[int]:
    """Windows types text as UTF-16 code units; outside the BMP that's two per character."""
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]
