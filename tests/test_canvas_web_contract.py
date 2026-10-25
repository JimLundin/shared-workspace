"""The browser boundary validates commands and preserves source/result truth."""

import json
import unittest
from dataclasses import replace

from assistant import browser_surface_values as browser
from assistant import canvas_model as m
from assistant import canvas_web_contract as web
from assistant.canvas_drafts import DraftSnapshot, PendingDraft, SavedDraft, capture
from assistant.llm import Json
from assistant.outlook import DraftFolder, OutlookLocation, SavedOutlookDraft

from .test_canvas import selected_canvas


def test_web_commands_validate_geometry_targets_and_text() -> None:
    assert web.command('{"kind":"move","space":"main","id":"budget","x":-12,"y":40,"width":420,"height":300}') == web.MoveItem(
        "main", m.Placement("budget", -12, 40, 420, 300),
    )
    assert web.command('{"kind":"edit","id":"draft","text":""}') == web.WriteText("draft", "")
    assert web.command('{"kind":"save_draft","id":"draft","revision":3}') == web.SaveDraft("draft", 3)
    oversized: dict[str, Json] = {"kind": "submit", "space": "main", "text": "x" * 8_001}
    previews: list[Json] = [{} for _ in range(13)]
    too_many: dict[str, Json] = {"kind": "previews", "space": "main", "previews": previews}
    invalid = (
        '[]', 'null', '{}', '{"kind":"shell","command":"calc.exe"}', '{"kind":"approve","id":"approval"}',
        '{"kind":"link","url":"file:///C:/secrets.txt"}', '{"kind":"link","url":"javascript:alert(1)"}',
        '{"kind":"move","space":"main","id":"budget","x":NaN,"y":0,"width":420,"height":300}',
        '{"kind":"move","space":"main","id":"budget","x":0,"y":0,"width":1,"height":300}',
        '{"kind":"camera","space":"main","x":0,"y":0,"zoom":true}',
        '{"kind":"camera","space":"main","x":0,"y":0,"zoom":100}',
        '{"kind":"save_draft","id":"draft","revision":true}',
        '{"kind":"previews","space":"main","previews":[{"id":"x","x":0,"y":0,"width":Infinity,"height":2}]}',
        '{"kind":"previews","space":"main","previews":[{"id":"x","x":-1,"y":0,"width":2,"height":2}]}',
        json.dumps(oversized), json.dumps(too_many),
    )
    for text in invalid:
        with unittest.TestCase().assertRaises(ValueError, msg=text[:120]):
            web.command(text)


def test_markdown_does_not_turn_source_text_into_executable_html() -> None:
    value = web.rendered_text('## A result\n\n<script>bad()</script><img onerror="bad()" src="x">\n\n[click](javascript:bad())')
    assert '<h2>A result</h2>' in value
    assert '<script>' not in value and '<img ' not in value and 'href="javascript:' not in value
    assert '&lt;script&gt;' in value
    table = web.rendered_text('| A | B |\n|---|---|\n|one|two|')
    assert '<table>' in table and '<td>two</td>' in table


def test_projection_exposes_exact_retained_sources_and_current_staleness() -> None:
    canvas, request = m.request(selected_canvas(), "run", "result", "Compare")
    canvas = m.accept(canvas, m.Completed(request.id, "## Original result"))
    canvas = m.draft(canvas, "result", "draft")
    canvas = m.edit(canvas, "draft", "My edited wording")
    budget = canvas.item("budget")
    assert budget is not None and isinstance(budget.content, m.Source)
    canvas = m.observe(canvas, replace(budget, content=replace(budget.content, text="260000")))
    value = web.snapshot(canvas)
    projected = value["items"]
    assert isinstance(projected, list)
    result = next(item for item in projected if isinstance(item, dict) and item["id"] == "result")
    assert isinstance(result, dict) and result["stale"] is True and result["status"] == "complete"
    sources = result["sources"]
    assert isinstance(sources, list) and isinstance(sources[0], dict)
    assert sources[0]["text"] == "240000"
    draft = next(item for item in projected if isinstance(item, dict) and item["id"] == "draft")
    assert isinstance(draft, dict) and draft["text"] == "My edited wording"


def test_approval_projection_requires_the_existing_full_preview_review() -> None:
    canvas, request = m.request(selected_canvas(), "run", "result", "Compare")
    canvas = m.accept(canvas, m.NeedsInput(request.id, m.Approval("approval", "Write file", "Review the full change")))
    item = canvas.item("result")
    assert item is not None
    value = web.item_view(canvas, item)
    assert value["status"] == "review"
    assert value["review"] == {"title": "Write file", "detail": "Review the full change"}
    assert "yes" not in json.dumps(value)


def test_mail_receipt_does_not_claim_later_edits_were_saved() -> None:
    canvas, request = m.request(selected_canvas(), "run", "result", "Compare")
    canvas = m.accept(canvas, m.Completed(request.id, "A result"))
    canvas = m.draft(canvas, "result", "draft")
    attempt = capture(canvas, "draft", 1, "attempt")
    pending = web.draft_receipts(DraftSnapshot(1, (PendingDraft(attempt),)))
    assert pending["draft"] == {"revision": 1, "status": "pending", "label": "Saving v1…"}
    saved = SavedDraft(attempt, SavedOutlookDraft(OutlookLocation("draft-entry", DraftFolder("folder", "store")), "key", "a" * 64))
    receipt = web.draft_receipts(DraftSnapshot(2, (saved,)))["draft"]
    assert isinstance(receipt, dict) and receipt["revision"] == 1 and receipt["status"] == "saved"
    changed = m.edit(canvas, "draft", "A later edit")
    assert changed.item("draft") != canvas.item("draft")
    assert "v1" in str(receipt["label"])


def test_application_layout_validates_epoch_rectangles_and_clipping() -> None:
    rect: dict[str, Json] = {"x": 10, "y": 80, "width": 640, "height": 400}
    entry: dict[str, Json] = {"id": "app", "rect": rect, "clips": [rect], "zoom": .75}
    command: dict[str, Json] = {"kind": "app_layout", "space": "main", "epoch": "viewport-one", "sequence": 1, "slots": [entry]}
    assert web.command(json.dumps(command)) == web.AppLayout(
        "main", "viewport-one", 1, browser.Visible((browser.SurfaceFrame("app", browser.CssRect(10, 80, 640, 400),
                                                                        (browser.CssRect(10, 80, 640, 400),), .75),)),
    )
    slots: list[Json] = [{**entry, "id": f"app-{index}"} for index in range(13)]
    twelve: dict[str, Json] = {**command, "slots": slots[:12]}
    accepted = web.command(json.dumps(twelve))
    assert isinstance(accepted, web.AppLayout) and isinstance(accepted.mode, browser.Visible)
    assert len(accepted.mode.entries) == 12
    invalid = (
        dict(command, epoch=None), dict(command, epoch=""), dict(command, sequence=True), dict(command, sequence=0),
        dict(command, slots=[entry, entry]), dict(command, slots=[dict(entry, zoom=0)]),
        dict(command, slots=[dict(entry, clips=[dict(rect, x=-1)])]),
        dict(command, slots=[dict(entry, rect=dict(rect, width=float("nan")))]),
        dict(command, slots=[dict(entry, clips=[rect] * 49)]),
        dict(command, mode="modal"), dict(command, mode="unknown"),
        dict(command, slots=slots),
    )
    for value in invalid:
        with unittest.TestCase().assertRaises(ValueError):
            web.command(json.dumps(value))
    modal: dict[str, Json] = {**command, "mode": "modal", "slots": []}
    assert web.command(json.dumps(modal)) == web.AppLayout(
        "main", "viewport-one", 1, browser.Suspended(browser.Suspension.MODAL),
    )
    hidden: dict[str, Json] = {**command, "mode": "hidden", "slots": []}
    assert web.command(json.dumps(hidden)) == web.AppLayout(
        "main", "viewport-one", 1, browser.Suspended(browser.Suspension.HIDDEN),
    )


def test_browser_state_enables_capture_only_for_an_exact_ready_document() -> None:
    app = m.WebApp("https://example.test/book", "default")
    item = m.Item("app", "Workbook", app)
    assert web.command('{"kind":"new_web_app","url":"https://vscode.dev/"}') == web.CreateWebApp(m.WebApp("https://vscode.dev/", "default"))
    assert web.command('{"kind":"web_app","space":"main","id":"app","action":"ask_agent"}') == web.ActOnWebApp(
        "main", "app", web.WebAppAction.ASK_AGENT,
    )
    starting = browser.BrowserObservation("app", app.url, "", browser.Loading("instance"))
    ready = replace(starting, title="Edited workbook", state=browser.Ready(browser.BrowserTarget("app", "instance", "document")))
    assert web.application_view(item, starting)["can_capture"] is False
    observed = web.application_view(item, ready)
    assert observed["can_capture"] is True and observed["title"] == "Edited workbook" and observed["live"] is True
    closed = web.application_view(item, replace(ready, state=browser.Dormant.CLOSED))
    assert closed["live"] is False and closed["can_capture"] is False
    failed = replace(ready, state=browser.Failed("instance", "Network unavailable"))
    assert web.application_view(item, failed)["can_capture"] is False
    assert web.application_view(item, failed)["error"] == "Network unavailable"
    value = web.item_view(m.add(m.Canvas(), item), item)
    assert value["kind"] == "application" and value["text"] == "" and value["sources"] == []
