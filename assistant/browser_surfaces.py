"""Own isolated browser pages, profiles, native clipping and guarded DOM reads.

Every public method runs on Qt's thread. Immutable specs describe saved apps;
repainting or hiding an app never replaces its live page. No remote page gets
the shell's WebChannel, profile, or a browser debugging endpoint.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# mypy: disallow-any-expr=False

import base64
import hashlib
import json
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import assert_never, cast

from automation.task import identifier, integer, text, variant

from . import browser_dom
from . import browser_surface_operations as ops
from . import browser_surface_values as v
from .llm import Json
from .page_source_values import MAX_DOCUMENT_UNITS, MAX_TEXT, Complete, Excerpt
from .qt import Qt, QtCore, QtGui, QtWidgets, shiboken6

# Resolve the supported Qt/Python initialization order before WebEngine imports.
# isort: split
from PySide6 import QtWebEngineCore, QtWebEngineWidgets

MAX_PAGES = 32
MAX_READS = 32
WORLD = QtWebEngineCore.QWebEngineScript.ScriptWorldId.ApplicationWorld.value

_DOCUMENT = """Object.defineProperty(globalThis, '__toolkitDocument', {value:
    Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('')});"""
_STATUS = """JSON.stringify({document:globalThis.__toolkitDocument, url:location.href,
    title:document.title, ready:document.readyState !== 'loading'})"""
_SOURCE = """(() => {
    if (!document.body) throw new Error('This page has no readable main document.');
    const text = document.body.innerText;
    let end = Math.min(text.length, 64000);
    const last = end > 0 ? text.charCodeAt(end - 1) : 0;
    if (end < text.length && last >= 0xD800 && last <= 0xDBFF) end--;
    return {url:location.href, title:document.title.slice(0,4096), text:text.slice(0,end),
            end_utf16:end, total_utf16:text.length};
})()"""


def _object(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("The browser returned an invalid document observation.")
    return value


def _wire(value: object) -> dict[str, Json]:
    data = text(value, "browser reply", maximum=500_000)
    return _object(cast(Json, json.loads(data)))


def _guard(document: str, url: str, expression: str) -> str:
    """Only host-owned expressions enter this isolated document world."""
    return f"""JSON.stringify((() => {{
        if (globalThis.__toolkitDocument !== {json.dumps(document)} || location.href !== {json.dumps(url)} || document.readyState === 'loading')
            return {{kind:'stale'}};
        try {{ return {{kind:'ok', value:{expression}}}; }}
        catch (error) {{ return {{kind:'error', reason:String(error.message || error).slice(0,2000)}}; }}
    }})())"""


def _rect(rect: v.CssRect) -> QtCore.QRect:
    left, top = math.floor(rect.x), math.floor(rect.y)
    return QtCore.QRect(left, top, math.ceil(rect.right) - left, math.ceil(rect.bottom) - top)


def _permission_purpose(permission: QtWebEngineCore.QWebEnginePermission.PermissionType) -> str | None:
    match permission:
        case QtWebEngineCore.QWebEnginePermission.PermissionType.MediaAudioCapture:
            return "use your microphone"
        case QtWebEngineCore.QWebEnginePermission.PermissionType.MediaVideoCapture:
            return "use your camera"
        case QtWebEngineCore.QWebEnginePermission.PermissionType.MediaAudioVideoCapture:
            return "use your microphone and camera"
        case QtWebEngineCore.QWebEnginePermission.PermissionType.MouseLock:
            return "lock the pointer to this page"
        case QtWebEngineCore.QWebEnginePermission.PermissionType.Notifications:
            return "show notifications"
        case QtWebEngineCore.QWebEnginePermission.PermissionType.Geolocation:
            return "use your location"
        case QtWebEngineCore.QWebEnginePermission.PermissionType.ClipboardReadWrite:
            return "read and change the clipboard"
        case QtWebEngineCore.QWebEnginePermission.PermissionType.LocalFontsAccess:
            return "use fonts installed on this PC"
        case _:
            # Desktop capture needs a separate screen chooser. Unknown future
            # capabilities are denied until their actual access can be shown.
            return None


class _Page(QtWebEngineCore.QWebEnginePage):
    def __init__(
        self, profile: QtWebEngineCore.QWebEngineProfile, parent: QtWidgets.QWidget,
        confirm: Callable[[str, str], bool],
    ) -> None:
        super().__init__(profile, parent)
        self.confirm = confirm

    def acceptNavigationRequest(  # noqa: N802
        self, url: QtCore.QUrl | str, _kind: QtWebEngineCore.QWebEnginePage.NavigationType, _main: bool,
    ) -> bool:
        destination = QtCore.QUrl(url)
        return destination.scheme() in ("http", "https", "blob") or destination.toString() == "about:blank"

    def javaScriptConfirm(self, security_origin: QtCore.QUrl | str, msg: str) -> bool:  # noqa: N802
        return self.confirm(QtCore.QUrl(security_origin).toString(), msg)


class _View(QtWebEngineWidgets.QWebEngineView):
    def __init__(self, parent: QtWidgets.QWidget, requested_close: Callable[[], None]) -> None:
        super().__init__(parent)
        self.requested_close = requested_close

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        event.ignore()
        self.requested_close()


@dataclass(slots=True)
class _Surface:
    """Mutable native lifetime, kept outside the immutable browser observations."""

    spec: v.SurfaceSpec
    view: QtWebEngineWidgets.QWebEngineView
    page: _Page
    instance: str
    generation: str
    document: str
    observation: v.BrowserObservation
    opener: str | None = None


@dataclass(frozen=True, slots=True)
class _Capture:
    target: v.BrowserTarget
    url: str
    document: str
    deadline: float
    finished: Callable[[v.CaptureResult], None]


@dataclass(frozen=True, slots=True)
class _Retired:
    spec: v.SurfaceSpec
    observation: v.BrowserObservation


@dataclass(frozen=True, slots=True)
class _Close:
    item_id: str
    instance_id: str
    request_id: str
    deadline: float
    finished: Callable[[v.CloseResult], None]


@dataclass(frozen=True, slots=True)
class _PageRef:
    item_id: str
    instance_id: str


class _PermissionChoice(Enum):
    ALLOWED = auto()
    DENIED = auto()
    RETIRED = auto()


@dataclass(frozen=True, slots=True)
class _Question:
    page: _PageRef
    generation: str
    url: str
    dialog: QtWidgets.QMessageBox


@dataclass(frozen=True, slots=True)
class _CloseGroup:
    root: _PageRef
    request_id: str
    remaining: tuple[_PageRef, ...]
    deadline: float
    finished: Callable[[v.CloseResult], None]


@dataclass(frozen=True, slots=True)
class _Document:
    target: v.BrowserTarget
    url: str
    token: str


class _Queued(Enum):
    OPERATION = auto()


@dataclass(frozen=True, slots=True)
class _Reading:
    document: _Document


@dataclass(frozen=True, slots=True)
class _Acting:
    document: _Document


@dataclass(frozen=True, slots=True)
class _Navigating:
    item_id: str
    instance_id: str
    url: str


type _OperationState = _Queued | _Reading | _Acting | _Navigating


@dataclass(frozen=True, slots=True)
class _Operation:
    operation: ops.AppOperation
    cancel: threading.Event
    finished: Callable[[ops.AppResult], None]
    deadline: float
    state: _OperationState = _Queued.OPERATION


def _app_row(entry: v.BrowserObservation) -> dict[str, Json]:
    target_id: str | None = None
    match entry.state:
        case v.Ready(target):
            state = "ready"
            target_id = ops.app_target_id(target.item_id, target.instance_id, target.document_id)
        case v.Loading():
            state = "loading"
        case v.Failed():
            state = "failed"
        case v.Crashed():
            state = "crashed"
        case v.Dormant.SAVED:
            state = "saved"
        case v.Dormant.CLOSED:
            state = "closed"
        case _:
            assert_never(entry.state)
    return {"item_id": entry.item_id, "target_id": target_id, "url": entry.url, "title": entry.title, "state": state}


def _success(data: dict[str, Json]) -> ops.AppSucceeded:
    return ops.AppSucceeded(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def _listed(snapshot: v.BrowserSnapshot, offset: int, limit: int) -> ops.AppSucceeded:
    end = min(offset + limit, len(snapshot.entries))
    return _success({"apps": [_app_row(entry) for entry in snapshot.entries[offset:end]], "offset": offset,
                     "total": len(snapshot.entries), "next_offset": end if end < len(snapshot.entries) else None})


def _stopped(state: _OperationState, *, cancelled: bool) -> ops.AppResult:
    match state:
        case _Acting() | _Navigating():
            return ops.AppFailed("The browser operation may have started, but its outcome is unconfirmed. Inspect the current page before retrying.")
        case _Queued.OPERATION | _Reading():
            return ops.AppCancelled.REQUEST if cancelled else ops.AppFailed("The browser operation did not finish before its deadline.")
        case _:
            assert_never(state)


class BrowserSurfaces:
    """Qt-thread effect owner; ``changed`` is always deferred to the event loop.

    A fresh Suspended frame establishes a new workspace/overlay epoch before
    HTML layout arrives. Subsequent visible frames must match that epoch.
    ``capture`` reports source text only for the requested ready document.
    """

    __slots__ = (
        "_captures", "_close_groups", "_closes", "_downloads", "_frame", "_notify", "_operations",
        "_profiles", "_question", "_retired", "_surfaces", "_timer",
        "changed", "closed", "parent", "profile_root",
    )

    def __init__(self, parent: QtWidgets.QWidget, profile_root: Path, changed: Callable[[], None]) -> None:
        self.parent, self.profile_root, self.changed = parent, profile_root.absolute(), changed
        self.closed = False
        self._surfaces: dict[str, _Surface] = {}
        self._profiles: dict[str, QtWebEngineCore.QWebEngineProfile] = {}
        self._question: _Question | None = None
        self._captures: dict[str, _Capture] = {}
        self._closes: dict[str, _Close] = {}
        self._close_groups: dict[str, _CloseGroup] = {}
        self._downloads: dict[str, QtWebEngineCore.QWebEngineDownloadRequest] = {}
        self._retired: dict[str, _Retired] = {}
        self._operations: dict[str, _Operation] = {}
        self._frame: v.ViewportFrame | None = None
        self._notify = QtCore.QTimer(parent)
        self._notify.setSingleShot(True)
        self._notify.timeout.connect(self._changed)
        self._timer = QtCore.QTimer(parent)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._expire)
        self._timer.start()

    def _changed(self) -> None:
        if not self.closed:
            self.changed()

    def _announce(self) -> None:
        if not self.closed and not self._notify.isActive():
            self._notify.start(0)

    def _profile(self, profile_id: str) -> QtWebEngineCore.QWebEngineProfile:
        existing = self._profiles.get(profile_id)
        if existing is not None:
            return existing
        key = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()
        folder = self.profile_root / ("profile-" + key)
        folder.mkdir(parents=True, exist_ok=True)
        profile = QtWebEngineCore.QWebEngineProfile("toolkit-app-" + key, self.parent)
        profile.setPersistentStoragePath(str(folder / "storage"))
        profile.setCachePath(str(folder / "cache"))
        profile.setPersistentCookiesPolicy(QtWebEngineCore.QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        profile.settings().setAttribute(QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        profile.settings().setAttribute(QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        profile.downloadRequested.connect(self._download)
        self._profiles[profile_id] = profile
        return profile

    def _download(self, download: QtWebEngineCore.QWebEngineDownloadRequest) -> None:
        if self.closed or len(self._downloads) >= 8 or not any(surface.page is download.page() for surface in self._surfaces.values()):
            download.cancel()
            return
        identity = uuid.uuid4().hex
        self._downloads[identity] = download
        directory = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.DownloadLocation)
        suggested = Path(directory) / (Path(download.suggestedFileName()).name or "download")
        # Qt requires acceptance before downloadRequested returns. The native
        # save dialog supplies the destination before any download is accepted.
        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(self.parent, "Save browser download", str(suggested))
        if not shiboken6.isValid(download):
            self._downloads.pop(identity, None)
            return
        if self.closed or not chosen:
            self._downloads.pop(identity, None)
            download.cancel()
            download.deleteLater()
            return
        destination = Path(chosen)
        download.setDownloadDirectory(str(destination.parent))
        download.setDownloadFileName(destination.name)
        download.isFinishedChanged.connect(partial(self._download_finished, identity))
        download.accept()

    def _download_finished(self, identity: str) -> None:
        download = self._downloads.get(identity)
        if download is not None and download.isFinished():
            del self._downloads[identity]
            download.deleteLater()

    def _permission(self, surface: _Surface, permission: QtWebEngineCore.QWebEnginePermission) -> None:
        if not permission.isValid():
            return
        purpose = _permission_purpose(permission.permissionType())
        if not self._live(surface) or purpose is None:
            permission.deny()
            return
        choice = self._ask_permission(surface, permission.origin(), purpose + "?")
        if not permission.isValid():
            return
        match choice:
            case _PermissionChoice.ALLOWED:
                permission.grant()
            case _PermissionChoice.DENIED:
                permission.deny()
            case _PermissionChoice.RETIRED:
                # Permission objects can persist per origin. An answer for a
                # retired document must not change that origin's stored choice.
                return
            case _:
                assert_never(choice)

    def _file_access(self, surface: _Surface, request: QtWebEngineCore.QWebEngineFileSystemAccessRequest) -> None:
        flags = request.accessFlags()
        read = QtWebEngineCore.QWebEngineFileSystemAccessRequest.AccessFlag.Read
        write = QtWebEngineCore.QWebEngineFileSystemAccessRequest.AccessFlag.Write
        if not self._live(surface) or not request.filePath().isLocalFile() or flags not in (read, write, read | write):
            request.reject()
            return
        access = "read and change" if flags == read | write else "change" if flags == write else "read"
        handle = request.handleType()
        match handle:
            case QtWebEngineCore.QWebEngineFileSystemAccessRequest.HandleType.File:
                kind = "file"
            case QtWebEngineCore.QWebEngineFileSystemAccessRequest.HandleType.Directory:
                kind = "folder and its contents"
            case _:
                assert_never(handle)
        chosen = str(Path(request.filePath().toLocalFile()))
        choice = self._ask_permission(surface, request.origin(), f"{access} this {kind}?\n\n{chosen}")
        if shiboken6.isValid(request):
            if choice is _PermissionChoice.ALLOWED:
                request.accept()
            else:
                request.reject()

    def _ask_permission(self, surface: _Surface, origin: QtCore.QUrl, purpose: str) -> _PermissionChoice:
        if not self._live(surface) or not shiboken6.isValid(surface.page):
            return _PermissionChoice.RETIRED
        if self._question is not None or origin.scheme() not in ("http", "https") or not origin.host():
            return _PermissionChoice.DENIED
        dialog = QtWidgets.QMessageBox(self.parent)
        dialog.setWindowTitle("Browser site permission")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Allow {origin.toString()} to {purpose}")
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        dialog.setEscapeButton(QtWidgets.QMessageBox.StandardButton.No)
        question = _Question(_PageRef(surface.spec.item_id, surface.instance), surface.generation, surface.page.url().toString(), dialog)
        self._question = question
        answer = dialog.exec()
        current = self._question is question
        if current:
            self._question = None
        if shiboken6.isValid(dialog):
            dialog.deleteLater()
        if (not current or not self._live(surface) or question.generation != surface.generation
                or not shiboken6.isValid(surface.page) or surface.page.url().toString() != question.url):
            return _PermissionChoice.RETIRED
        return _PermissionChoice.ALLOWED if answer == QtWidgets.QMessageBox.StandardButton.Yes else _PermissionChoice.DENIED

    def _retire_question(self, surface: _Surface | None = None) -> None:
        question = self._question
        if question is None or (surface is not None and question.page != _PageRef(surface.spec.item_id, surface.instance)):
            return
        self._question = None
        if shiboken6.isValid(question.dialog):
            question.dialog.reject()

    def sync(self, specs: tuple[v.SurfaceSpec, ...]) -> None:
        variant(specs, tuple, "browser app specifications")
        for spec in specs:
            variant(spec, v.SurfaceSpec, "browser app specification")
        identities = {spec.item_id for spec in specs}
        if len(specs) > MAX_PAGES or len(identities) != len(specs):
            raise ValueError("Choose at most 32 different live browser cards.")
        if self.closed:
            return
        for item_id in tuple(self._retired):
            if item_id not in identities:
                del self._retired[item_id]
        for item_id, existing in tuple(self._surfaces.items()):
            if existing.opener is None and item_id not in identities:
                self._drop(item_id)
        for spec in specs:
            retired = self._retired.get(spec.item_id)
            if retired is not None:
                if retired.spec == spec:
                    continue
                del self._retired[spec.item_id]
            surface = self._surfaces.get(spec.item_id)
            if surface is not None and surface.spec.app.profile_id != spec.app.profile_id:
                self._drop(spec.item_id)
                surface = None
            if surface is None:
                self._create(spec)
            elif surface.spec != spec:
                surface.spec = spec
                if surface.page.url().toString() != spec.app.url:
                    self.dispatch(v.Navigate(spec.item_id, spec.app.url))
        self._announce()

    def _create(self, spec: v.SurfaceSpec, *, opener: str | None = None, load: bool = True) -> _Surface:
        if len(self._surfaces) >= MAX_PAGES:
            raise ValueError("Close a browser card or popup before opening another page.")
        view = _View(self.parent, partial(self._view_close, spec.item_id))
        view.hide()
        if opener is not None:
            view.setWindowFlags(Qt.WindowType.Window)
        page = _Page(self._profile(spec.app.profile_id), view, partial(self._confirm, spec.item_id))
        view.setPage(page)
        marker = QtWebEngineCore.QWebEngineScript()
        marker.setName("Toolkit isolated document identity")
        marker.setInjectionPoint(QtWebEngineCore.QWebEngineScript.InjectionPoint.DocumentCreation)
        marker.setWorldId(WORLD)
        marker.setRunsOnSubFrames(False)
        marker.setSourceCode(_DOCUMENT)
        page.scripts().insert(marker)
        instance = uuid.uuid4().hex
        surface = _Surface(spec, view, page, instance, uuid.uuid4().hex, "",
                           v.BrowserObservation(spec.item_id, spec.app.url, "", v.Loading(instance)), opener)
        self._surfaces[spec.item_id] = surface
        page.loadStarted.connect(partial(self._loading, surface))
        page.loadFinished.connect(partial(self._loaded, surface))
        page.urlChanged.connect(partial(self._address, surface))
        page.titleChanged.connect(partial(self._title, surface))
        page.renderProcessTerminated.connect(partial(self._crashed, surface))
        page.windowCloseRequested.connect(partial(self._closed_window, surface))
        page.newWindowRequested.connect(partial(self._popup, surface))
        page.permissionRequested.connect(partial(self._permission, surface))
        page.fileSystemAccessRequested.connect(partial(self._file_access, surface))
        if opener is not None:
            page.geometryChangeRequested.connect(partial(self._popup_geometry, surface))
        if load:
            page.load(QtCore.QUrl(spec.app.url))
        return surface

    def _view_close(self, item_id: str) -> None:
        self.request_close(item_id, lambda _: None)

    def _popup(self, opener: _Surface, request: QtWebEngineCore.QWebEngineNewWindowRequest) -> None:
        if not self._live(opener) or not request.isUserInitiated() or len(self._surfaces) + len(self._retired) >= MAX_PAGES:
            return
        try:
            url = ops.http_url(request.requestedUrl().toString())
        except ValueError:
            url = opener.spec.app.url
        spec = v.SurfaceSpec("popup-" + uuid.uuid4().hex, replace(opener.spec.app, url=url))
        popup = self._create(spec, opener=opener.spec.item_id, load=False)
        popup.view.setWindowTitle("Browser popup")
        self._popup_geometry(popup, request.requestedGeometry())
        # openIn retains Chromium's opener relationship and the selected profile.
        request.openIn(popup.page)
        if opener.view.isVisible():
            popup.view.show()
        self._announce()

    def _popup_geometry(self, surface: _Surface, requested: QtCore.QRect) -> None:
        if not self._live(surface):
            return
        area = self.parent.screen().availableGeometry()
        width = min(max(requested.width() or 560, 320), area.width())
        height = min(max(requested.height() or 440, 240), area.height())
        left = requested.x() if requested.x() else area.center().x() - width // 2
        top = requested.y() if requested.y() else area.center().y() - height // 2
        surface.view.setGeometry(max(area.x(), min(left, area.right() - width + 1)),
                                 max(area.y(), min(top, area.bottom() - height + 1)), width, height)

    def _live(self, surface: _Surface) -> bool:
        return not self.closed and self._surfaces.get(surface.spec.item_id) is surface

    def _loading(self, surface: _Surface) -> None:
        if self._live(surface):
            surface.generation = uuid.uuid4().hex
            surface.document = ""
            surface.observation = replace(surface.observation, state=v.Loading(surface.instance))
            self._retire_question(surface)
            self._announce()

    def _loaded(self, surface: _Surface, success: bool) -> None:
        if not self._live(surface) or surface.page.isLoading():
            return
        if success:
            self._observe(surface)
        else:
            surface.observation = replace(surface.observation, state=v.Failed(surface.instance, "The browser could not load this page."))
            self._resolve_navigation(surface)
            self._announce()

    def _address(self, surface: _Surface, _url: QtCore.QUrl) -> None:
        if not self._live(surface):
            return
        url = surface.page.url().toString()
        try:
            text(url, "browser address", maximum=8_192, empty=True)
            url.encode("utf-8")
        except (ValueError, UnicodeError):
            self._loading(surface)
            surface.observation = replace(surface.observation, url="", state=v.Failed(surface.instance, "The browser address is unsupported."))
            self._resolve_navigation(surface)
            return
        ready = isinstance(surface.observation.state, v.Ready)
        surface.observation = replace(surface.observation, url=url)
        if ready:
            self._loading(surface)
            self._observe(surface)
        self._announce()

    def _title(self, surface: _Surface, title: str) -> None:
        if self._live(surface):
            displayed = title[:1_024].replace("\0", "\ufffd").encode("utf-8", errors="replace").decode("utf-8")
            surface.observation = replace(surface.observation, title=displayed)
            if surface.opener is not None:
                surface.view.setWindowTitle(displayed or "Browser popup")
            self._announce()

    def _observe(self, surface: _Surface) -> None:
        generation = surface.generation
        surface.page.runJavaScript(_STATUS, WORLD, partial(self._observed, surface, generation))

    def _observed(self, surface: _Surface, generation: str, value: object) -> None:
        if not self._live(surface) or generation != surface.generation or surface.page.isLoading():
            return
        try:
            data = _wire(value)
            if data.get("ready") is not True:
                return
            document = identifier(data.get("document"), "browser document token")
            url = text(data.get("url"), "browser address", maximum=8_192)
            if url != surface.page.url().toString():
                return
            title = text(data.get("title"), "browser title", maximum=4_096, empty=True)
            target = v.BrowserTarget(surface.spec.item_id, surface.instance, generation)
            surface.document = document
            surface.observation = v.BrowserObservation(surface.spec.item_id, url, title[:1_024], v.Ready(target),
                                                        surface.page.history().canGoBack(), surface.page.history().canGoForward())
        except (ValueError, UnicodeError):
            surface.observation = replace(surface.observation, state=v.Failed(surface.instance, "The browser document could not be identified."))
        self._resolve_navigation(surface)
        self._announce()

    def _crashed(self, surface: _Surface, status: QtWebEngineCore.QWebEnginePage.RenderProcessTerminationStatus, code: int) -> None:
        if self._live(surface):
            surface.document = ""
            state = v.Crashed(surface.instance, f"The browser renderer stopped ({status.name}, {code}).")
            surface.observation = replace(surface.observation, state=state)
            self._retire_question(surface)
            self._resolve_navigation(surface)
            self._announce()

    def snapshot(self) -> v.BrowserSnapshot:
        return v.BrowserSnapshot((*tuple(surface.observation for surface in self._surfaces.values()),
                                  *tuple(retired.observation for retired in self._retired.values())))

    def previews(self) -> tuple[tuple[str, str], ...]:
        """Presentation-only visible pixels: at most 12 PNGs/3 MiB, never persisted."""
        if self.closed:
            return ()
        previews: list[tuple[str, str]] = []
        used = 0
        for item_id, surface in self._surfaces.items():
            if surface.opener is not None or not surface.view.isVisible():
                continue
            image = surface.view.grab()
            if image.isNull():
                continue
            if max(image.width(), image.height()) > 720:
                image = image.scaled(720, 720, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            buffer = QtCore.QBuffer()
            buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                buffer.close()
                continue
            encoded = "data:image/png;base64," + base64.b64encode(buffer.data().data()).decode("ascii")
            buffer.close()
            if used + len(encoded) > 3 * 1024 * 1024:
                break
            used += len(encoded)
            previews.append((item_id, encoded))
            if len(previews) == 12:
                break
        return tuple(previews)

    def present(self, frame: v.ViewportFrame) -> None:
        variant(frame, v.ViewportFrame, "browser viewport frame")
        if self.closed:
            return
        previous = self._frame
        if (previous is not None and not (isinstance(frame.mode, v.Suspended) and frame.epoch != previous.epoch)
                and not v.accepts_frame(frame, space_id=previous.space_id, epoch=previous.epoch,
                                        after_sequence=previous.sequence, item_ids=frozenset(self._surfaces))):
            return
        self._frame = frame
        visible: set[str] = set()
        if isinstance(frame.mode, v.Visible):
            for entry in sorted(frame.mode.entries, key=lambda value: value.z):
                surface = self._surfaces.get(entry.item_id)
                if surface is None:
                    continue
                geometry = _rect(entry.rect)
                region = QtGui.QRegion()
                for clip in entry.clips:
                    left, top = math.ceil(clip.x), math.ceil(clip.y)
                    region |= QtGui.QRegion(QtCore.QRect(left - geometry.x(), top - geometry.y(),
                                                        math.floor(clip.right) - left, math.floor(clip.bottom) - top))
                if region.isEmpty():
                    continue
                surface.view.setGeometry(geometry)
                surface.view.setMask(region)
                surface.view.setZoomFactor(entry.zoom)
                surface.view.show()
                surface.view.raise_()
                visible.add(entry.item_id)
        for item_id, surface in self._surfaces.items():
            if surface.opener is not None and surface.opener in visible:
                visible.add(item_id)
                if not surface.view.isVisible():
                    surface.view.show()
            elif item_id not in visible:
                surface.view.hide()

    def dispatch(self, command: v.SurfaceCommand) -> None:
        if self.closed:
            return
        surface = self._surfaces.get(command.item_id)
        if surface is None:
            retired = self._retired.get(command.item_id)
            if retired is None or not isinstance(command, (v.Reload, v.Navigate)):
                return
            del self._retired[command.item_id]
            self._create(retired.spec)
            surface = self._surfaces[command.item_id]
            if isinstance(command, v.Reload):
                return
        match command:
            case v.Navigate(url=url):
                self._loading(surface)
                surface.page.load(QtCore.QUrl(url))
            case v.Back():
                if surface.page.history().canGoBack():
                    self._loading(surface)
                    surface.page.triggerAction(QtWebEngineCore.QWebEnginePage.WebAction.Back)
            case v.Forward():
                if surface.page.history().canGoForward():
                    self._loading(surface)
                    surface.page.triggerAction(QtWebEngineCore.QWebEnginePage.WebAction.Forward)
            case v.Reload():
                self._loading(surface)
                surface.page.triggerAction(QtWebEngineCore.QWebEnginePage.WebAction.Reload)
            case v.Remove():
                self.request_close(command.item_id, lambda _: None)
            case v.Focus():
                if surface.view.isVisible():
                    surface.view.setFocus(Qt.FocusReason.OtherFocusReason)
            case _:
                assert_never(command)

    def request_close(self, item_id: str, finished: Callable[[v.CloseResult], None]) -> str:
        """Ask descendants before their parent; any refusal retires this attempt."""
        identifier(item_id, "browser close item")
        request_id = uuid.uuid4().hex
        if not self.closed and item_id in self._retired:
            QtCore.QTimer.singleShot(0, partial(finished, v.Closed(item_id, request_id)))
            return request_id
        surface = self._surfaces.get(item_id)
        family = self._family(item_id)
        busy = {ref.item_id for group in self._close_groups.values() for ref in self._family(group.root.item_id)}
        if self.closed or surface is None or any(ref.item_id in busy for ref in family):
            QtCore.QTimer.singleShot(0, partial(finished, v.CloseCancelled(item_id, request_id)))
            return request_id
        group = _CloseGroup(_PageRef(item_id, surface.instance), request_id, family, time.monotonic() + 120, finished)
        self._close_groups[request_id] = group
        QtCore.QTimer.singleShot(0, partial(self._next_close, request_id))
        return request_id

    def _family(self, item_id: str) -> tuple[_PageRef, ...]:
        surface = self._surfaces.get(item_id)
        if surface is None:
            return ()
        children = tuple(ref for child in self._surfaces.values() if child.opener == item_id for ref in self._family(child.spec.item_id))
        return (*children, _PageRef(item_id, surface.instance))

    def _finish_close_group(self, request_id: str, *, accepted: bool) -> None:
        group = self._close_groups.pop(request_id, None)
        if group is None:
            return
        for item_id, attempt in tuple(self._closes.items()):
            if attempt.request_id == request_id:
                del self._closes[item_id]
        result = v.Closed(group.root.item_id, request_id) if accepted else v.CloseCancelled(group.root.item_id, request_id)
        QtCore.QTimer.singleShot(0, partial(group.finished, result))

    def _next_close(self, request_id: str) -> None:
        group = self._close_groups.get(request_id)
        if group is None:
            return
        if not group.remaining:
            self._finish_close_group(request_id, accepted=True)
            return
        root = self._surfaces.get(group.root.item_id)
        if self.closed or root is None or root.instance != group.root.instance_id:
            self._finish_close_group(request_id, accepted=False)
            return
        target = group.remaining[0]
        surface = self._surfaces.get(target.item_id)
        if surface is None:
            self._close_groups[request_id] = replace(group, remaining=group.remaining[1:])
            QtCore.QTimer.singleShot(0, partial(self._next_close, request_id))
            return
        if surface.instance != target.instance_id:
            self._finish_close_group(request_id, accepted=False)
            return
        descendants = self._family(target.item_id)[:-1]
        if descendants:
            self._close_groups[request_id] = replace(group, remaining=(*descendants, *group.remaining))
            QtCore.QTimer.singleShot(0, partial(self._next_close, request_id))
            return
        if target.item_id in self._closes:
            return
        request = _Close(target.item_id, target.instance_id, request_id, group.deadline, partial(self._close_step, request_id))
        self._closes[target.item_id] = request
        self._request_close(request)

    def _close_step(self, request_id: str, result: v.CloseResult) -> None:
        group = self._close_groups.get(request_id)
        if group is None or not group.remaining or result.item_id != group.remaining[0].item_id or result.request_id != request_id:
            return
        if isinstance(result, v.CloseCancelled):
            self._finish_close_group(request_id, accepted=False)
            return
        remaining = group.remaining[1:]
        if not remaining:
            self._finish_close_group(request_id, accepted=True)
        else:
            self._close_groups[request_id] = replace(group, remaining=remaining)
            QtCore.QTimer.singleShot(0, partial(self._next_close, request_id))

    def _request_close(self, request: _Close) -> None:
        surface = self._surfaces.get(request.item_id)
        if self._closes.get(request.item_id) != request:
            return
        if self.closed or surface is None or surface.instance != request.instance_id:
            self._cancel_close(request)
            return
        surface.page.triggerAction(QtWebEngineCore.QWebEnginePage.WebAction.RequestClose)

    def _cancel_close(self, request: _Close) -> None:
        if self._closes.get(request.item_id) == request:
            del self._closes[request.item_id]
            QtCore.QTimer.singleShot(0, partial(request.finished, v.CloseCancelled(request.item_id, request.request_id)))

    def _confirm(self, item_id: str, origin: str, message: str) -> bool:
        surface = self._surfaces.get(item_id)
        if surface is None or self.closed:
            return False
        request = self._closes.get(item_id)
        dialog = QtWidgets.QMessageBox(self.parent)
        dialog.setWindowTitle("Close browser card?" if request is not None else "Browser page confirmation")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(origin[:1_024] + "\n\n" + message[:4_096])
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        dialog.setEscapeButton(QtWidgets.QMessageBox.StandardButton.No)
        accepted = dialog.exec() == QtWidgets.QMessageBox.StandardButton.Yes
        dialog.deleteLater()
        if not accepted and request is not None:
            self._cancel_close(request)
        return accepted and self._live(surface)

    def _closed_window(self, surface: _Surface) -> None:
        if not self._live(surface):
            return
        if len(self._family(surface.spec.item_id)) > 1:
            request = self._closes.pop(surface.spec.item_id, None)
            if request is None:
                self.request_close(surface.spec.item_id, lambda _: None)
            else:
                QtCore.QTimer.singleShot(0, partial(self._next_close, request.request_id))
            return
        request = self._closes.pop(surface.spec.item_id, None)
        self._drop(surface.spec.item_id, remember=surface.opener is None)
        if request is not None and request.instance_id == surface.instance:
            QtCore.QTimer.singleShot(0, partial(request.finished, v.Closed(request.item_id, request.request_id)))

    def capture(self, target: v.BrowserTarget, finished: Callable[[v.CaptureResult], None]) -> None:
        variant(target, v.BrowserTarget, "browser capture target")
        surface = self._surfaces.get(target.item_id)
        if self.closed or surface is None or surface.observation.target != target or len(self._captures) >= MAX_READS:
            failure = v.CaptureFailed(target, "This browser document is unavailable. Capture its current page again.")
            QtCore.QTimer.singleShot(0, partial(finished, failure))
            return
        identity = uuid.uuid4().hex
        request = _Capture(target, surface.observation.url, surface.document, time.monotonic() + 10, finished)
        self._captures[identity] = request
        surface.page.runJavaScript(_guard(request.document, request.url, _SOURCE), WORLD, partial(self._captured, identity))

    def control(self, operation: ops.AppOperation, cancel: threading.Event, finished: Callable[[ops.AppResult], None]) -> None:
        """Queue a typed operation; the cancellation Event fences every deferred step.

        Only explicit saved cards and their owned popups are registered. OpenApp
        is handled by the canvas owner before reaching this native boundary.
        No expression, profile path, Qt object or debugging handle is accepted.
        """
        if self.closed or cancel.is_set():
            QtCore.QTimer.singleShot(0, partial(finished, ops.AppCancelled.REQUEST))
            return
        if len(self._operations) >= 32:
            QtCore.QTimer.singleShot(0, partial(finished, ops.AppFailed("The browser operation queue is full.")))
            return
        identity = uuid.uuid4().hex
        self._operations[identity] = _Operation(operation, cancel, finished, time.monotonic() + 20)
        QtCore.QTimer.singleShot(0, partial(self._execute, identity))

    def _finish(self, identity: str, result: ops.AppResult) -> None:
        request = self._operations.pop(identity, None)
        if request is not None:
            request.finished(result)

    def _resolve(self, target_id: str) -> _Surface | None:
        for surface in self._surfaces.values():
            target = surface.observation.target
            if (target is not None and ops.app_target_id(target.item_id, target.instance_id, target.document_id) == target_id
                    and surface.page.url().toString() == surface.observation.url):
                return surface
        return None

    def _execute(self, identity: str) -> None:
        request = self._operations.get(identity)
        if request is None:
            return
        if self.closed or request.cancel.is_set():
            self._finish(identity, ops.AppCancelled.REQUEST)
            return
        operation = request.operation
        try:
            match operation:
                case ops.ListApps(offset, limit):
                    self._finish(identity, _listed(self.snapshot(), offset, limit))
                    return
                case ops.OpenApp():
                    self._finish(identity, ops.AppFailed("Create a saved browser card before requesting its native page."))
                    return
                case ops.NavigateApp() | ops.SnapshotApp() | ops.ClickApp() | ops.FillApp() | ops.SelectApp() | ops.CheckApp():
                    surface = self._resolve(operation.target_id)
                case _:
                    assert_never(operation)
            if surface is None or surface.observation.target is None:
                self._finish(identity, ops.AppFailed("This exact browser document is no longer available. List the current app pages again."))
                return
            document = _Document(surface.observation.target, surface.observation.url, surface.document)
            options: dict[str, Json]
            match operation:
                case ops.NavigateApp(url=url):
                    if request.cancel.is_set():
                        self._finish(identity, ops.AppCancelled.REQUEST)
                        return
                    state = _Navigating(surface.spec.item_id, surface.instance, QtCore.QUrl(url).toString())
                    self._operations[identity] = replace(request, state=state)
                    self.dispatch(v.Navigate(surface.spec.item_id, url))
                    return
                case ops.SnapshotApp(offset=offset, limit=limit, text_offset=text_offset):
                    options = {"offset": offset, "limit": limit, "text_offset": text_offset, "prefix": uuid.uuid4().hex}
                    expression = f"({browser_dom.SNAPSHOT})({json.dumps(options)})"
                    self._operations[identity] = replace(request, state=_Reading(document))
                case ops.ClickApp(control_id=control_id):
                    options = {"action": "click", "id": control_id, "dom_click": True}
                    expression = f"({browser_dom.ACTION})({json.dumps(options)})"
                    self._operations[identity] = replace(request, state=_Acting(document))
                case ops.FillApp(control_id=control_id, value=value) | ops.SelectApp(control_id=control_id, value=value):
                    options = {"action": "fill" if isinstance(operation, ops.FillApp) else "select", "id": control_id, "value": value}
                    expression = f"({browser_dom.ACTION})({json.dumps(options)})"
                    self._operations[identity] = replace(request, state=_Acting(document))
                case ops.CheckApp(control_id=control_id, checked=checked):
                    options = {"action": "check", "id": control_id, "checked": checked}
                    expression = f"({browser_dom.ACTION})({json.dumps(options)})"
                    self._operations[identity] = replace(request, state=_Acting(document))
                case _:
                    assert_never(operation)
            if request.cancel.is_set():
                self._finish(identity, ops.AppCancelled.REQUEST)
                return
            surface.page.runJavaScript(_guard(document.token, document.url, expression), WORLD, partial(self._controlled, identity))
        except (ValueError, UnicodeError):
            self._finish(identity, ops.AppFailed("The browser operation or response exceeded its supported bounds. Request a smaller page."))

    def _controlled(self, identity: str, value: object) -> None:
        request = self._operations.get(identity)
        if request is None:
            return
        state = request.state
        if self.closed or request.cancel.is_set():
            self._finish(identity, _stopped(state, cancelled=True))
            return
        if not isinstance(state, (_Reading, _Acting)):
            return
        document = state.document
        target = document.target
        target_id = ops.app_target_id(target.item_id, target.instance_id, target.document_id)
        surface = self._resolve(target_id)
        try:
            if surface is None or surface.document != document.token or surface.observation.url != document.url:
                raise ValueError("The browser document changed before the operation could be confirmed. Inspect its current page before retrying.")
            reply = _wire(value)
            if reply.get("kind") == "error":
                raise ValueError(text(reply.get("reason"), "browser operation error", maximum=2_000))
            if reply.get("kind") != "ok":
                raise ValueError("The requested browser document is stale. Inspect its current page before retrying.")
            data = _object(reply.get("value"))
            data.update(target_id=target_id, item_id=target.item_id)
            result: ops.AppResult = _success(data)
        except (ValueError, UnicodeError) as error:
            result = ops.AppFailed(str(error)[:2_000])
        self._finish(identity, result)

    def _resolve_navigation(self, surface: _Surface) -> None:
        for identity, request in tuple(self._operations.items()):
            state = request.state
            if not isinstance(state, _Navigating) or (state.item_id, state.instance_id) != (surface.spec.item_id, surface.instance):
                continue
            if request.cancel.is_set():
                self._finish(identity, _stopped(state, cancelled=True))
            elif isinstance(surface.observation.state, v.Ready):
                if surface.page.requestedUrl().toString() == state.url:
                    self._finish(identity, _success(_app_row(surface.observation)))
                else:
                    self._finish(identity, ops.AppFailed("The app navigated again before the requested destination could be confirmed."))
            elif isinstance(surface.observation.state, (v.Failed, v.Crashed)):
                self._finish(identity, ops.AppFailed(surface.observation.state.reason[:2_000]))

    def _captured(self, identity: str, value: object) -> None:
        request = self._captures.pop(identity, None)
        if request is None:
            return
        result: v.CaptureResult
        try:
            surface = self._surfaces.get(request.target.item_id)
            if (self.closed or surface is None or surface.observation.target != request.target or surface.document != request.document
                    or surface.page.url().toString() != request.url):
                raise ValueError("The browser navigated or closed before its source capture completed.")
            reply = _wire(value)
            if reply.get("kind") != "ok":
                raise ValueError("The requested browser document is no longer readable.")
            data = _object(reply.get("value"))
            url = text(data.get("url"), "captured page address", maximum=8_192)
            if url != request.url:
                raise ValueError("The browser returned a different page address.")
            content = text(data.get("text"), "captured page text", maximum=MAX_TEXT, empty=True)
            end = integer(data.get("end_utf16"), "captured text end", maximum=MAX_TEXT)
            total = integer(data.get("total_utf16"), "document text length", minimum=end, maximum=MAX_DOCUMENT_UNITS)
            if len(content.encode("utf-16-le")) // 2 != end:
                raise ValueError("The browser returned incomplete text coverage.")
            scope = Complete.MAIN_DOCUMENT if end == total else Excerpt(0, end, total)
            result = v.CapturedPage(request.target, text(data.get("title"), "captured page title", maximum=4_096, empty=True),
                                    url, content, time.time(), scope)
        except (ValueError, UnicodeError) as error:
            result = v.CaptureFailed(request.target, str(error))
        request.finished(result)

    def _expire(self) -> None:
        now = time.monotonic()
        for identity, request in tuple(self._captures.items()):
            if now >= request.deadline:
                del self._captures[identity]
                request.finished(v.CaptureFailed(request.target, "The browser source capture timed out."))
        for identity, operation in tuple(self._operations.items()):
            if operation.cancel.is_set() or now >= operation.deadline:
                self._finish(identity, _stopped(operation.state, cancelled=operation.cancel.is_set()))
        for closing in tuple(self._closes.values()):
            if now >= closing.deadline:
                self._cancel_close(closing)
        for request_id, group in tuple(self._close_groups.items()):
            if now >= group.deadline:
                self._finish_close_group(request_id, accepted=False)

    def _drop(self, item_id: str, *, remember: bool = False) -> None:
        for child_id, child in tuple(self._surfaces.items()):
            if child.opener == item_id:
                self._drop(child_id)
        if not remember:
            self._retired.pop(item_id, None)
        request = self._closes.get(item_id)
        if request is not None:
            self._cancel_close(request)
        surface = self._surfaces.pop(item_id, None)
        if surface is not None:
            self._retire_question(surface)
            if remember:
                self._retired[item_id] = _Retired(surface.spec, replace(surface.observation, state=v.Dormant.CLOSED))
            surface.view.hide()
            surface.view.deleteLater()
            self._announce()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._retire_question()
        self._notify.stop()
        self._timer.stop()
        pending = tuple(self._captures.values())
        self._captures.clear()
        for request in pending:
            request.finished(v.CaptureFailed(request.target, "Browser capture closed before completion."))
        for identity, operation in tuple(self._operations.items()):
            operation.cancel.set()
            self._finish(identity, _stopped(operation.state, cancelled=True))
        for request_id in tuple(self._close_groups):
            self._finish_close_group(request_id, accepted=False)
        downloads = tuple(self._downloads.values())
        self._downloads.clear()
        for download in downloads:
            if shiboken6.isValid(download):
                download.cancel()
        for item_id in tuple(self._surfaces):
            self._drop(item_id)
        self._retired.clear()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        for profile in self._profiles.values():
            shiboken6.delete(profile)
        self._profiles.clear()
