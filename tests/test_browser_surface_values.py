"""Browser surfaces accept only current layouts and exact live documents."""

from dataclasses import replace
from unittest import TestCase

from assistant import browser_surface_values as v
from assistant import canvas_model as m
from assistant.page_source_values import Complete, Excerpt


def test_only_a_new_layout_for_the_current_workspace_can_present_browser_surfaces() -> None:
    slot = v.SurfaceFrame("workbook", v.CssRect(-40, 60, 800, 480),
                          (v.CssRect(0, 60, 300, 200), v.CssRect(400, 60, 360, 480)), .8)
    frame = v.ViewportFrame("main", "shell-a", 7, v.CssRect(0, 0, 900, 600), v.Visible((slot,)))

    assert v.accepts_frame(frame, space_id="main", epoch="shell-a", after_sequence=6, item_ids=frozenset({"workbook"}))
    assert not v.accepts_frame(frame, space_id="main", epoch="shell-a", after_sequence=7, item_ids=frozenset({"workbook"}))
    assert not v.accepts_frame(frame, space_id="other", epoch="shell-a", after_sequence=6, item_ids=frozenset({"workbook"}))
    assert not v.accepts_frame(frame, space_id="main", epoch="shell-b", after_sequence=6, item_ids=frozenset({"workbook"}))
    assert not v.accepts_frame(frame, space_id="main", epoch="shell-a", after_sequence=6, item_ids=frozenset({"private"}))


def test_live_document_target_cannot_follow_a_navigation_crash_or_replacement_page() -> None:
    spec = v.SurfaceSpec("workbook", m.WebApp("https://example.test/book", "default"))
    target = v.BrowserTarget(spec.item_id, "page-a", "document-a")
    ready = v.BrowserObservation(spec.item_id, spec.app.url, "Workbook", v.Ready(target), can_back=True)
    snapshot = v.BrowserSnapshot((ready,))

    assert v.current_target(snapshot, target) == ready
    assert ready.target == target
    for stale in (replace(target, item_id="private"), replace(target, instance_id="page-b"), replace(target, document_id="document-b")):
        assert v.current_target(snapshot, stale) is None
    for state in (v.Loading("page-a"), v.Crashed("page-a", "Renderer ended"), v.Dormant.CLOSED):
        retired = replace(ready, state=state)
        assert retired.target is None
        assert v.current_target(v.BrowserSnapshot((retired,)), target) is None


def test_explicit_capture_preserves_exact_document_and_validates_unicode_coverage() -> None:
    target = v.BrowserTarget("workbook", "page-a", "document-a")
    capture = v.CapturedPage(target, "Workbook", "https://example.test/book", "🧠x", 123, Excerpt(10, 13, 200))
    assert capture.target == target and capture.scope == Excerpt(10, 13, 200)
    with TestCase().assertRaises(ValueError):
        v.CapturedPage(target, "Workbook", "https://example.test/book", "🧠x", 123, Excerpt(10, 12, 200))
    with TestCase().assertRaises(ValueError):
        v.CapturedPage(target, "Workbook", "https://example.test/book", "🧠" * 32_001, 123, Complete.MAIN_DOCUMENT)
    with TestCase().assertRaises(ValueError):
        v.CapturedPage(target, "Workbook", "https://example.test/book", "", float("nan"), Complete.MAIN_DOCUMENT)
    with TestCase().assertRaises(ValueError):
        v.CapturedPage(target, "Workbook", "file:///private.txt", "local file", 123, Complete.MAIN_DOCUMENT)


def test_runtime_address_observations_do_not_mislabel_transient_browser_pages() -> None:
    target = v.BrowserTarget("workbook", "page-a", "document-a")
    blob = "blob:https://example.test/460a061d-4d9c-47bd-9da6-a32f3de17e81"
    observed = v.BrowserObservation("workbook", blob, "Popup", v.Ready(target))
    assert observed.url == blob
    assert v.BrowserObservation("workbook", "", "", v.Loading("page-a")).url == ""
    with TestCase().assertRaises(ValueError):
        v.BrowserObservation("workbook", "", "", v.Ready(target))
    with TestCase().assertRaises(ValueError):
        v.CapturedPage(target, "Popup", blob, "Document", 123, Complete.MAIN_DOCUMENT)


def test_browser_commands_require_a_card_and_a_safe_navigation_destination() -> None:
    command = v.Navigate("workbook", "https://example.test/book")
    assert command.url == "https://example.test/book"
    for url in ("javascript:alert(1)", "file:///private.txt", "https://user:secret@example.test/book", "https://example.test/\nbook",
                " https://example.test/book", "https://example.test/book ", "https://example.test:99999/book", "https://example.test:0/book"):
        with TestCase().assertRaises(ValueError):
            v.Navigate("workbook", url)
    for constructor in (v.Back, v.Forward, v.Reload, v.Remove, v.Focus):
        with TestCase().assertRaises(ValueError):
            constructor("")


def test_only_the_current_successful_close_request_can_remove_a_saved_card() -> None:
    closed = v.Closed("workbook", "close-a")
    assert v.accepts_close(closed, item_id="workbook", request_id="close-a")
    assert not v.accepts_close(closed, item_id="workbook", request_id="close-b")
    assert not v.accepts_close(closed, item_id="private", request_id="close-a")
    assert not v.accepts_close(v.CloseCancelled("workbook", "close-a"), item_id="workbook", request_id="close-a")


def test_invalid_clips_cannot_place_browser_input_outside_its_surface_or_viewport() -> None:
    rect = v.CssRect(10, 20, 400, 300)
    for clip in (v.CssRect(0, 20, 400, 300), v.CssRect(10, 20, 401, 300), v.CssRect(10, 20, 400, 301)):
        with TestCase().assertRaises(ValueError):
            v.SurfaceFrame("workbook", rect, (clip,), 1)
    slot = v.SurfaceFrame("workbook", rect, (rect,), 1)
    with TestCase().assertRaises(ValueError):
        v.ViewportFrame("main", "shell-a", 1, v.CssRect(0, 0, 400, 300), v.Visible((slot,)))
    with TestCase().assertRaises(ValueError):
        v.Visible((slot, slot))
    for invalid in (float("nan"), float("inf"), -1, 0):
        with TestCase().assertRaises(ValueError):
            v.CssRect(0, 0, invalid, 100)
    for invalid_zoom in (.1, 6, float("nan")):
        with TestCase().assertRaises(ValueError):
            v.SurfaceFrame("workbook", rect, (rect,), invalid_zoom)


def test_suspension_accepts_an_empty_workspace_and_retires_older_visible_layouts() -> None:
    rect = v.CssRect(0, 0, 800, 600)
    suspended = v.ViewportFrame("main", "shell-a", 8, rect, v.Suspended(v.Suspension.MODAL))
    assert v.accepts_frame(suspended, space_id="main", epoch="shell-a", after_sequence=7, item_ids=frozenset())
    covered = v.SurfaceFrame("workbook", rect, (), 1)
    previous = v.ViewportFrame("main", "shell-a", 7, rect, v.Visible((covered,)))
    assert not v.accepts_frame(previous, space_id="main", epoch="shell-a", after_sequence=8, item_ids=frozenset({"workbook"}))
    resumed = replace(previous, sequence=9)
    assert v.accepts_frame(resumed, space_id="main", epoch="shell-a", after_sequence=8, item_ids=frozenset({"workbook"}))


def test_observations_cannot_bind_another_card_or_publish_ambiguous_documents() -> None:
    target = v.BrowserTarget("workbook", "page-a", "document-a")
    observed = v.BrowserObservation("workbook", "https://example.test/book", "Workbook", v.Ready(target))
    with TestCase().assertRaises(ValueError):
        replace(observed, item_id="another-card")
    with TestCase().assertRaises(ValueError):
        v.BrowserSnapshot((observed, replace(observed, state=v.Ready(replace(target, document_id="document-b")))))
