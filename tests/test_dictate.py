"""Dictation tests: settings, the text that gets typed, audio, and the request to a
transcription endpoint (a local fake server). Run anywhere with `pytest`."""

from __future__ import annotations

import io
import threading
import wave
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dictate.core import RATE, Failure, Missing, Settings, long_enough, settings_from, transcribe, typed_text, utf16_units, wav

# ---- settings --------------------------------------------------------------------------


def test_settings_need_the_endpoint_and_key() -> None:
    assert settings_from({}) == Missing(("OPENAI_BASE_URL", "OPENAI_API_KEY"))
    assert settings_from({"OPENAI_BASE_URL": "https://ai.example/v1"}) == Missing(("OPENAI_API_KEY",))


def test_settings_defaults() -> None:
    settings = settings_from({"OPENAI_BASE_URL": "https://ai.example/v1/", "OPENAI_API_KEY": "k"})
    assert settings == Settings("https://ai.example/v1", "k", "gpt-4o-transcribe", "", "")


def test_settings_overrides() -> None:
    env = {
        "OPENAI_BASE_URL": "https://ai.example/v1",
        "OPENAI_API_KEY": "k",
        "DICTATE_MODEL": "whisper-1",
        "DICTATE_PROMPT": "Miryoku, Colemak",
        "DICTATE_LANGUAGE": "sv",
    }
    assert settings_from(env) == Settings("https://ai.example/v1", "k", "whisper-1", "Miryoku, Colemak", "sv")


# ---- what gets typed -------------------------------------------------------------------


def test_typed_text_never_contains_a_newline() -> None:
    # A newline would press Enter, and in a terminal run whatever was dictated.
    assert typed_text("git status\nrm -rf build\r\n") == "git status rm -rf build "


def test_typed_text_collapses_whitespace_and_drops_control_characters() -> None:
    assert typed_text("  hej\t\tdå \x1b[2J ") == "hej då [2J "


def test_typed_text_of_nothing_is_nothing() -> None:
    assert typed_text(" \n ") == ""


def test_utf16_units() -> None:
    assert utf16_units("aå") == [0x61, 0xE5]
    assert utf16_units("😀") == [0xD83D, 0xDE00]  # outside the BMP: a surrogate pair


# ---- audio -----------------------------------------------------------------------------


def test_wav_wraps_pcm() -> None:
    pcm = bytes(range(256)) * 4
    with wave.open(io.BytesIO(wav(pcm, RATE)), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        assert w.readframes(w.getnframes()) == pcm


def test_long_enough_skips_taps() -> None:
    assert not long_enough(bytes(2 * RATE // 10))  # 0.1 s: an accidental tap
    assert long_enough(bytes(2 * RATE // 2))  # 0.5 s


# ---- the transcription request ---------------------------------------------------------


@dataclass(slots=True)
class Received:
    path: str = ""
    authorization: str = ""
    content_type: str = ""
    body: bytes = b""


@dataclass(slots=True)
class Reply:
    status: int = 200
    body: bytes = b'{"text": "hello there"}'


@dataclass(slots=True)
class FakeEndpoint:
    url: str
    received: Received = field(default_factory=Received)
    reply: Reply = field(default_factory=Reply)


@contextmanager
def fake_endpoint() -> Generator[FakeEndpoint, None, None]:
    received, reply = Received(), Reply()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.path = self.path
            received.authorization = self.headers.get("Authorization", "")
            received.content_type = self.headers.get("Content-Type", "")
            received.body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(reply.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(reply.body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield FakeEndpoint(f"http://127.0.0.1:{server.server_port}/v1", received, reply)
    finally:
        server.shutdown()
        server.server_close()


def settings(url: str, prompt: str = "", language: str = "") -> Settings:
    return Settings(url, "secret", "gpt-4o-transcribe", prompt, language)


def test_transcribe_posts_the_audio_and_returns_the_text() -> None:
    with fake_endpoint() as endpoint:
        audio = wav(bytes(2 * RATE), RATE)
        assert transcribe(settings(endpoint.url), audio) == "hello there"
        r = endpoint.received
        assert r.path == "/v1/audio/transcriptions"
        assert r.authorization == "Bearer secret"
        assert r.content_type.startswith("multipart/form-data; boundary=")
        assert b'name="model"\r\n\r\ngpt-4o-transcribe\r\n' in r.body
        assert b'name="response_format"\r\n\r\njson\r\n' in r.body
        assert b'filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n' + audio + b"\r\n" in r.body
        assert b'name="prompt"' not in r.body
        assert b'name="language"' not in r.body


def test_transcribe_sends_prompt_and_language_when_set() -> None:
    with fake_endpoint() as endpoint:
        transcribe(settings(endpoint.url, prompt="Miryoku, Colemak", language="sv"), wav(b"", RATE))
        assert b'name="prompt"\r\n\r\nMiryoku, Colemak\r\n' in endpoint.received.body
        assert b'name="language"\r\n\r\nsv\r\n' in endpoint.received.body


def test_transcribe_reports_the_servers_error() -> None:
    with fake_endpoint() as endpoint:
        endpoint.reply.status = 401
        endpoint.reply.body = b'{"error": {"message": "Invalid session key", "type": "auth"}}'
        assert transcribe(settings(endpoint.url), wav(b"", RATE)) == Failure("HTTP 401: Invalid session key")


def test_transcribe_reports_a_reply_without_text() -> None:
    with fake_endpoint() as endpoint:
        endpoint.reply.body = b"<html>proxy login</html>"
        assert transcribe(settings(endpoint.url), wav(b"", RATE)) == Failure("unexpected reply: <html>proxy login</html>")


def test_transcribe_reports_an_unreachable_endpoint() -> None:
    result = transcribe(settings("http://127.0.0.1:9/v1"), wav(b"", RATE), timeout_s=2)
    assert isinstance(result, Failure)
    assert result.message.startswith("can't reach 127.0.0.1:9")
