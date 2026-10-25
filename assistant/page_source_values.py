"""Pure immutable browser-source values, provenance codecs and content checks."""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import assert_never, cast
from urllib.parse import urlsplit

from automation.task import digest, identifier, integer, text, timestamp, variant

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None

MAX_TEXT = 64_000
MAX_DOCUMENT_UNITS = 100_000_000


class BrowserKind(Enum):
    EDGE = "edge"
    CHROME = "chrome"
    TOOLKIT = "toolkit"


def _url(value: object) -> str:
    result = text(value, "page URL", maximum=8_192)
    parsed = urlsplit(result)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
            or any(ord(character) < 32 for character in result)):
        raise ValueError("Use an HTTP or HTTPS page without embedded credentials.")
    result.encode("utf-8")
    return result


def web_app_url(value: object) -> str:
    """Validate a new browser navigation without changing saved-source parsing."""
    result = _url(value)
    port = urlsplit(result).port
    if result != result.strip() or (port is not None and not 1 <= port <= 65_535):
        raise ValueError("App pages require a valid HTTP or HTTPS URL without surrounding whitespace.")
    return result


def _object(value: object) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("The browser returned an invalid source object.")
    return cast(dict[str, Json], value)


def _units(value: str) -> int:
    # DOM offsets count UTF-16 units. Encoding also rejects lone surrogates.
    return len(value.encode("utf-16-le")) // 2


@dataclass(frozen=True, slots=True)
class PageReference:
    browser: BrowserKind
    binding_id: str
    target_id: str
    url: str

    def __post_init__(self) -> None:
        variant(self.browser, BrowserKind, "browser source")
        identifier(self.binding_id, "browser binding")
        identifier(self.target_id, "browser tab identity")
        _url(self.url)


class Complete(Enum):
    MAIN_DOCUMENT = auto()


@dataclass(frozen=True, slots=True)
class Excerpt:
    start_utf16: int
    end_utf16: int
    total_utf16: int

    def __post_init__(self) -> None:
        integer(self.start_utf16, "excerpt start", maximum=MAX_DOCUMENT_UNITS)
        integer(self.end_utf16, "excerpt end", minimum=self.start_utf16 + 1, maximum=MAX_DOCUMENT_UNITS)
        integer(self.total_utf16, "document length", minimum=self.end_utf16, maximum=MAX_DOCUMENT_UNITS)
        if self.start_utf16 == 0 and self.end_utf16 == self.total_utf16:
            raise ValueError("Complete main-document text is not an excerpt.")


type Scope = Complete | Excerpt


def _scope_data(scope: Scope) -> dict[str, Json]:
    match scope:
        case Complete.MAIN_DOCUMENT:
            return {"kind": "complete_main_document"}
        case Excerpt(start, end, total):
            return {"kind": "excerpt", "start_utf16": start, "end_utf16": end, "total_utf16": total}
        case _:
            assert_never(scope)


def _version(reference: PageReference, content: str, scope: Scope) -> str:
    text(content, "page text", maximum=MAX_TEXT, empty=True)
    units = _units(content)
    if units > MAX_TEXT or (isinstance(scope, Excerpt) and scope.end_utf16 - scope.start_utf16 != units):
        raise ValueError("The observed text does not match its declared scope.")
    value: dict[str, Json] = {
        "browser": reference.browser.value, "binding": reference.binding_id, "target": reference.target_id,
        "url": reference.url, "text": content, "scope": _scope_data(scope),
    }
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PageProvenance:
    reference: PageReference
    scope: Scope
    version: str

    def __post_init__(self) -> None:
        variant(self.reference, PageReference, "page reference")
        variant(self.scope, (Complete, Excerpt), "page text scope")
        digest(self.version, "page content version")


def validate_provenance(provenance: PageProvenance, content: str) -> None:
    """Stored source/evidence text must match the provenance beside it."""
    variant(provenance, PageProvenance, "page provenance")
    if _version(provenance.reference, content, provenance.scope) != provenance.version:
        raise ValueError("The saved page text does not match its source provenance.")


def provenance_data(provenance: PageProvenance) -> dict[str, Json]:
    variant(provenance, PageProvenance, "page provenance")
    reference = provenance.reference
    return {
        "format": 1,
        "reference": {"browser": reference.browser.value, "binding_id": reference.binding_id,
                      "target_id": reference.target_id, "url": reference.url},
        "scope": _scope_data(provenance.scope), "version": provenance.version,
    }


def _fields(value: object, names: tuple[str, ...]) -> dict[str, Json]:
    result = _object(value)
    if set(result) != set(names):
        raise ValueError("The saved page provenance has missing or unexpected fields.")
    return result


def provenance_of(value: object) -> PageProvenance:
    data = _fields(value, ("format", "reference", "scope", "version"))
    integer(data["format"], "page provenance format", minimum=1, maximum=1)
    location = _fields(data["reference"], ("browser", "binding_id", "target_id", "url"))
    reference = PageReference(BrowserKind(text(location["browser"], "browser source", maximum=32)),
                              identifier(location["binding_id"]), identifier(location["target_id"]), _url(location["url"]))
    shape = _object(data["scope"])
    scope: Scope
    if shape.get("kind") == "complete_main_document":
        _fields(shape, ("kind",))
        scope = Complete.MAIN_DOCUMENT
    elif shape.get("kind") == "excerpt":
        _fields(shape, ("kind", "start_utf16", "end_utf16", "total_utf16"))
        scope = Excerpt(integer(shape["start_utf16"], "excerpt start"), integer(shape["end_utf16"], "excerpt end"),
                        integer(shape["total_utf16"], "document length"))
    else:
        raise ValueError("The saved page text scope is unknown.")
    return PageProvenance(reference, scope, digest(data["version"]))


@dataclass(frozen=True, slots=True)
class ObservedPage:
    reference: PageReference
    title: str
    text: str
    scope: Scope
    observed_at: float
    version: str = field(init=False)

    def __post_init__(self) -> None:
        variant(self.reference, PageReference, "page reference")
        text(self.title, "page title", maximum=4_096, empty=True)
        self.title.encode("utf-8")
        text(self.text, "page text", maximum=MAX_TEXT, empty=True)
        variant(self.scope, (Complete, Excerpt), "page text scope")
        object.__setattr__(self, "observed_at", timestamp(self.observed_at, "page observation time"))
        object.__setattr__(self, "version", _version(self.reference, self.text, self.scope))

    @property
    def provenance(self) -> PageProvenance:
        return PageProvenance(self.reference, self.scope, self.version)


@dataclass(frozen=True, slots=True)
class Unavailable:
    reason: str

    def __post_init__(self) -> None:
        text(self.reason, "page availability reason", maximum=2_000)


@dataclass(frozen=True, slots=True)
class RebindRequired:
    reference: PageReference
    reason: str

    def __post_init__(self) -> None:
        variant(self.reference, PageReference, "page reference")
        text(self.reason, "page reconnection reason", maximum=2_000)


class Cancelled(Enum):
    READ = auto()


type ReadResult = ObservedPage | Unavailable | RebindRequired | Cancelled


def observation_of(reference: PageReference, value: object, at: float) -> ReadResult:
    """Validate one atomic main-document observation at the browser boundary."""
    data = _object(value)
    if data.get("kind") == "loading":
        return Unavailable("The selected page is still loading. Its previous observation was kept.")
    if data.get("kind") == "no_body":
        return Unavailable("The selected page has no readable main document. Its previous observation was kept.")
    if data.get("kind") != "source":
        raise ValueError("The browser returned an unknown page observation.")
    url = _url(data.get("url"))
    if url != reference.url:
        return RebindRequired(reference, "The original tab navigated to another page. Select the intended page again.")
    content = text(data.get("text"), "observed page text", maximum=MAX_TEXT, empty=True)
    end = integer(data.get("end_utf16"), "observed text end", maximum=MAX_TEXT)
    total = integer(data.get("total_utf16"), "observed document length", maximum=MAX_DOCUMENT_UNITS)
    if end != _units(content) or end > total:
        raise ValueError("The browser returned inconsistent text coverage.")
    scope = Complete.MAIN_DOCUMENT if end == total else Excerpt(0, end, total)
    return ObservedPage(reference, text(data.get("title"), "observed page title", maximum=4_096, empty=True), content, scope, at)
