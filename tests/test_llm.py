"""The AI endpoint client: settings, transcription, chat (plain, streamed, with
tools and images). Against a local fake endpoint; runs anywhere."""

from __future__ import annotations

import json

from assistant.llm import (
    RATE,
    Failure,
    Image,
    Missing,
    Reply,
    Settings,
    Text,
    ToolCall,
    ToolSpec,
    assistant_message,
    chat,
    settings_from,
    stream,
    system_message,
    tool_result,
    transcribe,
    user_message,
    wav,
)

from .fake_endpoint import Reply as Canned
from .fake_endpoint import fake_endpoint, sse

ENV = {"OPENAI_BASE_URL": "https://ai.example/v1/", "OPENAI_API_KEY": "k"}


def settings(url: str, vocabulary: str = "", language: str = "") -> Settings:
    return Settings(url, "secret", "gpt-4o-transcribe", "chat-model", "agent-model", vocabulary, language)


def body_json(raw: bytes) -> dict[str, object]:
    data: object = json.loads(raw)
    assert isinstance(data, dict)
    return {str(k): v for k, v in data.items()}  # pyright: ignore[reportUnknownVariableType]


# ---- settings --------------------------------------------------------------------------


def test_settings_need_the_endpoint_and_key() -> None:
    assert settings_from({}) == Missing(("OPENAI_BASE_URL", "OPENAI_API_KEY"))
    assert settings_from({"OPENAI_BASE_URL": "https://ai.example/v1"}) == Missing(("OPENAI_API_KEY",))


def test_settings_defaults() -> None:
    assert settings_from(ENV) == Settings("https://ai.example/v1", "k", "gpt-4o-transcribe", "gpt-5.4-mini", "gpt-5.4-mini", "", "")


def test_settings_overrides() -> None:
    env = {
        **ENV,
        "TOOLKIT_TRANSCRIBE_MODEL": "whisper-1",
        "TOOLKIT_CHAT_MODEL": "fast",
        "TOOLKIT_AGENT_MODEL": "smart",
        "TOOLKIT_VOCABULARY": "Miryoku, Colemak",
        "TOOLKIT_LANGUAGE": "sv",
    }
    assert settings_from(env) == Settings("https://ai.example/v1", "k", "whisper-1", "fast", "smart", "Miryoku, Colemak", "sv")


def test_agent_model_follows_chat_model() -> None:
    result = settings_from({**ENV, "TOOLKIT_CHAT_MODEL": "fast"})
    assert isinstance(result, Settings)
    assert result.agent_model == "fast"


# ---- transcription ---------------------------------------------------------------------


def test_transcribe_posts_the_audio_and_returns_the_text() -> None:
    with fake_endpoint() as endpoint:
        audio = wav(bytes(2 * RATE), RATE)
        assert transcribe(settings(endpoint.url), audio) == "hello there"
        r = endpoint.last
        assert r.path == "/v1/audio/transcriptions"
        assert r.authorization == "Bearer secret"
        assert r.content_type.startswith("multipart/form-data; boundary=")
        assert b'name="model"\r\n\r\ngpt-4o-transcribe\r\n' in r.body
        assert b'name="response_format"\r\n\r\njson\r\n' in r.body
        assert b'filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n' + audio + b"\r\n" in r.body
        assert b'name="prompt"' not in r.body
        assert b'name="language"' not in r.body


def test_transcribe_sends_vocabulary_and_language_when_set() -> None:
    with fake_endpoint() as endpoint:
        transcribe(settings(endpoint.url, vocabulary="Miryoku, Colemak", language="sv"), wav(b"", RATE))
        assert b'name="prompt"\r\n\r\nMiryoku, Colemak\r\n' in endpoint.last.body
        assert b'name="language"\r\n\r\nsv\r\n' in endpoint.last.body


def test_failures_carry_the_servers_message() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(b'{"error": {"message": "Invalid session key", "type": "auth"}}', status=401)]
        assert transcribe(settings(endpoint.url), wav(b"", RATE)) == Failure("HTTP 401: Invalid session key")


def test_failures_for_replies_that_arent_what_was_asked() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(b"<html>proxy login</html>")]
        assert transcribe(settings(endpoint.url), wav(b"", RATE)) == Failure("unexpected reply: <html>proxy login</html>")


def test_failures_for_an_unreachable_endpoint() -> None:
    result = transcribe(settings("http://127.0.0.1:9/v1"), wav(b"", RATE), timeout_s=2)
    assert isinstance(result, Failure)
    assert result.message.startswith("can't reach 127.0.0.1:9")


# ---- chat ------------------------------------------------------------------------------

ANSWER = b'{"choices": [{"message": {"role": "assistant", "content": "Hej!"}}]}'


def test_chat_sends_messages_and_returns_the_answer() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(ANSWER)]
        reply = chat(settings(endpoint.url), [system_message("Be brief."), user_message("Say hi")])
        assert reply == Reply("Hej!", ())
        assert endpoint.last.path == "/v1/chat/completions"
        body = body_json(endpoint.last.body)
        assert body["model"] == "chat-model"
        assert body["messages"] == [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Say hi"}]
        assert "tools" not in body
        assert "stream" not in body


def test_chat_can_use_another_model() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(ANSWER)]
        chat(settings(endpoint.url), [user_message("hi")], model="agent-model")
        assert body_json(endpoint.last.body)["model"] == "agent-model"


def test_chat_sends_images_as_data_urls() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(ANSWER)]
        chat(settings(endpoint.url), [user_message([Text("What is this?"), Image(b"\x89PNG")])])
        content = body_json(endpoint.last.body)["messages"]
        assert content == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw=="}},
                ],
            }
        ]


def test_chat_with_tools_returns_tool_calls() -> None:
    call = {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}
    answer = json.dumps({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}).encode()
    spec = ToolSpec("read_file", "Read a file", {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(answer)]
        reply = chat(settings(endpoint.url), [user_message("read a.txt")], tools=[spec])
        assert reply == Reply("", (ToolCall("call_1", "read_file", '{"path": "a.txt"}'),))
        assert body_json(endpoint.last.body)["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                },
            }
        ]


def test_tool_calls_and_results_go_back_to_the_model() -> None:
    call = ToolCall("call_1", "read_file", '{"path": "a.txt"}')
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(ANSWER)]
        chat(settings(endpoint.url), [assistant_message("", (call,)), tool_result("call_1", "file contents")])
        assert body_json(endpoint.last.body)["messages"] == [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file contents"},
        ]


def test_stream_passes_text_on_as_it_arrives() -> None:
    chunks = [json.dumps({"choices": [{"delta": {"content": piece}}]}) for piece in ("Hej", " då", "!")]
    with fake_endpoint() as endpoint:
        endpoint.replies = [sse(json.dumps({"choices": [{"delta": {"role": "assistant"}}]}), *chunks)]
        pieces: list[str] = []
        assert stream(settings(endpoint.url), [user_message("hi")], pieces.append) == "Hej då!"
        assert pieces == ["Hej", " då", "!"]
        assert body_json(endpoint.last.body)["stream"] is True


def test_stream_reports_failures() -> None:
    with fake_endpoint() as endpoint:
        endpoint.replies = [Canned(b'{"error": {"message": "model not found"}}', status=404)]
        assert stream(settings(endpoint.url), [user_message("hi")], print) == Failure("HTTP 404: model not found")
