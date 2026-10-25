"""Versioned canvas storage. Save durable request IDs, never reusable window handles."""

import json
import os
import time
from pathlib import Path
from typing import assert_never, cast

from . import canvas_model as m
from .llm import Json
from .page_source_values import provenance_data, provenance_of


def object_of(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ValueError("Expected a canvas object.")
    return value


def string(data: dict[str, Json], key: str, maximum: int = m.MAX_TEXT, *, empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Canvas {key} must be text.")
    return m.bounded(value, key, maximum, empty=empty)


def number(data: dict[str, Json], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Canvas {key} must be a number.")
    return float(value)


def integer(data: dict[str, Json], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Canvas {key} must be a positive integer.")
    return value


def boolean(data: dict[str, Json], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Canvas {key} must be true or false.")
    return value


def array(data: dict[str, Json], key: str, limit: int = m.MAX_ITEMS) -> list[Json]:
    value = data.get(key)
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"Canvas {key} must be a list of at most {limit} values.")
    return value


def origin_data(origin: m.Origin) -> dict[str, Json]:
    match origin:
        case m.LiveWindow(app=app, title=title) | m.SavedWindow(app=app, title=title):
            return {"kind": "window", "app": app, "title": title}
        case m.WebPage(url, provenance=provenance):
            data: dict[str, Json] = {"kind": "web", "url": url}
            if provenance is not None:
                data["provenance"] = provenance_data(provenance)
            return data
        case m.FileSource(path):
            return {"kind": "file", "path": path}
        case m.LocalSource.NOTE:
            return {"kind": "note"}
        case m.LocalSource.CLIPBOARD:
            return {"kind": "clipboard"}
        case _:
            assert_never(origin)


def origin_of(value: Json) -> m.Origin:
    data = object_of(value)
    match data.get("kind"):
        case "window":
            return m.SavedWindow(string(data, "app", 512), string(data, "title", 4_096, empty=True))
        case "web":
            provenance = data.get("provenance")
            return m.WebPage(string(data, "url", 8_192), provenance=provenance_of(provenance) if provenance is not None else None)
        case "file":
            return m.FileSource(string(data, "path", 4_096))
        case "note":
            return m.LocalSource.NOTE
        case "clipboard":
            return m.LocalSource.CLIPBOARD
        case _:
            raise ValueError("Unknown canvas source.")


def evidence_data(source: m.Evidence) -> dict[str, Json]:
    return {"id": source.item_id, "revision": source.revision, "title": source.title, "text": source.text, "origin": origin_data(source.origin)}


def evidence_of(value: Json) -> m.Evidence:
    data = object_of(value)
    return m.Evidence(string(data, "id", 128), integer(data, "revision"), string(data, "title", 240),
                      string(data, "text", empty=True), origin_of(data.get("origin")))


def reference_data(reference: m.ResultReference) -> dict[str, Json]:
    match reference:
        case m.TaskReference(task_id):
            return {"kind": "task", "task_id": task_id}
        case m.NoTaskReference.NONE:
            return {"kind": "none"}
        case _:
            assert_never(reference)


def reference_of(value: Json) -> m.ResultReference:
    if value is None:
        return m.NoTaskReference.NONE
    data = object_of(value)
    match data.get("kind"):
        case "task" if set(data) == {"kind", "task_id"}:
            return m.TaskReference(string(data, "task_id", 128))
        case "none" if set(data) == {"kind"}:
            return m.NoTaskReference.NONE
        case _:
            raise ValueError("Unknown canvas task reference.")


def content_data(content: m.Content) -> dict[str, Json]:
    match content:
        case m.WebApp(url, profile_id):
            return {"kind": "web_app", "url": url, "profile_id": profile_id}
        case m.Source(origin, text, at, table):
            return {"kind": "source", "origin": origin_data(origin), "text": text, "at": at,
                    "table": {"columns": list(table.columns), "rows": [list(row) for row in table.rows]} if table is not None else None}
        case m.Note(text):
            return {"kind": "note", "text": text}
        case m.Draft(text, source):
            return {"kind": "draft", "text": text, "source": evidence_data(source)}
        case m.Result(prompt, text, used, state, steps, reference=reference):
            status = state.name.lower() if isinstance(state, m.Settled) else "failed" if isinstance(state, m.Failure) else "running"
            request = state.request if isinstance(state, m.Running) else None
            return {"kind": "result", "prompt": prompt, "text": text, "sources": [evidence_data(value) for value in used],
                    "status": status, "reason": state.reason if isinstance(state, m.Failure) else "", "steps": list(steps),
                    "reference": reference_data(reference),
                    "request": {"id": request.id, "space": request.space_id, "result": request.result_id, "prompt": request.prompt,
                                "sources": [evidence_data(source) for source in request.sources]} if request is not None else None}
        case _:
            assert_never(content)


def texts(values: list[Json]) -> tuple[str, ...]:
    if not all(isinstance(value, str) for value in values):
        raise ValueError("Expected a list of text values.")
    return tuple(m.bounded(value, "Text", empty=True) for value in cast(list[str], values))


def table_of(value: Json) -> m.Table | None:
    if value is None:
        return None
    data = object_of(value)
    columns = texts(array(data, "columns", 32))
    rows = array(data, "rows", 200)
    if not all(isinstance(row, list) for row in rows):
        raise ValueError("Expected table rows.")
    return m.Table(columns, tuple(texts(row) for row in cast(list[list[Json]], rows)))


def content_of(value: Json) -> m.Content:
    data = object_of(value)
    if data.get("kind") == "web_app":
        if data.keys() != {"kind", "url", "profile_id"}:
            raise ValueError("Saved browser cards contain only their URL and managed profile identity.")
        return m.WebApp(string(data, "url", 8_192), string(data, "profile_id", 64))
    text = string(data, "text", empty=True)
    match data.get("kind"):
        case "source":
            return m.Source(origin_of(data.get("origin")), text, number(data, "at"), table_of(data.get("table")))
        case "note":
            return m.Note(text)
        case "draft":
            return m.Draft(text, evidence_of(data.get("source")))
        case "result":
            state: m.Settled | m.Failure | m.Running
            match data.get("status"):
                case "complete":
                    state = m.Settled.COMPLETE
                case "stopped":
                    state = m.Settled.STOPPED
                case "interrupted":
                    state = m.Settled.INTERRUPTED
                case "failed":
                    state = m.Failure(string(data, "reason", 8_000))
                case "running":
                    request = object_of(data.get("request"))
                    state = m.Running(m.Request(string(request, "id", 128), string(request, "space", 128),
                        string(request, "result", 128), string(request, "prompt", 8_000),
                        tuple(evidence_of(source) for source in array(request, "sources", 24))))
                case _:
                    raise ValueError("Unknown saved result state.")
            return m.Result(string(data, "prompt"), text, tuple(evidence_of(source) for source in array(data, "sources", 24)),
                            state, texts(array(data, "steps", 128)), reference=reference_of(data.get("reference")))
        case _:
            raise ValueError("Unknown canvas content.")


def encode(canvas: m.Canvas) -> str:
    data: dict[str, Json] = {"version": 2, "current": canvas.current, "replace_taskbar": canvas.replace_taskbar,
        "items": [{"id": item.id, "title": item.title, "revision": item.revision, "content": content_data(item.content)} for item in canvas.items],
        "spaces": [{"id": space.id, "title": space.title, "selection": list(space.selection), "composer": space.composer,
                    "camera": {"x": space.camera.x, "y": space.camera.y, "zoom": space.camera.zoom},
                    "placements": [{"id": p.item_id, "x": p.x, "y": p.y, "width": p.width, "height": p.height, "parked": p.parked}
                                   for p in space.placements]} for space in canvas.spaces]}
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def decode(text: str) -> m.Canvas:
    data = object_of(cast(Json, json.loads(text)))
    if type(data.get("version")) is not int or data.get("version") not in (1, 2):
        raise ValueError("This canvas was saved by an unsupported version of Toolkit.")
    items: list[m.Item] = []
    for value in array(data, "items"):
        item = object_of(value)
        content = content_of(item.get("content"))
        if isinstance(content, m.WebApp) and data["version"] == 1:
            raise ValueError("Browser cards require canvas storage version 2.")
        items.append(m.Item(string(item, "id", 128), string(item, "title", 240), content, integer(item, "revision")))
    identities = {item.id for item in items}
    if len(identities) != len(items):
        raise ValueError("Duplicate canvas identities.")
    spaces: list[m.Space] = []
    for value in array(data, "spaces", 16):
        space = object_of(value)
        camera = object_of(space.get("camera"))
        placements: list[m.Placement] = []
        for row in array(space, "placements"):
            p = object_of(row)
            placements.append(m.Placement(string(p, "id", 128), number(p, "x"), number(p, "y"), number(p, "width"),
                                          number(p, "height"), boolean(p, "parked")))
        placed = {p.item_id for p in placements}
        visible = {p.item_id for p in placements if not p.parked}
        selection = texts(array(space, "selection", 24))
        if len(placed) != len(placements) or not placed <= identities or len(set(selection)) != len(selection) or not set(selection) <= visible:
            raise ValueError("Invalid canvas arrangement or selection.")
        spaces.append(m.Space(string(space, "id", 128), string(space, "title", 80), tuple(placements), selection,
                              m.Camera(number(camera, "x"), number(camera, "y"), number(camera, "zoom")),
                              string(space, "composer", 8_000, empty=True)))
    current = string(data, "current", 128)
    if not spaces or len({space.id for space in spaces}) != len(spaces) or current not in {space.id for space in spaces}:
        raise ValueError("Invalid workspace selection.")
    requests: set[str] = set()
    for saved_item in items:
        if isinstance(saved_item.content, m.Result) and isinstance(saved_item.content.state, m.Running):
            request = saved_item.content.state.request
            if request.id in requests or request.result_id != saved_item.id or request.space_id not in {space.id for space in spaces}:
                raise ValueError("A saved request must belong to exactly one result and an existing workspace.")
            requests.add(request.id)
    return m.Canvas(tuple(items), tuple(spaces), current, boolean(data, "replace_taskbar"))


class CanvasStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> m.Canvas:
        if not self.path.exists():
            return m.Canvas()
        if self.path.stat().st_size > 32_000_000:
            raise ValueError("The saved canvas exceeds 32 MB.")
        return decode(self.path.read_text(encoding="utf-8"))

    def save(self, canvas: m.Canvas) -> None:
        data = encode(canvas)
        if len(data.encode("utf-8")) > 32_000_000:
            raise ValueError("This canvas exceeds the 32 MB storage limit. Export and remove unused material before adding more.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".pending")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Windows readers, including indexing, can briefly deny a rename even
        # after our file is closed. Keep the previous complete file available
        # while retrying; permanent failures still reach the UI and close gate.
        for delay in (.01, .03, .06, .12, .24):
            try:
                temporary.replace(self.path)
                return
            except PermissionError:
                time.sleep(delay)
        temporary.replace(self.path)
