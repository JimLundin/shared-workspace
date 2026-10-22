"""Delegated Teams chat operations; authentication stays outside model context."""

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, urlencode, urlsplit

from .graph_http import GraphError, Http
from .llm import Json

GRAPH = "https://graph.microsoft.com/v1.0"
MAX_OUTPUT = 20_000
SCOPES = ("User.Read", "User.ReadBasic.All", "Chat.ReadBasic", "Chat.Create", "ChatMessage.Send")
SCOPE_REQUEST = "offline_access " + " ".join(f"https://graph.microsoft.com/{scope}" for scope in SCOPES)


def _text(data: dict[str, Json], name: str, default: str = "") -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise GraphError(f"{name} must be text.")
    return value


def _integer(data: dict[str, Json], name: str, default: int, low: int, high: int) -> int:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise GraphError(f"{name} must be an integer between {low} and {high}.")
    return value


def _object(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise GraphError("Microsoft returned an unexpected response.")
    return value


def _guid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise GraphError(f"{label} must be its Microsoft Entra ID (a GUID).") from error


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _protect(data: bytes) -> bytes:
    from winapi.protection import protect  # noqa: PLC0415

    return protect(data)


def _unprotect(data: bytes) -> bytes:
    from winapi.protection import unprotect  # noqa: PLC0415

    return unprotect(data)


class TokenStore:
    __slots__ = ("path", "protect", "unprotect")

    def __init__(self, path: Path, *, protect: Callable[[bytes], bytes] = _protect, unprotect: Callable[[bytes], bytes] = _unprotect) -> None:
        self.path, self.protect, self.unprotect = path, protect, unprotect

    def read(self) -> dict[str, Json]:
        if not self.path.exists():
            return {}
        try:
            return _object(cast(Json, json.loads(self.unprotect(self.path.read_bytes()))))
        except (OSError, ValueError) as error:
            raise GraphError("The saved Teams sign-in cannot be opened by this Windows account. Use teams_sign_out and sign in again.") from error

    def write(self, data: dict[str, Json]) -> None:
        _atomic(self.path, self.protect(json.dumps(data).encode("utf-8")))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class Transport(Protocol):
    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, cancel: threading.Event,
    ) -> tuple[int, dict[str, Json]]: ...


@dataclass(slots=True)
class DeviceFlow:
    code: str
    user_code: str
    verification_url: str
    expires: float
    interval: int
    next_poll: float


@dataclass(frozen=True, slots=True)
class Page:
    route: str
    chats: bool
    entries: tuple[Json, ...] = ()


class Teams:
    """A configured public client; no client secrets or migration-only app permissions."""

    __slots__ = ("flow", "folder", "http", "lock", "pages", "store")

    def __init__(self, folder: Path, *, store: TokenStore | None = None, http: Transport | None = None) -> None:
        self.folder = folder
        self.store = store or TokenStore(folder / "tokens.bin")
        self.http = http or Http()
        self.flow: DeviceFlow | None = None
        self.pages: dict[str, Page] = {}
        self.lock = threading.RLock()

    def _config(self, *, required: bool = True) -> dict[str, Json]:
        path = self.folder / "config.json"
        try:
            saved = _object(cast(Json, json.loads(path.read_bytes()))) if path.is_file() else {}
        except (OSError, ValueError) as error:
            raise GraphError("Teams setup could not be read. Use teams_configure with the approved app's IDs.") from error
        client = os.environ.get("TOOLKIT_GRAPH_CLIENT_ID") or _text(saved, "client_id")
        tenant = os.environ.get("TOOLKIT_GRAPH_TENANT_ID") or _text(saved, "tenant_id")
        if not client or not tenant:
            if required:
                raise GraphError("Teams setup needs an approved Entra app's client ID and tenant ID. Use teams_configure when available.")
            return {}
        return {"client_id": _guid(client, "Client ID"), "tenant_id": _guid(tenant, "Tenant ID")}

    def _oauth(self, route: str, fields: dict[str, str], cancel: threading.Event) -> tuple[int, dict[str, Json]]:
        config = self._config()
        url = f"https://login.microsoftonline.com/{_text(config, 'tenant_id')}/oauth2/v2.0/{route}"
        return self.http.request("POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, urlencode(fields).encode(), cancel)

    def _save(self, data: dict[str, Json], previous: dict[str, Json] | None = None) -> str:
        token = _text(data, "access_token")
        if not token:
            raise GraphError("Microsoft did not return a usable sign-in token.")
        lifetime = _integer(data, "expires_in", 3600, 1, 172800)
        stored: dict[str, Json] = {
            **self._config(), "access_token": token, "refresh_token": _text(data, "refresh_token") or _text(previous or {}, "refresh_token"),
            "expires_at": time.time() + lifetime, "scope": _text(data, "scope"),
        }
        self.store.write(stored)
        return token

    def _token(self, cancel: threading.Event) -> str:
        config = self._config()
        saved = self.store.read()
        if not saved or any(saved.get(name) != value for name, value in config.items()):
            raise GraphError("Sign in to the configured Teams integration with teams_sign_in first.")
        expires = saved.get("expires_at", 0)
        if isinstance(expires, (int, float)) and expires > time.time() + 60 and _text(saved, "access_token"):
            return _text(saved, "access_token")
        refresh = _text(saved, "refresh_token")
        if not refresh:
            raise GraphError("The Teams sign-in expired. Use teams_sign_in again.")
        status, result = self._oauth("token", {
            "client_id": _text(config, "client_id"), "grant_type": "refresh_token", "refresh_token": refresh, "scope": SCOPE_REQUEST,
        }, cancel)
        if status != 200:
            if result.get("error") in ("invalid_grant", "interaction_required", "consent_required"):
                self.store.clear()
            raise GraphError("Microsoft could not refresh Teams sign-in. Sign in again; consent or Conditional Access may require interaction.")
        return self._save(result, saved)

    def _api(self, method: str, path: str, cancel: threading.Event, body: dict[str, Json] | None = None) -> dict[str, Json]:
        if not path.startswith("/") or path.startswith("//"):
            raise GraphError("Invalid Microsoft Graph route.")
        token = self._token(cancel)
        status, data = self.http.request(
            method, GRAPH + path, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json.dumps(body).encode("utf-8") if body is not None else None, cancel,
        )
        if not 200 <= status < 300:
            if status == 401:
                raise GraphError("Microsoft rejected the Teams sign-in. Sign in again.")
            if status == 403:
                raise GraphError("Microsoft denied this Teams operation. Check the app's delegated permissions, consent and tenant policy.")
            if status == 429:
                raise GraphError("Microsoft is throttling Teams requests. Wait before trying again.")
            raise GraphError(f"Microsoft Graph returned HTTP {status}. A send is never retried automatically; inspect the chat before retrying.")
        return data

    def _begin(self, cancel: threading.Event) -> dict[str, Json]:
        config = self._config()
        status, result = self._oauth("devicecode", {"client_id": _text(config, "client_id"), "scope": SCOPE_REQUEST}, cancel)
        if status != 200:
            raise GraphError("Microsoft did not start sign-in. Check the approved app's public-client/device-code settings and tenant policy.")
        code, user_code, verification = _text(result, "device_code"), _text(result, "user_code"), _text(result, "verification_uri")
        parsed = urlsplit(verification)
        if (not code or not user_code or parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.netloc not in ("microsoft.com", "www.microsoft.com", "login.microsoftonline.com")):
            raise GraphError("Microsoft returned an unexpected sign-in destination.")
        interval = _integer(result, "interval", 5, 1, 60)
        self.flow = DeviceFlow(code, user_code, verification, time.monotonic() + _integer(result, "expires_in", 900, 1, 3600),
                               interval, time.monotonic() + interval)
        return {
            "state": "waiting_for_sign_in", "verification_url": verification, "user_code": user_code,
            "message": "Show this code to the user. browser_sign_in can open the URL; use teams_finish_sign_in after they sign in.",
        }

    def _finish(self, timeout: int, cancel: threading.Event) -> dict[str, Json]:
        flow = self.flow
        if flow is None:
            raise GraphError("No Teams sign-in is pending. Use teams_sign_in first.")
        deadline = min(time.monotonic() + timeout, flow.expires)
        while time.monotonic() < deadline:
            if cancel.wait(.05):
                self.flow = None
                raise GraphError("Teams sign-in stopped.")
            if time.monotonic() < flow.next_poll:
                continue
            status, result = self._oauth("token", {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": _text(self._config(), "client_id"), "device_code": flow.code,
            }, cancel)
            flow.next_poll = time.monotonic() + flow.interval
            if status == 200:
                self._save(result)
                self.flow = None
                return {"state": "signed_in", "message": "Teams sign-in saved with Windows account protection."}
            error = result.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                flow.interval += 5
                flow.next_poll = time.monotonic() + flow.interval
                continue
            self.flow = None
            raise GraphError("Teams sign-in was declined, expired or blocked by Microsoft. Start sign-in again when ready.")
        if time.monotonic() >= flow.expires:
            self.flow = None
            raise GraphError("The Teams sign-in code expired. Start sign-in again.")
        return {"state": "waiting_for_sign_in", "message": "The user has not finished signing in. Call teams_finish_sign_in after they finish."}

    def _page(self, page: Page, cancel: threading.Event) -> dict[str, Json]:
        if page.entries:
            return self._batch(list(page.entries), page.route, chats=page.chats)
        data = self._api("GET", page.route, cancel)
        entries = data.get("value", [])
        if not isinstance(entries, list):
            raise GraphError("Microsoft returned an invalid list.")
        values: list[Json] = []
        for item in entries[:50]:
            if not isinstance(item, dict):
                continue
            fields = ("id", "topic", "chatType", "webUrl") if page.chats else ("id", "displayName", "mail", "userPrincipalName")
            value: dict[str, Json] = {name: _text(item, name)[:500] if isinstance(item.get(name), str) else None for name in fields}
            if page.chats and isinstance(members := item.get("members"), list):
                value["members"] = [{name: _text(member, name)[:200] if isinstance(member.get(name), str) else None
                                     for name in ("userId", "displayName", "email")} for member in members[:10]
                                    if isinstance(member, dict)]
                value["members_truncated"] = len(members) > 10 or "members@odata.nextLink" in item
            values.append(value)
        route = ""
        if next_page := _text(data, "@odata.nextLink"):
            parsed = urlsplit(next_page)
            expected = "/v1.0/me/chats" if page.chats else "/v1.0/users"
            if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or parsed.path != expected or parsed.fragment:
                raise GraphError("Microsoft returned an unexpected pagination destination.")
            route = parsed.path.removeprefix("/v1.0") + ("?" + parsed.query if parsed.query else "")
        return self._batch(values, route, chats=page.chats)

    def _batch(self, values: list[Json], route: str, *, chats: bool) -> dict[str, Json]:
        result: dict[str, Json] = {"chats" if chats else "users": values}
        remaining: list[Json] = []
        while len(json.dumps(result, ensure_ascii=False)) > MAX_OUTPUT - 100:
            if len(values) == 1:
                raise GraphError("A Microsoft record exceeded the output limit. Narrow the lookup.")
            remaining.insert(0, values.pop())
        if remaining or route:
            if len(self.pages) >= 32:
                self.pages.pop(next(iter(self.pages)))
            cursor = uuid.uuid4().hex
            self.pages[cursor] = Page(route, chats, tuple(remaining))
            result["next_cursor"] = cursor
        return result

    def request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> str:
        with self.lock:
            if cancel.is_set():
                self.flow = None
                raise GraphError("Stopped before contacting Teams.")
            try:
                result = self._request(operation, args, cancel)
            finally:
                if cancel.is_set():
                    self.flow = None
            return json.dumps(result, ensure_ascii=False)

    def cancel_sign_in(self) -> None:
        with self.lock:
            self.flow = None

    def _configure(self, args: dict[str, Json]) -> dict[str, Json]:
        config: dict[str, Json] = {
            "client_id": _guid(_text(args, "client_id"), "Client ID"), "tenant_id": _guid(_text(args, "tenant_id"), "Tenant ID"),
        }
        for key, variable in (("client_id", "TOOLKIT_GRAPH_CLIENT_ID"), ("tenant_id", "TOOLKIT_GRAPH_TENANT_ID")):
            if (environment := os.environ.get(variable)) and _guid(environment, key) != config[key]:
                raise GraphError(f"{variable} overrides saved setup. Update that environment setting before saving different app IDs.")
        if config != self._config(required=False):
            self.store.clear()
            self.flow = None
            self.pages.clear()
        _atomic(self.folder / "config.json", json.dumps(config, indent=2).encode())
        return {"configured": True, "message": "Teams app IDs saved. Use teams_sign_in for delegated sign-in.", "scopes": list(SCOPES)}

    def _request(self, operation: str, args: dict[str, Json], cancel: threading.Event) -> dict[str, Json]:
        if operation == "configure":
            return self._configure(args)
        if operation == "status":
            config = self._config(required=False)
            saved = self.store.read() if config else {}
            return {
                "configured": bool(config), "saved_sign_in": bool(saved) and all(saved.get(k) == v for k, v in config.items()),
                "sign_in_pending": self.flow is not None and self.flow.expires > time.monotonic(), "scopes": list(SCOPES),
                "message": "Use teams_configure with an approved app's client and tenant IDs, then teams_sign_in." if not config else "",
            }
        if operation == "sign_out":
            self.store.clear()
            self.flow = None
            self.pages.clear()
            return {"state": "signed_out", "message": "Toolkit's saved Teams API tokens were removed. Browser sign-in is separate."}
        if operation == "sign_in":
            return self._begin(cancel)
        if operation == "finish_sign_in":
            return self._finish(_integer(args, "timeout_seconds", 30, 1, 600), cancel)
        if operation in ("find_users", "list_chats"):
            chats = operation == "list_chats"
            if cursor := _text(args, "cursor"):
                page = self.pages.get(cursor)
                if page is None or page.chats != chats:
                    raise GraphError("That page cursor is unavailable. Start the lookup again.")
            else:
                query: dict[str, str] = {"$top": str(_integer(args, "limit", 10, 1, 20))}
                if chats:
                    query.update({"$expand": "members", "$orderby": "lastMessagePreview/createdDateTime desc"})
                else:
                    search = _text(args, "query").strip()
                    if not 2 <= len(search) <= 200:
                        raise GraphError("Use at least two characters of a recipient's name, email or user principal name.")
                    escaped = search.replace("'", "''")
                    query.update({
                        "$select": "id,displayName,mail,userPrincipalName",
                        "$filter": f"startswith(displayName,'{escaped}') or startswith(userPrincipalName,'{escaped}') or mail eq '{escaped}'",
                    })
                route = ("/me/chats?" if chats else "/users?") + urlencode(query)
                page = Page(route, chats)
            return self._page(page, cancel)
        if operation == "open_chat":
            user = _guid(_text(args, "user_id"), "Recipient ID")
            me = self._api("GET", "/me?$select=id,userType", cancel)
            own_id = _guid(_text(me, "id"), "Signed-in user ID")
            if own_id == user:
                raise GraphError("Choose another user for a one-to-one chat.")
            members: list[Json] = []
            for identity, guest in ((own_id, me.get("userType") == "Guest"), (user, args.get("guest") is True)):
                members.append({"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["guest" if guest else "owner"],
                                "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{identity}')"})
            chat = self._api("POST", "/chats", cancel, {"chatType": "oneOnOne", "members": members})
            return {name: chat.get(name) for name in ("id", "chatType", "webUrl")}
        if operation == "send_message":
            chat_id, content = _text(args, "chat_id"), _text(args, "text")
            if not chat_id or len(chat_id) > 500 or not content.strip() or len(content) > 10_000 or len(content.encode("utf-8")) > 24_000:
                raise GraphError("Provide a chat ID and non-empty message of at most 10,000 characters / 24 KB.")
            result = self._api("POST", f"/chats/{quote(chat_id, safe='')}/messages", cancel, {"body": {"contentType": "text", "content": content}})
            if not _text(result, "id"):
                raise GraphError("Microsoft accepted the request but returned no message ID. Inspect the chat before retrying.")
            return {"sent": True, "chat_id": chat_id, "message_id": result["id"], "created_at": result.get("createdDateTime")}
        raise GraphError("Unknown Teams operation.")
