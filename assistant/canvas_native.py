"""Windows and document adapters for the canvas, called on its I/O worker."""

import csv
import io
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never
from urllib.parse import quote, urlencode

from automation.files import filesystem_path
from config import APPS
from usage import DIRECTORY
from winapi import desktop_shell, screen, windows
from winapi.shapes import Rect

from . import canvas_model as m
from .browser import Browser, Product
from .browser_values import Page, page_of
from .page_source_values import Cancelled, ObservedPage, PageReference, ReadResult, RebindRequired, Unavailable
from .page_sources import PageSources


@dataclass(frozen=True, slots=True)
class Application:
    name: str
    command: str
    exe: str


@dataclass(frozen=True, slots=True)
class Catalogue:
    windows: tuple[m.Item, ...]
    apps: tuple[Application, ...]
    pages: tuple[Page, ...] = ()


def catalogue() -> Catalogue:
    at = time.time()
    items = tuple(m.Item(f"window-{window.handle}-{pid}", window.title[:240] or window.exe,
                         m.Source(m.LiveWindow(window.handle, pid, window.exe, window.title), "", at))
                  for window in windows.windows() if (pid := windows.process_id(window.handle)) != os.getpid() and window.exe)
    return Catalogue(items[:120], tuple(Application(app.name, app.command, app.exe) for app in APPS))


def resolve(origin: m.LiveWindow | m.SavedWindow) -> int:
    if isinstance(origin, m.LiveWindow):
        if windows.exists(origin.handle) and windows.process_id(origin.handle) == origin.pid and windows.exe(origin.handle) == origin.app:
            return origin.handle
        raise OSError("This window has closed. Use the launcher to add its replacement.")
    matches = [window.handle for window in windows.windows() if window.exe == origin.app and window.title == origin.title]
    if len(matches) != 1:
        raise OSError("Choose the current window in the launcher to reconnect this saved reference.")
    return matches[0]


def file_item(path: Path, identity: str) -> m.Item:
    access = filesystem_path(path)
    if not access.is_file():
        raise OSError("This file is no longer available.")
    content = ""
    table: m.Table | None = None
    if path.suffix.lower() in (".md", ".txt", ".csv", ".tsv", ".log", ".json"):
        with access.open("rb") as source:
            raw = source.read(256_001)
        if len(raw) > 256_000:
            raise ValueError("Choose a text file smaller than 256 KB or add a selected excerpt.")
        content = raw.decode("utf-8-sig")
        m.bounded(content, "File content", empty=True)
        if path.suffix.lower() in (".csv", ".tsv"):
            rows = list(csv.reader(io.StringIO(content), delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
            if rows:
                table = m.Table(tuple(rows[0]), tuple(tuple(row) for row in rows[1:]))
    return m.Item(identity, path.name[:240], m.Source(m.FileSource(str(path.resolve())), content, time.time(), table))


def page_item(result: ReadResult, identity: str) -> m.Item:
    match result:
        case ObservedPage(reference, title, text, _scope, observed_at):
            return m.Item(identity, title[:240] or "Page excerpt",
                          m.Source(m.WebPage(reference.url, provenance=result.provenance), text, observed_at))
        case Unavailable(reason) | RebindRequired(reason=reason):
            raise OSError(reason)
        case Cancelled.READ:
            raise OSError("The page read was stopped. Its previous observation was kept.")
        case _:
            assert_never(result)


def page_reference(origin: m.WebPage) -> PageReference:
    if origin.provenance is None:
        raise OSError("This saved page has no live tab binding. Select its current tab in the launcher to reconnect.")
    return origin.provenance.reference


class NativeResources:
    __slots__ = ("browser", "cancel", "page_sources", "pages", "restores")

    def __init__(self, *, browser: Browser | None = None) -> None:
        self.browser = browser if browser is not None else Browser(DIRECTORY / "canvas-browser", source=Product.EDGE)
        self.cancel = threading.Event()
        self.page_sources = PageSources(self.browser)
        self.pages: tuple[Page, ...] = ()
        self.restores: dict[int, tuple[int, Rect, bool, bool]] = {}

    def connect_pages(self) -> tuple[Page, ...]:
        with self.browser.lock:
            self.browser.select(Product.EDGE)
            self.browser.connect(self.cancel)
            connection = self.browser.connection
            if connection is None:
                raise OSError("The Edge connection ended before its page list was available.")
            values = connection.call("Target.getTargets", {}, self.cancel).get("targetInfos")
            if not isinstance(values, list):
                raise ValueError("Edge did not return its page list.")
            self.pages = tuple(page_of(value) for value in values if isinstance(value, dict) and value.get("type") == "page")
            return self.pages

    def page(self, target: str, identity: str) -> m.Item:
        return page_item(self.page_sources.capture(target, self.cancel, at=time.time()), identity)

    def refresh(self, item: m.Item) -> m.Item:
        if not isinstance(item.content, m.Source):
            raise ValueError("Only application sources can be read again.")
        origin = item.content.origin
        if isinstance(origin, m.FileSource):
            return file_item(Path(origin.path), item.id)
        if isinstance(origin, (m.LiveWindow, m.SavedWindow)):
            hwnd = resolve(origin)
            return m.Item(item.id, windows.title(hwnd)[:240] or origin.app,
                          m.Source(m.LiveWindow(hwnd, windows.process_id(hwnd), origin.app, windows.title(hwnd)), "", time.time()))
        if isinstance(origin, m.WebPage):
            return page_item(self.page_sources.refresh(page_reference(origin), self.cancel, at=time.time()), item.id)
        raise ValueError("Clipboard excerpts are snapshots. Paste the updated selection as a new source.")

    def open(self, origin: m.Origin, identity: str = "") -> None:
        match origin:
            case m.LiveWindow() | m.SavedWindow():
                if not windows.focus(resolve(origin)):
                    raise OSError("The app could not take focus. It may be waiting for a dialog.")
            case m.WebPage():
                with self.browser.lock:
                    result = self.page_sources.resolve(page_reference(origin), self.cancel)
                    match result:
                        case PageReference(target_id=target):
                            connection = self.browser.connection
                            if connection is None:
                                raise OSError("The original browser connection ended. Select its current tab to reconnect.")
                            connection.call("Target.activateTarget", {"targetId": target}, self.cancel)
                        case Unavailable(reason) | RebindRequired(reason=reason):
                            raise OSError(reason)
                        case Cancelled.READ:
                            raise OSError("Opening the page was stopped.")
                        case _:
                            assert_never(result)
            case m.FileSource(path):
                if not filesystem_path(Path(path)).exists():
                    raise OSError("This file is no longer available.")
                os.startfile(path)
            case m.LocalSource.NOTE | m.LocalSource.CLIPBOARD:
                raise ValueError("This text is stored on the canvas.")

    def open_link(self, url: str) -> None:
        """Follow an explicitly clicked document link, separate from source open."""
        os.startfile(m.WebPage(url).url)

    def launch(self, app: Application) -> None:
        existing = next((window for window in windows.windows() if window.exe == app.exe), None)
        if existing is not None:
            if not windows.focus(existing.handle):
                raise OSError("The application could not take focus.")
        else:
            os.startfile(app.command)

    def arrange(self, items: tuple[m.Item, ...], canvas_hwnd: int) -> None:
        origins = [item.content.origin for item in items if isinstance(item.content, m.Source)
                   and isinstance(item.content.origin, (m.LiveWindow, m.SavedWindow))]
        if not 1 <= len(origins) <= 3:
            raise ValueError("Select one to three app windows to arrange beside the canvas.")
        handles = [resolve(origin) for origin in origins]
        work = screen.monitor_of(canvas_hwnd).work
        middle = work.left + work.width // 2
        for index, hwnd in enumerate(handles):
            self.restores.setdefault(hwnd, (windows.process_id(hwnd), windows.outer_rect(hwnd),
                                           windows.is_minimized(hwnd), windows.is_maximized(hwnd)))
            if windows.is_maximized(hwnd) or windows.is_minimized(hwnd):
                windows.show(hwnd, windows.SW_RESTORE)
            height = work.height // len(handles)
            windows.move(hwnd, Rect(work.left, work.top + index * height, middle, work.top + (index + 1) * height))
        self.restores.setdefault(canvas_hwnd, (os.getpid(), windows.outer_rect(canvas_hwnd), False, windows.is_maximized(canvas_hwnd)))
        windows.show(canvas_hwnd, windows.SW_RESTORE)
        windows.move(canvas_hwnd, Rect(middle, work.top, work.right, work.bottom))
        windows.focus(handles[0])

    def restore(self) -> None:
        for hwnd, (pid, bounds, minimized, maximized) in self.restores.items():
            if windows.exists(hwnd) and windows.process_id(hwnd) == pid:
                windows.move(hwnd, bounds)
                if minimized or maximized:
                    windows.show(hwnd, windows.SW_MINIMIZE if minimized else windows.SW_MAXIMIZE)
        self.restores.clear()

    def close(self) -> None:
        self.cancel.set()
        try:
            self.restore()
        finally:
            self.browser.close()


def mail_draft(title: str, text: str) -> None:
    target = "mailto:?" + urlencode({"subject": title, "body": text}, quote_via=quote)
    if len(target) > 7_000:
        raise ValueError("This draft is too long for a mail link. Copy it or export it, then paste it into your mail app.")
    os.startfile(target)


def start_guard(pid: int, handles: tuple[int, ...]) -> subprocess.Popen[bytes]:
    """Require a helper that survives termination of the assistant's whole job."""
    python = Path(sys.executable).with_name("python.exe")
    def launch(flags: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(python), "-m", "assistant.canvas_guard", str(pid), *(str(hwnd) for hwnd in handles)],
            cwd=Path(__file__).resolve().parent.parent, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    try:
        return launch(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except PermissionError as error:
        raise OSError("Windows could not start independent taskbar recovery. Explorer stays visible. "
                      "Open Toolkit directly from Windows to enable taskbar replacement.") from error


class TaskbarLease:
    __slots__ = ("_closed", "_lock", "guard", "hidden")

    def __init__(self) -> None:
        self.hidden: tuple[int, ...] = ()
        self.guard: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._lock = threading.Lock()

    def enable(self) -> None:
        with self._lock:
            if self._closed or self.guard is not None:
                return
        handles = tuple(hwnd for hwnd in desktop_shell.taskbars() if desktop_shell.visible(hwnd))
        if not handles:
            return
        guard = start_guard(os.getpid(), handles)
        with self._lock:
            cancelled = self.closed()
            if not cancelled:
                self.guard = guard
        if cancelled:
            self._stop_guard(guard)
            return
        ready = threading.Event()
        response: list[bytes] = []
        def read_ready() -> None:
            try:
                if guard.stdout is not None:
                    response.append(guard.stdout.readline(32))
            finally:
                ready.set()
        threading.Thread(target=read_ready, name="taskbar recovery startup", daemon=True).start()
        if not ready.wait(5) or response not in ([b"ready\r\n"], [b"ready\n"]):
            self.disable()
            raise OSError("Taskbar recovery could not start. Explorer's taskbar stays available.")
        with self._lock:
            if self._closed or self.guard is not guard:
                return
            exited = guard.poll() is not None
            if not exited:
                self.hidden = handles
                for hwnd in self.hidden:
                    desktop_shell.taskbar_visible(hwnd, show=False)
        if exited:
            self.disable()
            raise OSError("Taskbar recovery exited before it was ready. Explorer's taskbar stays available.")

    @staticmethod
    def _stop_guard(guard: subprocess.Popen[bytes]) -> None:
        if guard.poll() is None:
            guard.terminate()
        guard.wait(timeout=3)
        if guard.stdout is not None:
            guard.stdout.close()

    def closed(self) -> bool:
        # The UI can close the lease while its I/O worker starts the companion.
        return self._closed

    def disable(self) -> None:
        with self._lock:
            for hwnd in self.hidden:
                desktop_shell.taskbar_visible(hwnd, show=True)
            self.hidden = ()
            guard = self.guard
            self.guard = None
        if guard is not None:
            self._stop_guard(guard)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.disable()
