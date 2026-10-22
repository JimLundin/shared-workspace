"""Teams setup, delegated auth and sends against a controlled transport."""

import os
import threading
import time
import zlib
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from assistant.graph_http import GraphError
from assistant.llm import Json
from assistant.teams import GRAPH, MAX_OUTPUT, SCOPES, Teams, TokenStore

from .test_browser import decoded

CLIENT = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
OWN = "33333333-3333-3333-3333-333333333333"
RECIPIENT = "44444444-4444-4444-4444-444444444444"
CONFIG: dict[str, Json] = {"client_id": CLIENT, "tenant_id": TENANT}
ENVIRONMENT = {"TOOLKIT_GRAPH_CLIENT_ID": "", "TOOLKIT_GRAPH_TENANT_ID": ""}
type Reply = tuple[int, dict[str, Json]] | Exception


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


@dataclass(slots=True)
class Http:
    replies: list[Reply] = field(default_factory=list[Reply])
    received: list[Request] = field(default_factory=list[Request])
    stop_during_request: bool = False

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, cancel: threading.Event,
    ) -> tuple[int, dict[str, Json]]:
        self.received.append(Request(method, url, headers, body))
        if self.stop_during_request:
            cancel.set()
            raise GraphError("Stopped during the request.")
        assert self.replies, "The test did not authorize a network request."
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@contextmanager
def integration(folder: Path, *, signed_in: bool = False) -> Generator[tuple[Teams, Http]]:
    with patch.dict(os.environ, ENVIRONMENT):
        # Compression is only a deterministic fake for the DPAPI boundary.
        store = TokenStore(folder / "tokens.bin", protect=zlib.compress, unprotect=zlib.decompress)
        http = Http()
        teams = Teams(folder, store=store, http=http)
        teams.request("configure", CONFIG, threading.Event())
        if signed_in:
            store.write({
                **CONFIG, "access_token": "fixture-access-secret", "refresh_token": "fixture-refresh-secret", "expires_at": time.time() + 3600,
            })
        yield teams, http


def begin(teams: Teams, http: Http) -> str:
    http.replies.append((200, {
        "device_code": "fixture-device-secret", "user_code": "ABCD-EFGH", "verification_uri": "https://microsoft.com/devicelogin",
        "expires_in": 900, "interval": 1,
    }))
    result = teams.request("sign_in", {}, threading.Event())
    assert teams.flow is not None
    teams.flow.next_poll = 0
    return result


def test_teams_setup_is_offline_and_unconfigured_status_is_actionable(tmp_path: Path) -> None:
    with patch.dict(os.environ, ENVIRONMENT):
        http = Http()
        teams = Teams(tmp_path / "teams", http=http)
        status = decoded(teams.request("status", {}, threading.Event()))
        assert not status["configured"] and not teams.folder.exists() and not http.received
        assert "teams_configure" in cast(str, status["message"])
        result = decoded(teams.request("configure", CONFIG, threading.Event()))
        assert result["configured"] and decoded((teams.folder / "config.json").read_text()) == CONFIG
        assert not teams.store.path.exists() and not http.received
        assert set(SCOPES) == {"User.Read", "User.ReadBasic.All", "Chat.ReadBasic", "Chat.Create", "ChatMessage.Send"}


def test_environment_overrides_cannot_silently_ignore_saved_configuration(tmp_path: Path) -> None:
    overrides = {"TOOLKIT_GRAPH_CLIENT_ID": RECIPIENT}
    with integration(tmp_path) as (teams, http), patch.dict(os.environ, overrides):
        with TestCase().assertRaisesRegex(GraphError, "overrides"):
            teams.request("configure", CONFIG, threading.Event())
        assert not http.received


def test_device_flow_keeps_tokens_out_of_results_and_persists_protected_credentials(tmp_path: Path) -> None:
    with integration(tmp_path) as (teams, http):
        started = begin(teams, http)
        assert "ABCD-EFGH" in started and "fixture-device-secret" not in started
        fields = parse_qs((http.received[0].body or b"").decode())
        assert fields["client_id"] == [CLIENT] and "offline_access" in fields["scope"][0]
        assert http.received[0].url.startswith(f"https://login.microsoftonline.com/{TENANT}/")
        http.replies.append((200, {"access_token": "fixture-access-secret", "refresh_token": "fixture-refresh-secret", "expires_in": 3600}))
        finished = teams.request("finish_sign_in", {"timeout_seconds": 2}, threading.Event())
        assert decoded(finished)["state"] == "signed_in" and teams.flow is None
        assert "fixture-access-secret" not in finished and "fixture-refresh-secret" not in finished
        assert b"fixture-access-secret" not in teams.store.path.read_bytes()
        assert teams.store.read()["refresh_token"] == "fixture-refresh-secret"
        assert "fixture-access-secret" not in teams.request("status", {}, threading.Event())


def test_declined_and_cancelled_sign_in_clear_the_pending_flow(tmp_path: Path) -> None:
    with integration(tmp_path) as (teams, http):
        begin(teams, http)
        http.replies.append((400, {"error": "authorization_declined", "error_description": "private diagnostic"}))
        with TestCase().assertRaisesRegex(GraphError, "declined") as failed:
            teams.request("finish_sign_in", {"timeout_seconds": 2}, threading.Event())
        assert "private diagnostic" not in str(failed.exception) and teams.flow is None
        begin(teams, http)
        http.stop_during_request = True
        with TestCase().assertRaisesRegex(GraphError, "Stopped"):
            teams.request("finish_sign_in", {"timeout_seconds": 2}, threading.Event())
        assert teams.flow is None and not teams.store.path.exists()


def test_device_flow_respects_pending_and_slow_down_responses(tmp_path: Path) -> None:
    with integration(tmp_path) as (teams, http):
        begin(teams, http)
        http.replies.append((400, {"error": "slow_down"}))
        result = decoded(teams.request("finish_sign_in", {"timeout_seconds": 1}, threading.Event()))
        assert result["state"] == "waiting_for_sign_in"
        assert teams.flow is not None and teams.flow.interval == 6
        assert len(http.received) == 2
        teams.flow.expires = time.monotonic() - 1
        with TestCase().assertRaisesRegex(GraphError, "expired"):
            teams.request("finish_sign_in", {"timeout_seconds": 1}, threading.Event())
        assert teams.flow is None


def test_expired_tokens_refresh_and_invalid_grants_remove_the_saved_sign_in(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        saved = teams.store.read()
        saved["expires_at"] = 0
        teams.store.write(saved)
        http.replies.extend([(200, {"access_token": "refreshed-secret", "expires_in": 3600}), (200, {"value": []})])
        assert decoded(teams.request("list_chats", {}, threading.Event())) == {"chats": []}
        assert parse_qs((http.received[0].body or b"").decode())["grant_type"] == ["refresh_token"]
        assert http.received[1].headers["Authorization"] == "Bearer refreshed-secret"
        # Graph supports this ordering for chats, but not lastUpdatedDateTime.
        assert parse_qs(urlsplit(http.received[1].url).query)["$orderby"] == ["lastMessagePreview/createdDateTime desc"]
        saved = teams.store.read()
        assert saved["refresh_token"] == "fixture-refresh-secret"
        saved["expires_at"] = 0
        teams.store.write(saved)
        http.replies.append((400, {"error": "invalid_grant"}))
        with TestCase().assertRaisesRegex(GraphError, "Sign in again"):
            teams.request("list_chats", {}, threading.Event())
        assert not teams.store.path.exists()


def test_user_lookup_escapes_filter_literals_and_uses_opaque_checked_page_cursors(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        http.replies.append((200, {"value": [{"id": RECIPIENT, "displayName": "O'Brien"}],
                                  "@odata.nextLink": GRAPH + "/users?$skiptoken=opaque"}))
        first = decoded(teams.request("find_users", {"query": "O'Brien"}, threading.Event()))
        query = parse_qs(urlsplit(http.received[0].url).query)
        assert "O''Brien" in query["$filter"][0]
        http.replies.append((200, {"value": []}))
        assert decoded(teams.request("find_users", {"cursor": first["next_cursor"]}, threading.Event())) == {"users": []}
        assert http.received[1].url == GRAPH + "/users?$skiptoken=opaque"
        with TestCase().assertRaisesRegex(GraphError, "cursor"):
            teams.request("list_chats", {"cursor": first["next_cursor"]}, threading.Event())


def test_pagination_never_forwards_tokens_to_an_unexpected_host_or_route(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        for link in ("https://example.com/v1.0/users", GRAPH + "/me/chats", "https://graph.microsoft.com@evil.test/v1.0/users"):
            http.replies.append((200, {"value": [], "@odata.nextLink": link}))
            with TestCase().assertRaisesRegex(GraphError, "pagination destination"):
                teams.request("find_users", {"query": "Alice"}, threading.Event())
        assert len(http.received) == 3 and all(r.url.startswith(GRAPH + "/users?") for r in http.received)


def test_large_chat_pages_remain_bounded_and_keep_all_results_reachable(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        rows: list[Json] = [{"id": f"chat-{i}", "topic": "x" * 500, "chatType": "group",
                             "members": [{"userId": f"user-{n}", "displayName": "n" * 200, "email": "e" * 200} for n in range(20)]}
                            for i in range(20)]
        http.replies.append((200, {"value": rows}))
        seen: list[Json] = []
        arguments: dict[str, Json] = {}
        for _ in range(21):
            output = teams.request("list_chats", arguments, threading.Event())
            assert len(output) <= MAX_OUTPUT
            data = decoded(output)
            entries = cast(list[Json], data["chats"])
            seen.extend(entries)
            if "next_cursor" not in data:
                break
            arguments = {"cursor": data["next_cursor"]}
        assert len(seen) == 20 and len(http.received) == 1
        assert all(isinstance(row, dict) and row["members_truncated"] for row in seen)


def test_one_to_one_chat_uses_resolved_id_and_guest_role_without_sending(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        http.replies.extend([(200, {"id": OWN, "userType": "Member"}), (201, {"id": "fixture-chat", "chatType": "oneOnOne"})])
        result = decoded(teams.request("open_chat", {"user_id": RECIPIENT, "guest": True}, threading.Event()))
        assert result["id"] == "fixture-chat"
        body = decoded((http.received[1].body or b"").decode())
        members = cast(list[dict[str, Json]], body["members"])
        assert members[0]["roles"] == ["owner"] and members[1]["roles"] == ["guest"]
        assert RECIPIENT in cast(str, members[1]["user@odata.bind"])
        assert http.received[1].url == GRAPH + "/chats" and not any("/messages" in request.url for request in http.received)


def test_sends_are_plain_text_and_uncertain_failures_are_never_retried(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        http.replies.append((201, {"id": "message-1", "createdDateTime": "2026-10-22T00:00:00Z"}))
        result = decoded(teams.request("send_message", {"chat_id": "chat/with?chars", "text": "<b>literal</b>"}, threading.Event()))
        assert result["sent"] and result["message_id"] == "message-1"
        assert http.received[0].url == GRAPH + "/chats/chat%2Fwith%3Fchars/messages"
        assert decoded((http.received[0].body or b"").decode()) == {"body": {"contentType": "text", "content": "<b>literal</b>"}}
        failures: list[Reply] = [(503, {"error": {"message": "private body"}}), GraphError("Timed out; inspect before retrying"), (201, {})]
        for reply in failures:
            before = len(http.received)
            http.replies.append(reply)
            with TestCase().assertRaises(GraphError) as failed:
                teams.request("send_message", {"chat_id": "fixture-chat", "text": "once"}, threading.Event())
            assert len(http.received) == before + 1 and "private body" not in str(failed.exception)


def test_consent_denial_does_not_leak_graph_diagnostics_and_stop_prevents_sends(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        http.replies.append((403, {"error": {"message": "fixture-access-secret diagnostic"}}))
        with TestCase().assertRaisesRegex(GraphError, "consent") as failed:
            teams.request("send_message", {"chat_id": "fixture-chat", "text": "once"}, threading.Event())
        assert "fixture-access-secret" not in str(failed.exception)
        stop = threading.Event()
        stop.set()
        with TestCase().assertRaisesRegex(GraphError, "Stopped"):
            teams.request("send_message", {"chat_id": "fixture-chat", "text": "never"}, stop)
        assert len(http.received) == 1


def test_sign_out_and_reconfiguration_remove_credentials_and_old_cursors(tmp_path: Path) -> None:
    with integration(tmp_path, signed_in=True) as (teams, http):
        begin(teams, http)
        teams.request("configure", {"client_id": RECIPIENT, "tenant_id": TENANT}, threading.Event())
        assert not teams.store.path.exists() and teams.flow is None and not teams.pages
        teams.store.write({"access_token": "fixture-secret"})
        teams.request("sign_out", {}, threading.Event())
        assert not teams.store.path.exists()
