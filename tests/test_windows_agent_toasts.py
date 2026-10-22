"""Real notification windows, with controlled time and no microphone or key input."""

import sys
import threading
import time
import unittest
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from itertools import pairwise
from typing import Literal, Protocol, cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows native agent notifications")

import tkinter as tk

from assistant.agent_session import Answer, Begin, Failed, Question, ReasoningSummary, Said, Session, StreamState, ToolActivity, transition
from assistant.agent_toasts import AgentToasts, Card
from assistant.agent_window import AgentWindow
from assistant.notifications import MAX_VISIBLE, WIDTH, Kind, MessageKey, ResponseKey
from assistant.panel import PanelPreferences
from assistant.recording_state import RecordingAction, RecordingState
from assistant.rich_text import formatted
from winapi import appearance, screen, windows
from winapi.shapes import Rect

from .test_agent_session import asking


class Notifications:
    __slots__ = ("agent", "animated", "jobs", "now", "recorded", "requested", "root", "view")

    def __init__(self, *, animated: bool) -> None:
        screen.use_physical_pixels()
        self.root = tk.Tk()
        self.root.withdraw()
        self.now = 100.0
        self.animated = animated
        self.jobs: deque[Callable[[], None]] = deque()
        self.requested = threading.Event()
        self.recorded: list[RecordingAction] = []
        self.view: AgentToasts
        self.agent = AgentWindow(
            self.stop, post=self.jobs.append, create_view=self.build,
            on_waiting=lambda waiting: self.requested.set() if waiting else None, on_recording=self.recorded.append,
        )
        self.pump()
        assert self.agent.ready.is_set(), self.agent.all_text()

    def build(self, agent: AgentWindow) -> AgentToasts:
        self.view = AgentToasts(
            self.root, agent, preferences=PanelPreferences(Rect(30, 30, 430, 790)), remember=False,
            clock=lambda: self.now, motion_enabled=lambda: self.animated,
        )
        return self.view

    def pump(self) -> None:
        while self.jobs:
            self.jobs.popleft()()
        self.root.update_idletasks()
        self.root.update()

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.view.refresh()
        self.view.tick()
        self.pump()

    def stop(self) -> None:
        self.agent.stopping()

    def close(self) -> None:
        self.agent.close()
        self.pump()
        self.root.destroy()


@contextmanager
def notifications(*, animated: bool = False) -> Generator[Notifications]:
    # The user's actual pointer must not affect a deterministic expiry check.
    with patch.object(tk.Misc, "winfo_pointerxy", return_value=(-100000, -100000)):
        panel = Notifications(animated=animated)
        try:
            yield panel
        finally:
            panel.close()


def top(bounds: Rect) -> int:
    return bounds.top


class IndicatorReading(Protocol):
    """The concrete Tk return type missing from its itemcget stub."""

    def itemcget(self, tag: str, option: str) -> str: ...


class WindowReading(Protocol):
    """Only the string-valued attribute used by the material checks."""

    def attributes(self, option: Literal["-transparentcolor"], /) -> str: ...


class ScrollReading(Protocol):
    """Only the no-argument overload; Tk's other yview overload is untyped."""

    def yview(self) -> tuple[float, float]: ...


def scroll_position(card: Card) -> float:
    return cast(ScrollReading, card.text).yview()[0]


def test_long_reply_keeps_its_ending_scrolls_and_stays_until_read() -> None:
    with notifications(animated=True) as panel:
        text = "A useful detail. " * 170 + "\n\nThe final instruction is to save the workbook."
        panel.agent.say(text)
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[MessageKey(0)]
        assert card.text.get("1.0", "end-1c") == "".join(span.text for span in formatted(text))
        assert scroll_position(card) == 0
        panel.advance(31)
        assert panel.view.cards[MessageKey(0)] is card and scroll_position(card) > 0
        panel.advance(10000)
        last = card.text.bbox("end-2c")
        assert last is not None and last[1] + last[3] <= card.text.winfo_height()
        panel.advance(31)
        panel.advance(.3)
        assert not panel.view.cards


def test_long_replies_take_turns_scrolling_instead_of_expiring_unread() -> None:
    with notifications(animated=True) as panel:
        panel.agent.say("First answer. " * 220)
        panel.agent.say("Second answer. " * 220)
        panel.pump()
        panel.advance(.3)
        first, second = panel.view.cards[MessageKey(0)], panel.view.cards[MessageKey(1)]
        panel.advance(31)
        assert scroll_position(first) > 0 and scroll_position(second) == 0
        assert not first.retiring and not second.retiring


def test_wheel_pauses_reading_and_streaming_preserves_the_chosen_position() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Read the notes", panel.stop)
        publish = panel.agent.updates()
        text = "A useful detail. " * 220
        publish(Answer("stream", text, 1))
        panel.pump()
        panel.advance(.3)
        panel.advance(31)
        card = panel.view.cards[MessageKey(1)]
        before = scroll_position(card)
        bounds = windows.outer_rect(card.handle)
        # The periodic glass observer must see the same hover as the wheel
        # event, regardless of when Tk runs its pending pointer callback.
        with patch.object(tk.Misc, "winfo_pointerxy", return_value=(bounds.left + 8, bounds.top + 8)):
            card.text.event_generate("<MouseWheel>", delta=120)
            panel.view.observe_pointer()
            panel.pump()
            assert scroll_position(card) < before and card.hovered
            anchor = card.text.index("@0,6")
            panel.now += 20
            panel.view.tick()
            panel.pump()
            assert card.text.index("@0,6") == anchor
            publish(Answer("stream", text + "\n\nMore details arrive.", 2))
            panel.pump()
            assert card.text.index("@0,6") == anchor
        panel.view.observe_pointer()
        panel.advance(10)
        assert card.text.compare(card.text.index("@0,6"), ">", anchor)


def test_reduced_motion_keeps_the_whole_reply_and_advances_by_pages() -> None:
    with notifications() as panel:
        text = "A useful detail. " * 220 + "\n\nThe end."
        panel.agent.say(text)
        panel.pump()
        card = panel.view.cards[MessageKey(0)]
        assert card.text.get("1.0", "end-1c") == "".join(span.text for span in formatted(text))
        panel.advance(5)
        assert scroll_position(card) == 0 and not panel.view.timing.active
        panel.advance(60)
        assert scroll_position(card) > 0 and not panel.view.timing.active
        panel.advance(10000)
        assert card.text.bbox("end-2c") is not None


def test_wheel_over_empty_glass_reaches_the_text_without_focusing_or_scrolling_other_apps() -> None:
    with notifications() as panel, patch("assistant.agent_panel.windows.focus"):
        panel.agent.say("A useful detail. " * 220)
        panel.pump()
        card = panel.view.cards[MessageKey(0)]
        router = panel.view.wheel
        assert router.ready.wait(timeout=2) and router.active, router.error
        bounds = windows.outer_rect(card.handle)
        foreground = windows.foreground()
        assert not router.dispatch(bounds.left - 1, bounds.top, -120)
        assert router.dispatch(bounds.left + 2, bounds.top + 2, -120)
        deadline = time.monotonic() + 1
        while scroll_position(card) == 0 and time.monotonic() < deadline:
            panel.pump()
            time.sleep(.005)
        assert scroll_position(card) > 0 and windows.foreground() == foreground
        panel.view.show(focus=True)
        panel.pump()
        assert not router.regions and not router.dispatch(bounds.left + 2, bounds.top + 2, -120)
        panel.agent.close()
        panel.pump()
        assert router.thread is not None and not router.thread.is_alive() and not router.active


def test_spinner_fits_its_contents_and_the_cross_stops_the_agent() -> None:
    with notifications() as panel:
        foreground = windows.foreground()
        panel.agent.begin("Check the notes", panel.stop)
        panel.pump()
        card = panel.view.cards[Kind.WORK]
        bounds = windows.outer_rect(card.handle)
        assert bounds.width == card.width < round(WIDTH * windows.scale(panel.root.winfo_id()) * .6)
        assert cast(str, card.stop.cget("text")) == "\u00d7"
        control = windows.outer_rect(card.stop.winfo_id())
        assert control.width >= 24 and control.height >= 24
        assert bounds.left < control.left < control.right < bounds.right
        assert card.label.winfo_width() >= card.label.winfo_reqwidth()
        card.stop.invoke()
        panel.pump()
        assert panel.agent.state.stopping and cast(str, card.stop.cget("state")) == "disabled"
        assert windows.foreground() == foreground


def test_short_replies_grow_horizontally_then_wrap_at_the_width_limit() -> None:
    with notifications() as panel:
        panel.agent.begin("Check the notes", panel.stop)
        publish = panel.agent.updates()
        sizes: list[tuple[int, int]] = []
        for text in ("Ready.", "Ready to review.", "Ready to review the plan.", "Ready to review the plan. " * 4):
            publish(Answer("stream", text, 1))
            panel.pump()
            card = panel.view.cards[MessageKey(1)]
            sizes.append((card.width, card.height))
            assert windows.outer_rect(card.handle).width == card.width
            last = card.text.bbox("end-2c")
            assert last is not None and last[0] + last[2] <= card.width
        cap = round(WIDTH * windows.scale(panel.root.winfo_id()))
        assert sizes[0][0] < sizes[1][0] < sizes[2][0] < sizes[3][0] == cap
        assert sizes[0][1] == sizes[1][1] == sizes[2][1] < sizes[3][1]


def test_intrinsic_width_uses_rendered_markdown_lines_and_constrains_unbroken_text() -> None:
    with notifications() as panel:
        panel.agent.begin("Check the notes", panel.stop)
        publish = panel.agent.updates()
        widths: list[int] = []
        for text in ("## A heading\n\n**Ready**\n\n- OK", "[Docs](https://example.org/" + "a" * 160 + ")", "W" * 50):
            publish(Answer("stream", text, 1))
            panel.pump()
            card = panel.view.cards[MessageKey(1)]
            widths.append(card.width)
            index = "1.0"
            while card.text.compare(index, "<", "end-1c"):
                row = card.text.dlineinfo(index)
                assert row is not None and row[0] + row[2] <= card.width
                index = card.text.index(f"{index} + 1 displaylines")
        cap = round(WIDTH * windows.scale(panel.root.winfo_id()))
        assert widths[1] < widths[0] < widths[2] == cap, "Hidden link destinations must not widen short labels."


def test_width_changes_retarget_smoothly_with_the_right_edge_anchored() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Check the notes", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("stream", "Ready.", 1))
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[MessageKey(1)]
        right = windows.outer_rect(card.handle).right
        for text in ("Ready to review.", "Ready to review the plan.", "Ready to review the plan. " * 4, "**Done.**"):
            before = windows.outer_rect(card.handle)
            publish(Answer("stream", text, 1))
            panel.pump()
            assert windows.outer_rect(card.handle) == before, "Adaptive width must not snap when a chunk arrives."
            widths: list[int] = []
            for _ in range(4):
                panel.now += .016
                panel.view.tick()
                panel.pump()
                bounds = windows.outer_rect(card.handle)
                assert bounds.right == right
                assert min(before.width, card.width) <= bounds.width <= max(before.width, card.width)
                widths.append(bounds.width)
            assert len(set(widths)) > 1
        panel.advance(.3)
        assert windows.outer_rect(card.handle).width == card.width and card.motion is None


def test_adaptive_width_respects_a_narrow_work_area_and_keeps_voice_hints_separate() -> None:
    with notifications() as panel, patch.object(AgentToasts, "area", return_value=Rect(40, 40, 290, 1040)):
        state = transition(Session(identity="narrow-area"), Begin("Check the notes", 1))
        state = transition(state, Question("question", "Which?", "", 2))
        panel.view.render(state)
        panel.pump()
        cap = 250 - 2 * round(16 * windows.scale(panel.root.winfo_id()))
        for card in panel.view.cards.values():
            assert windows.outer_rect(card.handle).width == card.width <= cap
        question = panel.view.cards[MessageKey(1)]
        assert question.text.winfo_rooty() + question.text.winfo_height() <= question.hint.winfo_rooty()
        assert cast(str, question.hint.cget("text")) == "Hold N to reply by voice"
        assert question.hint.winfo_height() >= question.hint.winfo_reqheight()


def test_question_body_does_not_lose_lines_to_the_voice_reply_hint() -> None:
    with notifications() as panel:
        state = transition(Session(identity="clipping"), Begin("Please find the right workbook and check the figures for this month.", 1))
        panel.view.render(state)
        panel.pump()
        question = ("Which workbook should I use for the monthly summary?\n\n"
                    "The revenue sheet has the actual figures, while the forecast covers the next quarter.")
        state = transition(state, Question("question", question, "Please say the filename.", 2))
        panel.view.render(state)
        panel.pump()
        card = next(card for card in panel.view.cards.values() if "Which workbook" in card.text.get("1.0", "end-1c"))
        last = card.text.bbox("end-2c")
        assert last is not None and last[1] + last[3] <= card.text.winfo_height(), "The last question line must be readable above the hint."
        assert card.text.winfo_rooty() + card.text.winfo_height() <= card.hint.winfo_rooty()


def test_stream_update_never_resizes_a_visible_window_to_measure_its_text() -> None:
    with notifications() as panel:
        panel.agent.begin("Read the notes", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("stream", "Movement probe: the letters should travel with this card.", 1))
        panel.pump()
        card = next(card for card in panel.view.cards.values() if "Movement probe" in card.text.get("1.0", "end-1c"))
        before = windows.outer_rect(card.handle).height
        heights: list[int] = []
        idle = tk.Misc.update_idletasks

        def observed_idle(widget: tk.Misc) -> None:
            idle(widget)
            heights.append(windows.outer_rect(card.handle).height)

        with patch.object(tk.Misc, "update_idletasks", observed_idle):
            publish(Answer("stream", "Updated probe: the letters should travel with this card.", 2))
            panel.pump()
        assert max(heights, default=card.height) <= max(before, card.height), "Live measurement must never show an intermediate window size."


def test_narrow_conversation_budget_keeps_complete_lines_and_a_separate_footer() -> None:
    with notifications() as panel:
        state = transition(Session(identity="long-conversation"), Begin("Summarize the meeting", 1))
        for number in range(3):
            state = transition(state, Said(f"## Result {number}\n\n" + "A **formatted** sentence with details. " * 60, 2))
        state = transition(state, Question("question", "Which notes should I use?", "Please choose the revenue sheet or the forecast.", 3))
        panel.view.preferences = replace(panel.view.preferences, rail_width=640)
        for preference in (appearance.Material.GLASS, appearance.Material.SOLID, appearance.Contrast("#ffffff", "#000000")):
            def selected(value: appearance.Preference = preference) -> appearance.Preference:
                return value

            panel.view.material_preference = selected
            panel.view.render(state)
            panel.pump()
            assert len(panel.view.cards) == MAX_VISIBLE
            bounds = sorted((windows.outer_rect(card.handle) for card in panel.view.cards.values()), key=top)
            assert panel.view.work is not None and bounds[0].top >= panel.view.work.top
            assert bounds[-1].bottom < panel.view.work.bottom
            assert all(first.bottom < second.top for first, second in pairwise(bounds))
            for card in panel.view.cards.values():
                assert card.width <= round(WIDTH * windows.scale(panel.root.winfo_id()))
                assert windows.outer_rect(card.handle).height == card.height <= card.maximum
                if card.text.winfo_manager():
                    row = card.text.dlineinfo(f"@0,{card.text.winfo_height() - 7}")
                    assert row is not None and row[1] >= 0 and row[1] + row[3] <= card.text.winfo_height() - 6
                    if card.hint.winfo_manager():
                        assert card.text.winfo_rooty() + card.text.winfo_height() <= card.hint.winfo_rooty()
            question = panel.view.cards[MessageKey(4)]
            assert question.text.bbox("end-2c") is not None and "Hold N" in cast(str, question.hint.cget("text"))


def test_stream_growth_reflows_the_whole_stack_without_overlapping_neighbors() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Summarize the notes", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("stream", "The summary is ready.", 1))
        panel.pump()
        panel.advance(.3)
        reply = panel.view.cards[MessageKey(1)]
        height = reply.height
        before = windows.outer_rect(reply.handle)
        publish(Answer("stream", "## Summary\n\n- **Alex:** prepare the draft by Friday.\n"
                       "- **Sam:** confirm the review date.\n- **Robin:** send the updated plan.", 2))
        panel.pump()
        assert reply.height > height
        assert windows.outer_rect(reply.handle) == before, "A stream update must start at the visible geometry, without a jump."
        positions: list[int] = []
        for _ in range(20):
            panel.now += .016
            panel.view.tick()
            panel.pump()
            bounds = sorted((windows.outer_rect(card.handle) for card in panel.view.cards.values()), key=top)
            assert all(first.bottom < second.top for first, second in pairwise(bounds))
            assert panel.view.work is not None and bounds[-1].bottom < panel.view.work.bottom
            positions.append(windows.outer_rect(reply.handle).top)
        assert len(set(positions)) > 3 and positions == sorted(positions, reverse=True)
        assert windows.outer_rect(reply.handle).height == reply.height


def test_stream_chunks_preserve_the_text_already_being_read() -> None:
    with notifications() as panel:
        panel.agent.begin("Summarize the notes", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("stream", "The first paragraph stays here.", 1))
        panel.pump()
        reply = panel.view.cards[MessageKey(1)]
        reply.text.mark_set("reading", "1.10")
        reply.text.mark_gravity("reading", "left")
        reply.text.tag_add("sel", "1.4", "1.9")
        publish(Answer("stream", "The first paragraph stays here.\n\nA **new** paragraph arrives.", 2))
        panel.pump()
        assert reply.text.index("reading") == "1.10", "Appending a chunk must not replace earlier text."
        assert reply.text.get("sel.first", "sel.last") == "first"
        assert reply.text.tag_ranges("bold")


def test_stream_bursts_retarget_growing_and_shrinking_neighbors_together() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Summarize the notes", panel.stop)
        panel.agent.say("Earlier context. " * 40)
        panel.agent.say("Another useful finding. " * 30)
        publish = panel.agent.updates()
        publish(Answer("stream", "The summary follows.", 1))
        panel.pump()
        panel.advance(.3)
        reply = panel.view.cards[MessageKey(3)]
        text = "The summary follows."
        for index in range(45):
            panel.now += .016
            panel.view.tick()
            panel.pump()
            if index in (0, 2, 5, 8, 12):
                before = windows.outer_rect(reply.handle)
                text += "\n\nA **formatted** finding with more detail. " * 2
                publish(Answer("stream", text, 2))
                panel.pump()
                assert windows.outer_rect(reply.handle) == before
            bounds = sorted((windows.outer_rect(card.handle) for card in panel.view.cards.values()), key=top)
            assert all(first.bottom < second.top for first, second in pairwise(bounds))
            assert panel.view.work is not None and bounds[0].top >= panel.view.work.top and bounds[-1].bottom < panel.view.work.bottom
            for card in panel.view.cards.values():
                if card.text.winfo_manager() and card.hint.winfo_manager():
                    assert card.text.winfo_rooty() + card.text.winfo_height() <= card.hint.winfo_rooty()
        assert all(card.motion is None for card in panel.view.cards.values())


def test_stream_reveals_only_new_text_and_finishes_when_the_agent_is_done() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Summarize the notes", panel.stop)
        publish = panel.agent.updates()
        publish(Answer("stream", "First.", 1))
        panel.pump()
        panel.advance(.3)
        reply = panel.view.cards[MessageKey(1)]
        for index in range(30):
            publish(Answer("stream", "First." + " a" * (index + 1), 2))
            panel.pump()
            assert not any(tag.startswith("reveal-") for tag in reply.text.tag_names("1.2"))
            assert len([tag for tag in reply.text.tag_names() if tag.startswith("reveal-")]) <= 12
        assert any(tag.startswith("reveal-") for tag in reply.text.tag_names("end-2c"))
        final = "First." + " a" * 30
        publish(Answer("stream", final, 3, StreamState.COMPLETE))
        panel.agent.done(final)
        panel.agent.finish()
        panel.pump()
        panel.advance(.3)
        assert reply.text.get("1.0", "end-1c") == final
        assert not any(tag.startswith("reveal-") for tag in reply.text.tag_names())
        assert panel.view.animation_timer is None and not panel.view.timing.active


def test_motion_timing_is_balanced_on_reduced_motion_history_and_close() -> None:
    calls: list[str] = []

    def begin(period: int) -> int:
        assert period == 1
        calls.append("begin")
        return 0

    def end(period: int) -> int:
        assert period == 1
        calls.append("end")
        return 0

    with patch.object(appearance, "_TimeBeginPeriod", begin), patch.object(appearance, "_TimeEndPeriod", end):
        with notifications(animated=True) as panel:
            panel.agent.begin("Read the notes", panel.stop)
            panel.pump()
            assert (panel.view.timing.active, calls) == (True, ["begin"])
            panel.animated = False
            panel.advance(.016)
            assert (panel.view.timing.active, calls) == (False, ["begin", "end"])
            panel.animated = True
            panel.agent.say("A reply.")
            panel.pump()
            assert bool(panel.view.timing.active)
            panel.view.show(focus=True)
            assert not bool(panel.view.timing.active)
            panel.view.resume()
            panel.pump()
            assert bool(panel.view.timing.active)
        assert calls == ["begin", "end"] * 3


def test_measured_previews_end_on_complete_lines_with_mixed_markdown_fonts() -> None:
    with notifications() as panel:
        measure = panel.view.measure
        for source in ("A long wrapped paragraph with Åäö and 👩🏽‍💻. " * 12,
                       "# Heading\n\nA **bold** paragraph.\n\n- An item that wraps across several display lines in a narrow preview.\n"
                       "- Another item.\n\n```python\nprint('hello')\n```\n\nEnd."):
            spans = tuple(formatted(source))
            wanted = measure.load(spans, 350)
            panel.root.update_idletasks()
            boundaries = [0]
            index = "1.0"
            while measure.text.compare(index, "<", "end"):
                line = measure.text.dlineinfo(index)
                assert line is not None
                boundaries.append(line[1] + line[3] + 6)
                index = measure.text.index(f"{index} + 1 displaylines")
            for available in range(1, min(wanted + 1, 500)):
                assert measure.fit_body(spans, 350, available) == max(height for height in boundaries if height <= available)


def test_streamed_unicode_links_and_markup_match_a_complete_render() -> None:
    with notifications() as panel:
        panel.agent.begin("Summarize the notes", panel.stop)
        publish = panel.agent.updates()
        prefix = "Åäö 👩🏽‍💻 e\u0301: "
        for suffix in ("**bold", "**bold**", "**bold** and [docs](https://example.org", "**bold** and [docs](https://example.org).",
                       "**bold** and [docs](https://example.org).\n\n```python\nprint('ok')", "Corrected."):
            text = prefix + suffix
            publish(Answer("stream", text, 1))
            panel.pump()
            reply = panel.view.cards[MessageKey(1)]
            spans = tuple(formatted(text))
            assert reply.text.get("1.0", "end-1c") == "".join(span.text for span in spans)
            assert set(reply.links.values()) == {span.link for span in spans if span.link}


def test_native_notifications_are_idle_until_needed_and_never_take_focus() -> None:
    with notifications() as panel:
        view = panel.view
        assert not view.cards and view.history is None
        foreground = windows.foreground()
        panel.agent.begin("Find my workbook", panel.stop)
        panel.pump()
        work = view.cards[Kind.WORK]
        request = view.cards[MessageKey(0)]
        assert windows.is_visible(work.handle)
        assert work.stop.winfo_manager() == "pack"
        assert cast(str, request.label.cget("text")) == "You"
        assert request.text.get("1.0", "end-1c") == "Find my workbook"
        assert request.width < round(WIDTH * windows.scale(panel.root.winfo_id()))
        assert work.text.winfo_manager() == ""
        assert work.text.get("1.0", "end-1c") == ""
        publish = panel.agent.updates()
        publish(ReasoningSummary("thought", "Checking **recent files**.", 1))
        panel.pump()
        assert view.cards[Kind.WORK] is work
        assert work.text.get("1.0", "end-1c") == ""
        assert view.cards[MessageKey(0)] is request and request.text.get("1.0", "end-1c") == "Find my workbook"
        panel.advance(3600)
        assert work.handle == view.cards[Kind.WORK].handle
        publish(Answer("reply", "Found **budget.xlsx**.", 2, StreamState.COMPLETE))
        panel.agent.done("Found **budget.xlsx**.")
        panel.agent.finish()
        panel.pump()
        assert Kind.WORK not in view.cards and len(view.cards) == 2
        reply = view.cards[MessageKey(2)]
        assert reply.text.tag_ranges("bold")
        assert reply.stop.winfo_manager() == ""
        assert reply.text.bbox("1.0") is not None
        assert reply.height <= reply.maximum and reply.height < 200
        assert not view.animation_timer
        panel.advance(31)
        assert not view.cards and view.expiry_timer is None
        panel.agent.show()
        panel.pump()
        assert not view.cards and view.history is None
        assert "Found **budget.xlsx**." in panel.agent.all_text()
        assert windows.foreground() == foreground


def test_native_stack_moves_up_retargets_and_bounds_windows_even_during_bursts() -> None:
    with notifications(animated=True) as panel:
        view = panel.view
        panel.agent.begin("Find the workbook", panel.stop)
        panel.pump()
        panel.advance(.3)
        request = view.cards[MessageKey(0)]
        work = view.cards[Kind.WORK]
        control_bounds = windows.outer_rect(work.handle)
        original = windows.outer_rect(request.handle)
        panel.agent.say("The first reply.")
        panel.pump()
        assert request.motion is not None and request.motion.end.y < original.top
        panel.now += .08
        current = request.motion.sample(panel.now)
        panel.agent.say("Another reply arrives during movement.")
        panel.pump()
        assert request.motion is not None
        assert request.motion.start == current
        assert windows.outer_rect(work.handle) == control_bounds
        for index in range(12):
            panel.agent.say(f"Reply {index} with **Markdown**.")
            panel.pump()
            assert len(view.cards) <= MAX_VISIBLE
            assert Kind.WORK in view.cards
        panel.advance(.5)
        active_timer = view.animation_timer
        assert active_timer is not None  # the active work's spinner still runs
        assert all(card.motion is None for card in view.cards.values())
        bounds = sorted((windows.outer_rect(card.handle) for card in view.cards.values()), key=top)
        assert view.work is not None
        assert bounds[0].top >= view.work.top
        assert bounds[-1].bottom < view.work.bottom
        assert all(first.bottom < second.top for first, second in pairwise(bounds))
        assert all(card.alpha == 1 for card in view.cards.values())
        panel.animated = False
        panel.agent.finish()
        panel.pump()
        assert view.animation_timer is None and all(card.motion is None for card in view.cards.values())
        panel.advance(31)
        assert not view.cards


def test_spinner_reuses_widgets_pauses_for_questions_and_cleans_up_when_done() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Find **the workbook**", panel.stop)
        panel.pump()
        panel.advance(.3)
        work = panel.view.cards[Kind.WORK]
        items = work.indicator.find_all()
        indicator = cast(IndicatorReading, work.indicator)
        angle = indicator.itemcget("spin", "start")
        with patch.object(Card, "render_text", side_effect=AssertionError("Spinner must not lay out text")):
            panel.now += .1
            panel.view.tick()
            panel.pump()
            publish = panel.agent.updates()
            publish(ReasoningSummary("summary", "A progress paragraph that stays in history.", 1))
            panel.pump()
        assert indicator.itemcget("spin", "start") != angle
        assert work.indicator.find_all() == items
        assert panel.view.cards[MessageKey(0)].text.get("1.0", "end-1c") == "Find **the workbook**"
        panel.animated = False
        panel.view.tick()
        assert panel.view.animation_timer is None
        panel.animated = True
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            panel.advance(.3)
            assert panel.view.animation_timer is None
            assert indicator.itemcget("spin", "state") == "hidden"
            assert panel.agent.reply_by_voice("The workbook on my desktop")
            assert pending.result() == "The workbook on my desktop"
        panel.agent.finish()
        panel.pump()
        panel.advance(.3)
        assert panel.view.animation_timer is None and panel.view.cards
        panel.advance(31)
        panel.advance(.3)
        assert not panel.view.cards


def test_semantic_colors_and_readable_material_fallbacks_follow_live_updates() -> None:
    with notifications() as panel:
        panel.view.material_preference = lambda: appearance.Material.SOLID
        panel.agent.begin("Read the workbook", panel.stop)
        panel.pump()
        work = panel.view.cards[Kind.WORK]
        window = cast(WindowReading, work.window)
        thinking = cast(str, work.label.cget("foreground"))
        assert not work.glass and not window.attributes("-transparentcolor")
        publish = panel.agent.updates()
        publish(ToolActivity("tool", "read_file", "budget.xlsx", 1))
        panel.pump()
        action_card = panel.view.cards[MessageKey(1)]
        action = cast(str, action_card.label.cget("foreground"))
        publish(ToolActivity("tool", "read_file", "budget.xlsx", 2, Failed("Missing file")))
        panel.pump()
        assert panel.view.cards[MessageKey(1)] is action_card
        error = cast(str, action_card.label.cget("foreground"))
        assert len({thinking, action, error}) == 3
        panel.view.material_preference = lambda: appearance.Material.GLASS
        with patch("assistant.agent_toasts.appearance._SetWindowCompositionAttribute", return_value=0):
            panel.view.refresh()
            panel.pump()
            assert not work.glass and not window.attributes("-transparentcolor")
            assert action_card.text.bbox("1.0") is not None
        panel.view.material_preference = lambda: appearance.Contrast("#ffffff", "#000000")
        panel.view.refresh()
        panel.pump()
        assert not work.glass and not window.attributes("-transparentcolor")
        assert cast(str, work.text.cget("background")) == "#ffffff"
        assert cast(str, work.text.cget("foreground")) == "#000000"
        assert cast(str, panel.view.cards[MessageKey(0)].text.cget("foreground")) == "#000000"


def test_hovering_empty_glass_pauses_expiry_and_closing_cancels_pointer_observation() -> None:
    with notifications() as panel, patch("assistant.agent_toasts.appearance.material", return_value=True):
        panel.view.material_preference = lambda: appearance.Material.GLASS
        panel.agent.say("A reply to read.")
        panel.pump()
        card = next(iter(panel.view.cards.values()))
        reading_timer = panel.view.pointer_timer
        assert reading_timer is not None
        bounds = windows.outer_rect(card.handle)
        panel.now += 5
        with patch.object(tk.Misc, "winfo_pointerxy", return_value=(bounds.right - 12, bounds.bottom - 12)):
            panel.view.observe_pointer()
            paused_expiry = panel.view.expiry_timer
            assert paused_expiry is None
            panel.advance(100)
            assert card.key in panel.view.cards
        panel.view.observe_pointer()
        panel.advance(6)
        assert card.key in panel.view.cards
        panel.advance(2)
        assert not panel.view.cards and panel.view.pointer_timer is None
        panel.agent.say("Another reply.")
        panel.pump()
        card = next(iter(panel.view.cards.values()))
        bounds = windows.outer_rect(card.handle)
        expiry = panel.view.expiry_timer
        assert expiry is not None
        panel.root.after_cancel(expiry)
        panel.now += 12
        with patch.object(tk.Misc, "winfo_pointerxy", return_value=(bounds.right - 12, bounds.bottom - 12)):
            panel.view.expire()
            assert card.key in panel.view.cards, "Hovering at the deadline must also keep the reply visible."
        panel.agent.close()
        panel.pump()
        assert panel.view.pointer_timer is None


def test_questions_can_be_answered_or_stopped_by_voice_without_opening_history() -> None:
    with notifications() as panel:
        panel.agent.begin("Open the workbook", panel.stop)
        with asking(panel.agent.inputs, question=True) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            panel.advance(3600)
            question = panel.view.cards[MessageKey(1)]
            assert "Hold N" in cast(str, question.hint.cget("text"))
            assert question.hint.winfo_viewable()
            assert "Open this?" in question.text.get("1.0", "end-1c")
            assert panel.agent.reply_by_voice("The revenue sheet.")
            assert pending.result() == "The revenue sheet."
        panel.pump()
        assert panel.view.history is None
        assert panel.view.cards[MessageKey(1)] is question
        assert panel.view.cards[ResponseKey(1)].text.get("1.0", "end-1c") == "The revenue sheet."
        assert "The revenue sheet." not in question.text.get("1.0", "end-1c")
        panel.requested.clear()
        with asking(panel.agent.inputs) as pending:
            assert panel.requested.wait(timeout=2)
            panel.pump()
            approval = panel.view.cards[MessageKey(2)]
            assert "approve" in cast(str, approval.hint.cget("text"))
            assert approval.hint.winfo_viewable()
            work = panel.view.cards[Kind.WORK]
            work.stop.invoke()
            assert pending.result() is None
        panel.pump()
        assert cast(str, work.stop.cget("state")) == "disabled"
        assert panel.view.history is None


def test_history_keeps_expired_replies_and_returns_to_active_notifications() -> None:
    with notifications() as panel, patch("assistant.agent_panel.windows.focus"):
        panel.agent.begin("Find it", panel.stop)
        panel.agent.say("## Result\n\n" + "A long response with **formatting**. " * 120)
        panel.agent.finish()
        panel.pump()
        reply = panel.view.cards[MessageKey(1)]
        assert reply.height <= reply.maximum
        assert reply.text.get("1.0", "end-1c").count("A long response") == 120
        assert reply.scrollbar.winfo_viewable()
        panel.advance(10000)
        panel.advance(31)
        assert not panel.view.cards
        panel.agent.show(focus=True)
        panel.pump()
        history = panel.view.history
        assert history is not None and windows.is_visible(history.handle)
        assert history.text.get("1.0", "end-1c").count("A long response") == 120
        panel.agent.begin("Continue with that result", panel.stop)
        panel.pump()
        assert not panel.view.cards
        history.hide()
        panel.pump()
        assert not windows.is_visible(history.handle)
        assert Kind.WORK in panel.view.cards
        assert MessageKey(2) in panel.view.cards and MessageKey(0) not in panel.view.cards
        panel.agent.say("The follow-up is done.")
        panel.agent.finish()
        panel.pump()
        follow_up = next(iter(panel.view.cards.values()))
        panel.view.set_hover(follow_up.key, True)
        panel.view.show(focus=True)
        panel.advance(31)
        assert not panel.view.stack.toasts, "Opening history must release a hovered card's paused timer."
        history.hide()
        panel.pump()
        assert not panel.view.cards


def test_recording_has_its_own_stop_and_closing_cleans_up_live_animation_and_expiry() -> None:
    with notifications(animated=True) as panel:
        panel.agent.begin("Find the workbook", panel.stop)
        panel.agent.recording(RecordingState(phase="recording", identity="meeting", seconds=3723, preview="**00:00:00**  First sentence."))
        panel.agent.say("A message with an expiry timer.")
        panel.pump()
        recording = panel.view.cards[Kind.RECORDING]
        assert "01:02:03" in cast(str, recording.label.cget("text"))
        recording.stop.invoke()
        assert panel.recorded == ["stop"] and not panel.agent.state.stopping
        assert panel.view.expiry_timer is None  # the current conversation stays while work runs
        panel.agent.finish()
        panel.pump()
        assert all(timer is not None for timer in (panel.view.animation_timer, panel.view.expiry_timer))
        handles = [card.handle for card in panel.view.cards.values()]
        panel.agent.close()
        panel.pump()
        assert panel.agent.closed_ready.is_set()
        assert not panel.view.cards
        assert (panel.view.animation_timer, panel.view.expiry_timer) == (None, None)
        assert all(not windows.is_visible(handle) for handle in handles)


def test_recording_preview_keeps_the_latest_transcript_visible() -> None:
    with notifications() as panel:
        preview = "**00:00:00**  " + "An earlier sentence. " * 150 + "\n\n**00:15:00**  The newest sentence."
        panel.agent.recording(RecordingState(phase="recording", identity="meeting", seconds=900, preview=preview))
        panel.pump()
        card = panel.view.cards[Kind.RECORDING]
        assert "The newest sentence." in card.text.get("1.0", "end-1c")
        last = card.text.bbox("end-2c")
        assert last is not None and last[1] + last[3] <= card.text.winfo_height()


def test_a_new_recording_returns_during_dismissal_and_repositions_on_work_area_changes() -> None:
    with notifications(animated=True) as panel:
        panel.agent.recording(RecordingState(phase="saved", identity="first", preview="Saved transcript."))
        panel.pump()
        panel.advance(.3)
        card = panel.view.cards[Kind.RECORDING]
        panel.advance(31)
        assert all(item.retiring for item in panel.view.cards.values())
        panel.now += .08
        assert card.motion is not None
        assert 0 < card.motion.sample(panel.now).alpha < 1
        panel.agent.recording(RecordingState(phase="recording", identity="second", preview="New transcript."))
        panel.pump()
        assert panel.view.cards[Kind.RECORDING] is card and not card.retiring
        assert panel.view.cards[Kind.RECORDING].motion is None and card.alpha == 1, "The new recording's Stop control reflows immediately into place."
        panel.advance(.3)
        assert panel.view.work is not None
        work = panel.view.work
        before = windows.outer_rect(card.handle)
        smaller = Rect(work.left + 30, work.top, work.right - 30, work.bottom)
        with patch.object(AgentToasts, "area", return_value=smaller):
            panel.view.refresh()
            panel.pump()
        after = windows.outer_rect(card.handle)
        assert after.right == before.right - 30 and after.top == before.top
