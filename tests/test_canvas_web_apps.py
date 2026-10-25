"""Saved web applications retain configuration without becoming captured sources."""

from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from assistant import canvas_model as m
from assistant.browser_surface_values import BrowserTarget, CapturedPage
from assistant.canvas_requests import CanvasRequests
from assistant.canvas_store import CanvasStore, content_of, decode, encode
from assistant.page_source_values import BrowserKind, Excerpt


def test_saved_web_app_retains_its_profile_and_workspace_arrangement(tmp_path: Path) -> None:
    canvas = m.add(m.Canvas(), m.Item("notes", "Notes", m.Note("Keep this beside the workbook.")))
    canvas = m.add(canvas, m.Item("workbook", "Excel", m.WebApp("https://excel.cloud.microsoft/", "default")))
    canvas = m.place(canvas, m.Placement("workbook", 460, -30, 870, 525))
    canvas = m.select(canvas, "workbook")
    canvas = m.with_space(canvas, replace(canvas.space, camera=m.Camera(880, 230, .8), composer="Compare the selected items"))
    store = CanvasStore(tmp_path / "canvas.json")

    store.save(canvas)
    restored = store.load()

    assert restored == canvas
    assert '"version":2' in encode(restored)
    app = restored.item("workbook")
    assert app is not None and app.content == m.WebApp("https://excel.cloud.microsoft/", "default")
    assert m.sources(app.content) == ()


def test_reopened_workspaces_preserve_distinct_named_browser_profiles(tmp_path: Path) -> None:
    workbook = m.Item("workbook", "Quarterly workbook", m.WebApp("https://office.example.test/quarterly", "work-account"), 2)
    notes = m.Item("notes", "Personal notes", m.WebApp("https://notes.example.test/projects", "personal-account"), 4)
    work_space = m.Space("work", "Work", (m.Placement("workbook", 120, -40, 960, 600),), ("workbook",),
                         m.Camera(440, 270, .75), "Review the quarterly figures")
    personal_space = m.Space("personal", "Personal", (m.Placement("notes", -80, 90, 800, 640),), ("notes",),
                             m.Camera(280, 360, .9), "Plan the weekend")
    canvas = m.Canvas((workbook, notes), (work_space, personal_space), "personal")
    path = tmp_path / "canvas.json"

    CanvasStore(path).save(canvas)
    restored = CanvasStore(path).load()

    assert restored.item("workbook") == workbook
    assert restored.item("notes") == notes
    assert restored.spaces == (work_space, personal_space)
    assert restored.current == "personal"


def test_selecting_a_live_web_app_prepares_only_its_unread_saved_reference(tmp_path: Path) -> None:
    app = m.Item("workbook", "Workbook", m.WebApp("https://example.test/original", "default"))
    canvas = m.select(m.add(m.Canvas(), app), app.id)
    _, request = m.request(canvas, "request-a", "result-a", "Summarize this workbook")
    prepared = CanvasRequests(tmp_path / "requests").prepare(request, at=123)

    assert prepared.request.sources == (m.Evidence("workbook", 1, "Workbook", "", m.WebPage("https://example.test/original")),)
    assert "https://example.test/original" in prepared.prompt
    assert "reference only; content has not been read" in prepared.prompt
    moved = m.put(canvas, replace(app, content=m.WebApp("https://example.test/another", "default")))
    assert moved.item(app.id) != app
    assert prepared.request.sources[0].origin == m.WebPage("https://example.test/original")


def test_explicit_capture_saves_a_separate_source_with_honest_managed_browser_provenance(tmp_path: Path) -> None:
    config = m.WebApp("https://example.test/book", "default")
    app = m.Item("workbook", "Workbook", config)
    captured = CapturedPage(BrowserTarget(app.id, "page-a", "document-a"), app.title, config.url, "Hi 🌍", 123, Excerpt(0, 5, 20))
    observation = captured.observation
    source = m.Item("capture-a", "Workbook excerpt", m.Source(
        m.WebPage(observation.reference.url, provenance=observation.provenance), observation.text, observation.observed_at,
    ))
    canvas = m.add(m.add(m.Canvas(), app), source)
    store = CanvasStore(tmp_path / "canvas.json")
    store.save(canvas)
    restored = store.load()

    assert restored.item(app.id) == app and restored.item(source.id) == source
    assert observation.reference.browser is BrowserKind.TOOLKIT
    assert observation.reference.binding_id == "page-a" and observation.reference.target_id == "document-a"
    assert observation.scope == Excerpt(0, 5, 20)
    _, request = m.request(m.select(restored, source.id), "request-a", "result-a", "Summarize this excerpt")
    prepared = CanvasRequests(tmp_path / "requests").prepare(request, at=124)
    assert prepared.request.sources[0].text == "Hi 🌍"
    assert "excerpt only" in prepared.prompt and observation.version in prepared.prompt


def test_saved_browser_configuration_rejects_paths_credentials_and_invalid_urls() -> None:
    for profile in ("", "..", "../other", "profiles/other", "C:\\profiles\\other", "UPPER", "profile\0id"):
        with TestCase().assertRaises(ValueError):
            m.WebApp("https://example.test/book", profile)
    for url in ("file:///private.txt", "javascript:alert(1)", "https://user:secret@example.test/book", "https://example.test/\nbook",
                "https://example.test/\ud800", " https://example.test/book", "https://example.test/book ",
                "https://example.test:99999/book", "https://example.test:0/book"):
        with TestCase().assertRaises(ValueError):
            m.WebApp(url, "default")


def test_legacy_canvas_upgrades_without_changing_its_existing_items_or_layout(tmp_path: Path) -> None:
    legacy = ('{"version":1,"current":"main","replace_taskbar":false,'
              '"items":[{"id":"note","title":"Saved note","revision":4,"content":{"kind":"note","text":"Keep me"}}],'
              '"spaces":[{"id":"main","title":"My work","selection":["note"],"composer":"Unsubmitted request",'
              '"camera":{"x":50,"y":75,"zoom":0.8},'
              '"placements":[{"id":"note","x":-20,"y":30,"width":410,"height":285,"parked":false}]}]}')
    store = CanvasStore(tmp_path / "canvas.json")
    store.path.write_text(legacy, encoding="utf-8")
    original = store.load()
    app = m.Item("workbook", "Workbook", m.WebApp("https://example.test/book", "default"))
    extended = m.put(original, app)
    store.save(extended)

    restored = store.load()
    assert restored.space == original.space
    assert restored.item("note") == m.Item("note", "Saved note", m.Note("Keep me"), 4)
    assert restored.item(app.id) == app
    assert '"version":2' in encode(restored)


def test_storage_rejects_unknown_schemas_and_live_fields_on_saved_web_apps() -> None:
    canvas = m.add(m.Canvas(), m.Item("workbook", "Workbook", m.WebApp("https://example.test/book", "default")))
    for version in ("1", "3", "true"):
        with TestCase().assertRaises(ValueError):
            decode(encode(canvas).replace('"version":2', '"version":' + version))
    with TestCase().assertRaises(ValueError):
        content_of({"kind": "web_app", "url": "https://example.test/book", "profile_id": "default", "document_id": "live-document"})


def test_new_web_app_cards_are_placed_beside_existing_apps() -> None:
    first = m.Item("first", "First", m.WebApp("https://example.test/first", "default"))
    second = m.Item("second", "Second", m.WebApp("https://example.test/second", "default"))
    canvas = m.add(m.add(m.Canvas(), first), second)
    a, b = canvas.space.placements
    assert a.x + a.width <= b.x or b.x + b.width <= a.x or a.y + a.height <= b.y or b.y + b.height <= a.y
