"""Observe exact live browser excerpts without connecting, focusing or navigating.

Callers own Browser and connect it explicitly. Failed, cancelled or unbound
reads contain no replacement content, so the previous observation stays intact.
"""

import contextlib
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from automation.task import identifier, integer, text, timestamp, variant

from .browser import Attached, Browser, Cdp
from .browser_values import BrowserError, DisconnectedError, InterruptedError, mapping
from .llm import Json
from .page_source_values import (
    MAX_DOCUMENT_UNITS,
    MAX_TEXT,
    BrowserKind,
    Cancelled,
    Complete,
    Excerpt,
    ObservedPage,
    PageProvenance,
    PageReference,
    ReadResult,
    RebindRequired,
    Scope,
    Unavailable,
    observation_of,
    provenance_data,
    provenance_of,
    validate_provenance,
)

__all__ = [
    "MAX_DOCUMENT_UNITS", "MAX_TEXT", "BrowserKind", "Cancelled", "Complete", "Excerpt", "ObservedPage", "PageProvenance",
    "PageReference", "PageSources", "ReadResult", "RebindRequired", "Scope", "Unavailable", "provenance_data", "provenance_of",
    "validate_provenance",
]

# A single synchronous evaluation binds URL, title, coverage and text together.
# It reads only the main document, not form values, frames or browser storage.
_READ = r"""(() => {
    if (document.readyState === 'loading') return {kind: 'loading'};
    if (!document.body) return {kind: 'no_body'};
    const text = document.body.innerText;
    let end = Math.min(text.length, 64000);
    const last = end > 0 ? text.charCodeAt(end - 1) : 0;
    if (end < text.length && last >= 0xD800 && last <= 0xDBFF) end--;
    return {
        kind: 'source', url: location.href, title: document.title,
        text: text.slice(0, end), end_utf16: end, total_utf16: text.length
    };
})()"""

class _Detached(Enum):
    SESSION = auto()


@dataclass(frozen=True, slots=True)
class _Binding:
    connection: Cdp
    id: str


def _target(connection: Cdp, target_id: str, cancel: threading.Event) -> dict[str, Json] | None:
    values = connection.call("Target.getTargets", {}, cancel).get("targetInfos")
    if not isinstance(values, list):
        raise ValueError("The browser returned no valid tab catalog.")
    candidates = tuple(value for value in values if isinstance(value, dict) and value.get("targetId") == target_id)
    if len(candidates) > 1:
        raise ValueError("The browser returned an ambiguous tab identity.")
    if not candidates or candidates[0].get("type") != "page":
        return None
    return candidates[0]


class PageSources:
    """Read selected tabs on the caller's background worker; never connect or focus.

    capture binds the currently selected tab. refresh accepts only that exact
    live binding and URL. A new connection, closed tab or navigation requires
    explicit capture again. Failures contain no replacement source content.
    The caller must still match its refresh request before applying a result.
    """

    __slots__ = ("_binding", "browser")

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self._binding: _Binding | _Detached = _Detached.SESSION

    def capture(self, target_id: str, cancel: threading.Event, *, at: float) -> ReadResult:
        return self._read(identifier(target_id, "selected tab identity"), None, cancel, timestamp(at, "page observation time"))

    def refresh(self, reference: PageReference, cancel: threading.Event, *, at: float) -> ReadResult:
        variant(reference, PageReference, "page reference")
        return self._read(reference.target_id, reference, cancel, timestamp(at, "page observation time"))

    def resolve(self, reference: PageReference, cancel: threading.Event) -> PageReference | Unavailable | RebindRequired | Cancelled:
        """Validate an exact live reference without reading or focusing its page.

        A caller that then acts on the target must hold Browser.lock across both
        resolution and the action so the connection cannot be replaced between.
        """
        variant(reference, PageReference, "page reference")
        return self._run(reference, cancel, lambda: self._reference(reference.target_id, reference, cancel))

    def _reference(
        self, target_id: str, previous: PageReference | None, cancel: threading.Event,
    ) -> PageReference | Unavailable | RebindRequired:
        connection, owner = self.browser.connection, self.browser.owner
        if connection is None or not isinstance(owner, Attached):
            self._binding = _Detached.SESSION
            return (RebindRequired(previous, "The original browser connection is unavailable. Select its current tab to reconnect.")
                    if previous is not None else Unavailable("Connect an existing Edge or Chrome session before selecting a page source."))
        if not isinstance(self._binding, _Binding) or self._binding.connection is not connection:
            self._binding = _Binding(connection, uuid.uuid4().hex)
        binding = self._binding
        if previous is not None and (previous.binding_id != binding.id or previous.browser.value != owner.product.value):
            return RebindRequired(previous, "This reference belongs to an earlier browser connection. Select its current tab again.")
        selected = _target(connection, target_id, cancel)
        if selected is None:
            return (RebindRequired(previous, "The original tab has closed. Select its replacement explicitly.") if previous is not None else
                    Unavailable("The selected browser tab is no longer available."))
        reference = PageReference(BrowserKind(owner.product.value), binding.id, target_id,
                                  text(selected.get("url"), "selected page URL", maximum=8_192))
        if previous is not None and reference.url != previous.url:
            return RebindRequired(previous, "The original tab navigated to another page. Select the intended page again.")
        return reference

    def _observed(self, target_id: str, previous: PageReference | None, cancel: threading.Event, at: float) -> ReadResult:
        reference = self._reference(target_id, previous, cancel)
        if not isinstance(reference, PageReference):
            return reference
        connection = self.browser.connection
        if connection is None:
            raise DisconnectedError("The browser connection ended before the page read.")
        return self._capture(connection, reference, cancel, at)

    def _capture(self, connection: Cdp, reference: PageReference, cancel: threading.Event, at: float) -> ReadResult:
        # Keep our temporary CDP session separate from Browser's control sessions.
        try:
            attached = connection.call("Target.attachToTarget", {"targetId": reference.target_id, "flatten": True}, cancel)
            session = identifier(attached.get("sessionId"), "source session identity")
        except (OSError, ValueError):
            # A missing acknowledgement may still have attached a session.
            self._disconnect()
            raise
        try:
            tree = mapping(connection.call("Page.getFrameTree", {}, cancel, session=session).get("frameTree"))
            frame = identifier(mapping(tree.get("frame")).get("id"), "main document frame")
            world = connection.call("Page.createIsolatedWorld", {"frameId": frame, "worldName": "toolkit-page-source"}, cancel, session=session)
            context = integer(world.get("executionContextId"), "source execution context", minimum=1, maximum=2**53)
            evaluated = connection.call("Runtime.evaluate", {
                "expression": _READ, "contextId": context, "returnByValue": True, "silent": True, "userGesture": False,
            }, cancel, session=session)
            observation: ReadResult
            if "exceptionDetails" in evaluated:
                observation = Unavailable("The selected document could not supply readable text. Its previous observation was kept.")
            else:
                observation = observation_of(reference, mapping(evaluated.get("result")).get("value"), at)
        finally:
            try:
                connection.call("Target.detachFromTarget", {"sessionId": session}, threading.Event(), timeout=1)
            except (OSError, ValueError):
                # Detaching this borrowed connection cannot close the user's app.
                self._disconnect()
        if self.browser.connection is not connection:
            return Unavailable("The browser connection ended before its source read finished. Its previous observation was kept.")
        return observation

    def _disconnect(self) -> None:
        with contextlib.suppress(OSError):
            self.browser.close()
        self._binding = _Detached.SESSION

    def _read(self, target_id: str, previous: PageReference | None, cancel: threading.Event, at: float) -> ReadResult:
        return self._run(previous, cancel, lambda: self._observed(target_id, previous, cancel, at))

    def _run[T](
        self, previous: PageReference | None, cancel: threading.Event, operation: Callable[[], T],
    ) -> T | Unavailable | RebindRequired | Cancelled:
        if cancel.is_set():
            return Cancelled.READ
        if previous is not None and previous.browser is BrowserKind.TOOLKIT:
            return RebindRequired(previous, "This source belongs to a Toolkit browser card. Capture that card again for a new observation.")
        deadline = time.monotonic() + 10
        while not self.browser.lock.acquire(timeout=.05):
            if cancel.is_set():
                return Cancelled.READ
            if time.monotonic() >= deadline:
                return Unavailable("The browser is busy. Its previous source observation was kept.")
        try:
            if cancel.is_set():
                return Cancelled.READ
            result = operation()
            return Cancelled.READ if cancel.is_set() else result
        except InterruptedError:
            return Cancelled.READ if cancel.is_set() else Unavailable("The page read timed out. Its previous observation was kept.")
        except DisconnectedError:
            self._disconnect()
            return (RebindRequired(previous, "The original browser connection ended. Select its current tab to reconnect.")
                    if previous is not None else Unavailable("The browser connection ended before its source could be read."))
        except (BrowserError, OSError, ValueError):
            return Unavailable("The browser returned unavailable or invalid source data. Its previous observation was kept.")
        finally:
            self.browser.lock.release()
