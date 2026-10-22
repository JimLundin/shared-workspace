"""Bounded, cancellable HTTPS for Microsoft authentication and Graph."""

import asyncio
import contextlib
import json
import ssl
import threading
from typing import cast

from .llm import Json

REQUEST_TIMEOUT = 25


class GraphError(OSError):
    pass


class Http:
    __slots__ = ()

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, cancel: threading.Event,
    ) -> tuple[int, dict[str, Json]]:
        if cancel.is_set():
            raise GraphError("Stopped before contacting Microsoft.")
        return asyncio.run(self._request(method, url, headers, body, cancel))

    async def _request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, cancel: threading.Event,
    ) -> tuple[int, dict[str, Json]]:
        import httpx  # noqa: PLC0415 (no SDK or HTTP client work on Toolkit startup)

        async def receive() -> tuple[int, dict[str, Json]]:
            async with (
                httpx.AsyncClient(verify=ssl.create_default_context(), timeout=20, follow_redirects=False) as client,
                client.stream(method, url, headers=headers, content=body) as response,
            ):
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1_000_000:
                        raise GraphError("Microsoft's response exceeded the size limit.")
                result = cast(Json, json.loads(data or b"{}"))
                if not isinstance(result, dict):
                    raise GraphError("Microsoft returned an unexpected response.")
                return response.status_code, result

        pending = asyncio.create_task(receive())
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                while not pending.done():
                    if cancel.is_set():
                        raise GraphError("Stopped. A request already sent to Microsoft may have completed; check its result before retrying.")
                    await asyncio.sleep(.05)
                return await pending
        except (httpx.HTTPError, ValueError, TimeoutError) as error:
            # Never include request headers, response bodies or token endpoint
            # diagnostics in an exception that the agent could journal.
            raise GraphError("Microsoft's request failed or timed out. Check the result before retrying a send.") from error
        finally:
            if not pending.done():
                pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, GraphError, httpx.HTTPError, ValueError):
                await pending
