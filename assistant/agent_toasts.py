"""Unobtrusive native agent notifications on the shared Tk thread.

Each glass card is its own nonactivating window: the desktop between cards is
uncovered, including for mouse input. Windows blurs the background; text stays
opaque. Conversation entries keep their identities; a separate compact spinner
and stop icon stay below the conversation while the agent runs.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, replace

from winapi import appearance, screen, windows
from winapi.notification_input import Region, WheelRouter
from winapi.shapes import Rect

from . import notification_reading as reading
from .agent_panel import AgentPanel, button
from .agent_session import Answer, Session, Snapshot, StreamState
from .agent_window import AgentWindow
from .notifications import (
    MAX_VISIBLE,
    REVEAL_SECONDS,
    WIDTH,
    Busy,
    Content,
    Entry,
    Frame,
    Held,
    Key,
    Motion,
    Recording,
    Space,
    Stack,
    Toast,
    Tone,
    Working,
    advance,
    allocate,
    body,
    can_stop,
    heading,
    hint,
    hover,
    next_expiry,
    order_of,
    plain,
    priority,
    reading_hold,
    spinning,
    tone,
)
from .overlay import nearest_work_area
from .panel import PanelPreferences, load_preferences
from .rich_text import Span, changed_tail, formatted
from .theme import BACKGROUND, FONT, FOREGROUND, MUTED, SURFACE
from .tk_text import configure_markdown, span_tags
from .toast_style import GLASS_KEY, INK, SECONDARY, USER, Palette, palette, reveal_ink


@dataclass(frozen=True, slots=True)
class Prepared:
    width: int
    spans: tuple[Span, ...]
    body_height: int
    header_height: int
    label_width: int
    hint: str
    hint_height: int

    @property
    def preferred(self) -> int:
        return self.header_height + self.body_height + self.hint_height + 12


@dataclass(frozen=True, slots=True)
class Layout:
    body_height: int
    hint: str
    hint_height: int
    height: int


@dataclass(frozen=True, slots=True)
class Reveal:
    tag: str
    started: float


def stop_button(parent: tk.Misc, command: Callable[[], object]) -> tk.Button:
    control = button(parent, "\u00d7", command)
    control.configure(font=("Segoe UI", 14), padx=6, pady=0, highlightthickness=0, takefocus=False)
    return control


class Measure:
    """One unshown text widget measures the same Markdown fonts and wrapping.

    The shared Tk root is withdrawn. Only this child changes size to measure;
    visible cards receive their final geometry together after measurement.
    """

    __slots__ = ("hint", "label", "spans", "stop", "styles", "text")

    def __init__(self, root: tk.Tk) -> None:
        self.spans: tuple[Span, ...] = ()
        self.styles: set[str] = set()
        self.text = tk.Text(root, wrap="word", font=FONT, borderwidth=0, highlightthickness=0, padx=0, pady=6)
        configure_markdown(self.text)
        self.label = tk.Label(root, font=("Segoe UI", 9, "bold"))
        self.hint = tk.Label(root, font=("Segoe UI", 9), justify="left")
        self.stop = stop_button(root, lambda: None)

    def load(self, spans: tuple[Span, ...], width: int) -> int:
        if self.spans != spans:
            tail = changed_tail(self.spans, spans)
            self.spans = spans
            self.text.delete(f"1.0 + {tail.offset} chars", "end")
            for span in tail.spans:
                self.text.insert("end", span.text, ("entry", *span_tags(self.text, span, self.styles)))
        if self.text.winfo_width() != width:
            # A tall, unshown viewport lets Tk answer pixel-to-line queries
            # from its display layout without repeatedly walking wrapped text.
            self.text.place(width=width, height=self.text.winfo_screenheight())
            self.text.update_idletasks()
        self.text.yview_moveto(0)
        return int(self.text.tk.call(self.text, "count", "-update", "-ypixels", "1.0", "end")) + 12 if spans else 0

    def footer(self, text: str, width: int) -> int:
        if not text:
            return 0
        self.hint.configure(text=text, wraplength=max(1, width - 32))
        return self.hint.winfo_reqheight() + 8

    def intrinsic_width(self, spans: tuple[Span, ...], maximum: int) -> int:
        """A wrapped line already needs the maximum; measure shorter lines exactly."""
        if not spans:
            return 0
        # Keep word wrapping enabled so Tk can reuse the preceding paragraphs'
        # layout while a long reply streams, instead of reflowing all its text.
        self.load(spans, maximum)
        widest = 0
        lines = int(self.text.index("end-1c").split(".")[0])
        for line in range(1, lines + 1):
            if int(self.text.tk.call(self.text, "count", "-displaylines", f"{line}.0", f"{line}.end")):
                return maximum
            end = int(self.text.tk.call(self.text, "count", "-xpixels", f"{line}.0", f"{line}.end"))
            # Includes the left margin; reserve the largest right margin/cursor.
            widest = max(widest, end + 10)
            if widest >= maximum:
                return maximum
        return widest

    def prepare(self, content: Content, maximum: int, indicator_size: int) -> Prepared:
        text = body(content)
        spans = ((Span(text),) if plain(content) else tuple(formatted(text))) if text else ()
        controls = indicator_size + 8 + (self.stop.winfo_reqwidth() + 8 if can_stop(content) else 0)
        self.label.configure(text=heading(content), wraplength=0)
        footer = hint(content)
        self.hint.configure(text=footer, wraplength=0)
        width = min(maximum, max(32 + controls + self.label.winfo_reqwidth(),
                                 self.hint.winfo_reqwidth() + 32 if footer else 0,
                                 self.intrinsic_width(spans, maximum)))
        body_height = self.load(spans, width)
        label_width = max(1, width - 32 - controls)
        self.label.configure(text=heading(content), wraplength=label_width)
        header_height = 12 + max(self.label.winfo_reqheight(), indicator_size, self.stop.winfo_reqheight() if can_stop(content) else 0)
        return Prepared(width, spans, body_height, header_height, label_width, footer,
                        self.footer(footer, width))

    def fit_body(self, spans: tuple[Span, ...], width: int, available: int) -> int:
        """Use complete display lines, including Markdown's font sizes and spacing."""
        wanted = self.load(spans, width)
        if wanted <= available:
            return wanted
        if available < self.text.winfo_height():
            line = self.text.dlineinfo(f"@0,{max(0, available - 6)}")
            if line is not None:
                used = line[1] - 6
                if line[1] + line[3] <= available - 6:
                    used += line[3]
                return used + 12 if used else 0
        # An unusually tall work area can exceed the hidden viewport. Keep the
        # exact-count fallback for that case, off the normal streaming path.
        low, high = 0, int(self.text.tk.call(self.text, "count", "-displaylines", "1.0", "end"))
        while low < high:
            middle = (low + high + 1) // 2
            pixels = int(self.text.tk.call(self.text, "count", "-ypixels", "1.0", f"1.0 + {middle} displaylines"))
            if pixels + 12 <= available:
                low = middle
            else:
                high = middle - 1
        return int(self.text.tk.call(self.text, "count", "-ypixels", "1.0", f"1.0 + {low} displaylines")) + 12 if low else 0

    def layout(self, prepared: Prepared, width: int, maximum: int) -> Layout:
        footer, footer_height = prepared.hint, prepared.hint_height
        available = max(0, maximum - prepared.header_height - footer_height - 12)
        body_height = self.fit_body(prepared.spans, width, available) if prepared.body_height > available else prepared.body_height
        return Layout(body_height, footer, footer_height, prepared.header_height + body_height + footer_height + 12)

    def close(self) -> None:
        for widget in (self.text, self.label, self.hint, self.stop):
            widget.destroy()


class Card:
    __slots__ = (
        "bounds", "closed", "content", "frame", "glass", "handle", "header", "height", "hint", "hover_timer", "hovered", "indicator", "key", "label",
        "layout", "line_height", "links", "look", "maximum", "motion", "order", "owner", "page_origin", "prepared", "reader",
        "retiring", "reveal_serial", "reveals",
        "right", "scrollbar", "spans", "speed", "stop", "styles", "text", "width", "window",
    )

    def __init__(self, owner: AgentToasts, toast: Toast, width: int) -> None:
        self.owner, self.key, self.order = owner, toast.key, order_of(toast)
        self.content: Content | None = None
        self.prepared: tuple[Content, int, Prepared] | None = None
        self.layout: Layout | None = None
        self.bounds: Rect | None = None
        self.look: tuple[Tone, appearance.Preference] | None = None
        self.glass = False
        self.motion: Motion | None = None
        self.hover_timer: str | None = None
        self.hovered = False
        self.reader: reading.State = reading.Waiting(0, owner.clock() + reading.START_SECONDS)
        self.page_origin = 0.0
        self.speed = 0.0
        self.line_height = int(owner.root.tk.call("font", "metrics", FONT, "-linespace"))
        self.retiring = False
        self.closed = False
        self.width, self.height = width, 1
        self.maximum = 0
        self.right = 0
        self.frame = Frame(0, width, 1, 0)
        self.spans: tuple[Span, ...] = ()
        self.reveals: tuple[Reveal, ...] = ()
        self.reveal_serial = 0
        self.styles: set[str] = set()
        self.links: dict[str, str] = {}
        self.window = tk.Toplevel(owner.root, background=BACKGROUND, highlightthickness=1, highlightbackground="#41454c")
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.title("Toolkit · Agent notification")
        self.window.attributes("-alpha", 0.0)
        self.window.geometry(f"{width}x240")
        self.create_header()
        self.text = tk.Text(
            self.window, wrap="word", font=FONT, background=BACKGROUND, foreground=FOREGROUND, borderwidth=0,
            highlightthickness=0, padx=0, pady=6, state="disabled", cursor="arrow", takefocus=False,
            exportselection=False, selectbackground="#3d5374", selectforeground=FOREGROUND,
        )
        configure_markdown(self.text)
        self.scrollbar = tk.Canvas(self.window, width=3, background=BACKGROUND, borderwidth=0, highlightthickness=0)
        self.scrollbar.create_line(1, 1, 1, 2, width=2, capstyle="round", tags="thumb")
        self.text.configure(yscrollcommand=self.scrolled)
        self.text.tag_raise("sel")
        self.text.tag_bind("link", "<Button-1>", self.open_link)
        self.text.tag_bind("link", "<Enter>", lambda event: self.text.configure(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda event: self.text.configure(cursor="arrow"))
        self.window.bind("<Enter>", self.pointer)
        self.window.bind("<Leave>", self.pointer)
        self.window.bind("<Button-3>", owner.open_menu)
        self.text.bind("<MouseWheel>", self.wheel)
        self.window.bind("<MouseWheel>", self.wheel)
        self.window.update_idletasks()
        self.handle = int(self.window.wm_frame(), 16)
        windows.make_overlay(self.handle, click_through=False)

    @property
    def y(self) -> float:
        return self.frame.y

    @property
    def alpha(self) -> float:
        return self.frame.alpha

    def create_header(self) -> None:
        self.header = tk.Frame(self.window, background=BACKGROUND)
        size = round(14 * windows.scale(self.owner.root.winfo_id()))
        self.indicator = tk.Canvas(self.header, width=size, height=size, background=BACKGROUND, borderwidth=0, highlightthickness=0)
        self.indicator.pack(side="left", padx=(0, 8))
        self.indicator.create_oval(2, 2, size - 2, size - 2, width=2, tags="track")
        self.indicator.create_arc(2, 2, size - 2, size - 2, width=2, style="arc", extent=260, start=90, tags="spin")
        self.indicator.create_oval(size / 2 - 3, size / 2 - 3, size / 2 + 3, size / 2 + 3, outline="", tags="dot")
        self.stop = stop_button(self.header, self.cancel)
        self.label = tk.Label(self.header, font=("Segoe UI", 9, "bold"), background=BACKGROUND, foreground=MUTED, anchor="w")
        self.label.pack(side="left", fill="x", expand=True)
        self.hint = tk.Label(self.window, font=("Segoe UI", 9), background=BACKGROUND, foreground=MUTED, anchor="w", justify="left")

    def cancel(self) -> None:
        match self.content:
            case Working():
                self.owner.agent.stop()
            case Recording() if self.owner.agent.on_recording is not None:
                self.owner.agent.on_recording("stop")
            case _:
                pass

    def pointer(self, _event: tk.Event[tk.Misc]) -> None:
        # Child Enter/Leave events bubble up too. Test the entire card after Tk
        # has settled, so moving to the stop icon doesn't restart its lifetime.
        if not self.closed and self.hover_timer is None:
            self.hover_timer = self.window.after_idle(self.check_pointer)

    def check_pointer(self) -> None:
        self.hover_timer = None
        if self.closed:
            return
        x, y = self.window.winfo_pointerxy()
        bounds = windows.outer_rect(self.handle)
        inside = bounds.left <= x < bounds.right and bounds.top <= y < bounds.bottom
        self.owner.set_hover(self.key, inside)

    def open_link(self, event: tk.Event[tk.Misc]) -> str:
        for tag in self.text.tag_names(f"@{event.x},{event.y}"):
            if url := self.links.get(tag):
                webbrowser.open(url)
                break
        return "break"

    def scrolled(self, first: str | float, last: str | float) -> None:
        height = self.scrollbar.winfo_height()
        start, end = float(first), float(last)
        if height > 2:
            self.scrollbar.coords("thumb", 1, max(1, round(start * height)), 1, min(height - 1, max(5, round(end * height))))

    def wheel(self, event: tk.Event[tk.Misc]) -> str:
        self.scroll_delta(event.delta)
        return "break"

    def scroll_delta(self, delta: int) -> None:
        if not delta or self.closed or self.prepared is None:
            return
        self.text.tk.call(self.text, "yview", "scroll", round(-delta / 120 * self.line_height * 3), "pixels")
        total = max(1, self.prepared[2].body_height - 12)
        self.reader = reading.Paused(self.text.yview()[0] * total)
        self.page_origin = self.reader.offset
        self.owner.set_hover(self.key, True)
        self.owner.tick()

    def overflow(self) -> int:
        if self.prepared is None or self.layout is None:
            return 0
        return max(0, self.prepared[2].body_height - self.layout.body_height)

    def needs_reading(self) -> bool:
        return not self.retiring and not isinstance(self.content, Recording) and self.reader.offset < self.overflow()

    def read(self, now: float, *, paused: bool, smooth: bool) -> bool:
        if isinstance(self.content, Recording) or self.prepared is None or self.layout is None:
            return False
        prepared = self.prepared[2]
        limit = self.overflow()
        self.reader = reading.advance(self.reader, now, limit, self.speed, paused=paused)
        if limit and not paused and self.text.winfo_height() > 1:
            offset = reading.displayed(self.reader.offset, limit, self.layout.body_height, self.line_height,
                                       smooth=smooth, origin=self.page_origin)
            total = max(1, prepared.body_height - 12)
            if round(offset) != round(self.text.yview()[0] * total):
                self.text.yview_moveto(offset / total)
        return not isinstance(self.reader, reading.Finished)

    def render_header(self, content: Content, prepared: Prepared) -> None:
        self.label.configure(text=heading(content), wraplength=prepared.label_width)
        busy = spinning(content)
        for tag in ("track", "spin"):
            self.indicator.itemconfigure(tag, state="normal" if busy else "hidden")
        self.indicator.itemconfigure("dot", state="hidden" if busy else "normal")
        match content:
            case Working(phase=phase):
                self.stop.pack(side="right", padx=(8, 0), before=self.label)
                self.stop.configure(state="disabled" if phase is Busy.STOPPING else "normal")
            case Recording(state) if state.capturing:
                self.stop.pack(side="right", padx=(8, 0), before=self.label)
                self.stop.configure(state="disabled" if state.phase == "stopping" else "normal")
            case _:
                self.stop.pack_forget()

    def apply_style(self, content: Content, preference: appearance.Preference) -> None:
        look = (tone(content), preference)
        if self.look == look:
            return
        self.look = look
        colors = palette(tone(content))
        foreground, secondary, user = INK, SECONDARY, USER
        if isinstance(preference, appearance.Contrast):
            colors = Palette(preference.background, preference.foreground, preference.foreground)
            foreground = secondary = user = preference.foreground
        self.glass = appearance.material(self.handle, colors.surface, colors.edge, glass=preference is appearance.Material.GLASS)
        background = GLASS_KEY if self.glass else colors.surface
        self.window.attributes("-transparentcolor", GLASS_KEY if self.glass else "")
        self.window.configure(background=background, highlightthickness=0 if self.glass else 1, highlightbackground=colors.edge)
        for widget in (self.header, self.indicator, self.label, self.hint, self.text, self.scrollbar):
            widget.configure(background=background)
        self.label.configure(foreground=colors.accent)
        self.hint.configure(foreground=secondary)
        self.text.configure(foreground=foreground, selectforeground=colors.surface if isinstance(preference, appearance.Contrast) else foreground,
                            selectbackground=foreground if isinstance(preference, appearance.Contrast) else "#3d5374")
        self.stop.configure(background=colors.surface, foreground=foreground, activebackground=colors.edge,
                            activeforeground=foreground, disabledforeground=secondary)
        self.indicator.itemconfigure("track", outline=colors.edge)
        self.indicator.itemconfigure("spin", outline=colors.accent)
        self.indicator.itemconfigure("dot", fill=colors.accent)
        self.scrollbar.itemconfigure("thumb", fill=colors.accent)
        for tag in ("quote", "muted", "table_label", "rule"):
            self.text.tag_configure(tag, foreground=secondary)
        self.text.tag_configure("link", foreground=user)
        self.text.tag_configure("code", background=colors.surface)

    def render_text(self, spans: tuple[Span, ...]) -> None:
        if spans == self.spans:
            return
        position = self.text.yview()[0] * int(self.text.tk.call(self.text, "count", "-ypixels", "1.0", "end"))
        tail = changed_tail(self.spans, spans)
        self.spans = spans
        self.text.configure(state="normal")
        self.text.delete(f"1.0 + {tail.offset} chars", "end")
        links = {f"href-{number}": span.link for number, span in enumerate(spans) if span.link}
        for tag in self.links.keys() - links.keys():
            self.text.tag_delete(tag)
        self.links = links
        for number, span in enumerate(tail.spans, start=tail.index):
            link = (f"href-{number}",) if span.link else ()
            self.text.insert("end", span.text, ("entry", *span_tags(self.text, span, self.styles), *link))
        self.text.configure(state="disabled")
        # Tk estimates long wrapped lines until its metrics catch up. Resolve
        # the changed text before restoring a pixel offset, or appending a
        # paragraph can move the reader several screens forward.
        total = int(self.text.tk.call(self.text, "count", "-update", "-ypixels", "1.0", "end"))
        if total:
            self.text.yview_moveto(position / total)

    def reveal_text(self, offset: int, now: float) -> None:
        if offset >= sum(len(span.text) for span in self.spans):
            return
        # At most twelve short-lived tags, even if packets arrive faster than
        # the frame timer. A new packet never dims text that is already visible.
        tag = f"reveal-{self.reveal_serial % 12}"
        self.reveal_serial += 1
        self.text.tag_delete(tag)
        self.reveals = (*(reveal for reveal in self.reveals if reveal.tag != tag), Reveal(tag, now))
        self.text.tag_configure(tag, foreground=reveal_ink(0))
        self.text.tag_add(tag, f"1.0 + {offset} chars", "end-1c")
        self.text.tag_raise("sel")

    def fade_text(self, now: float, *, animate: bool) -> bool:
        pending: list[Reveal] = []
        for reveal in self.reveals:
            progress = (now - reveal.started) / REVEAL_SECONDS
            if progress >= 1 or not animate:
                self.text.tag_delete(reveal.tag)
            else:
                self.text.tag_configure(reveal.tag, foreground=reveal_ink(progress))
                pending.append(reveal)
        self.reveals = tuple(pending)
        return bool(self.reveals)

    def measure(self, content: Content, width: int) -> Prepared:
        if self.prepared is None or self.prepared[:2] != (content, width):
            size = round(14 * windows.scale(self.owner.root.winfo_id()))
            self.prepared = (content, width, self.owner.measure.prepare(content, width, size))
        return self.prepared[2]

    def render(self, content: Content, maximum_width: int, maximum: int, preference: appearance.Preference) -> None:
        prepared = self.measure(content, maximum_width)
        width = prepared.width
        same_size = (width, maximum) == (self.width, self.maximum)
        same_style = self.look == (tone(content), preference)
        if content == self.content and same_size and same_style:
            return
        previous = self.content
        layout = self.owner.measure.layout(prepared, width, maximum)
        self.content, self.width, self.maximum = content, width, maximum
        self.layout = layout
        self.apply_style(content, preference)
        self.render_header(content, prepared)
        if previous is None or body(previous) != body(content) or plain(previous) != plain(content):
            offset = sum(len(span.text) for span in self.spans)
            self.render_text(prepared.spans)
            self.speed = reading.pace("".join(span.text for span in prepared.spans), prepared.body_height, self.line_height)
            match previous, content:
                case Entry(message=Answer()), Entry(message=Answer(state=state)) if state is not StreamState.INTERRUPTED:
                    if self.owner.motion_enabled() and not isinstance(preference, appearance.Contrast):
                        self.reveal_text(offset, self.owner.clock())
                case _:
                    pass
        self.hint.configure(text=layout.hint, wraplength=max(1, width - 32))
        self.height = layout.height
        self.place_content(self.frame.width if previous is not None else width, self.frame.height if previous is not None else self.height)
        if isinstance(content, Recording):
            self.text.see("end")
        elif previous is None:
            self.text.yview_moveto(0)

    def place_content(self, width: float, height: float) -> None:
        if self.layout is None or self.prepared is None:
            return
        layout, prepared = self.layout, self.prepared[2]
        self.header.place(x=16, y=12, width=max(1, round(width) - 32), height=prepared.header_height - 12)
        visible = max(0, min(layout.body_height, round(height) - prepared.header_height - layout.hint_height - 12))
        if visible:
            # Wrap once at the destination width; the moving native bounds
            # reveal it without reflowing the words on every animation frame.
            self.text.place(x=0, y=prepared.header_height, width=self.width, height=visible)
        else:
            self.text.place_forget()
        if prepared.body_height > layout.body_height and visible:
            self.scrollbar.place(x=max(0, round(width) - 5), y=prepared.header_height + 6, width=3, height=max(1, visible - 12))
            self.window.tk.call("raise", self.scrollbar)
        else:
            self.scrollbar.place_forget()
        if layout.hint_height:
            self.hint.place(x=16, y=prepared.header_height + visible, width=max(1, self.width - 32), height=layout.hint_height - 8)
        else:
            self.hint.place_forget()

    def pulse(self, now: float) -> None:
        # Rotate an existing vector item. Never rebuild text or request layout
        # on this timer; the tiny indicators share the UI callback.
        self.indicator.itemconfigure("spin", start=(90 - now * 300) % 360)  # 1.2 seconds per revolution, linear

    def position(self, right: int, frame: Frame) -> None:
        bounds = Rect(right - round(frame.width), round(frame.y), right, round(frame.y + frame.height))
        if self.bounds != bounds:
            if self.bounds is None or self.bounds.height != bounds.height or self.bounds.width != bounds.width:
                self.place_content(bounds.width, bounds.height)
            windows.move(self.handle, bounds)
            self.bounds = bounds
        if self.alpha != frame.alpha:
            self.window.attributes("-alpha", max(0.0, min(1.0, frame.alpha)))
        self.right, self.frame = right, frame

    def target(self, y: float, alpha: float, now: float, *, animate: bool, retarget: bool = False) -> None:
        end = Frame(y, self.width, self.height, alpha)
        if not animate:
            self.motion = None
            self.position(self.right, end)
            return
        if not retarget and self.motion is not None and self.motion.end == end:
            return
        current = self.motion.sample(now) if self.motion is not None else self.frame
        self.motion = Motion(current, end, now) if current != end else None

    def reveal(self) -> None:
        self.window.deiconify()
        windows.show_quietly(self.handle, visible=True)

    def close(self) -> None:
        self.closed = True
        if self.hover_timer is not None:
            self.window.after_cancel(self.hover_timer)
            self.hover_timer = None
        self.window.destroy()


class AgentToasts:
    __slots__ = (
        "agent", "animation_timer", "cards", "clock", "closed", "expiry_timer", "history", "history_visible", "material_preference", "measure",
        "menu", "motion_enabled", "pointer_timer", "preferences", "remember", "root", "scale", "session", "stack", "timing", "wheel", "work",
    )

    def __init__(
        self, root: tk.Tk, agent: AgentWindow, *, preferences: PanelPreferences | None = None, remember: bool = True,
        clock: Callable[[], float] = time.monotonic, motion_enabled: Callable[[], bool] = appearance.animations_enabled,
        material_preference: Callable[[], appearance.Preference] = appearance.preference,
    ) -> None:
        self.root, self.agent, self.clock, self.motion_enabled = root, agent, clock, motion_enabled
        self.material_preference = material_preference
        self.preferences = preferences if preferences is not None else load_preferences()
        self.remember = remember
        self.session = Session()
        self.stack = Stack()
        self.measure = Measure(root)
        self.timing = appearance.TimerResolution()
        self.wheel = WheelRouter()
        self.cards: dict[Key, Card] = {}
        self.history: AgentPanel | None = None
        self.history_visible = False
        self.closed = False
        self.work: Rect | None = None
        self.scale = 0.0
        self.expiry_timer: str | None = None
        self.animation_timer: str | None = None
        self.pointer_timer: str | None = None
        self.menu = tk.Menu(root, tearoff=False, background=SURFACE, foreground=FOREGROUND, font=("Segoe UI", 10))
        self.menu.add_command(label="Agent session", command=lambda: self.show(focus=True))

    def open_menu(self, event: tk.Event[tk.Misc]) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def render(self, state: Snapshot) -> None:
        if self.closed:
            return
        if self.stack.identity != state.identity:
            self.clear_cards()
        self.session = state
        if self.history is not None:
            self.history.render(state)
        self.refresh()

    def refresh(self) -> None:
        if self.closed:
            return
        now = self.clock()
        self.stack = advance(self.stack, self.session, now)
        if not self.history_visible:
            self.sync(now)
        self.schedule_pointer()
        self.schedule_expiry()

    def schedule_expiry(self) -> None:
        if self.expiry_timer is not None:
            self.root.after_cancel(self.expiry_timer)
            self.expiry_timer = None
        if (deadline := next_expiry(self.stack)) is not None:
            self.expiry_timer = self.root.after(max(1, round((deadline - self.clock()) * 1000)), self.expire)

    def expire(self) -> None:
        self.expiry_timer = None
        if not self.closed and self.cards:
            self.observe_pointer()  # include an arrival since the last glass hover sample
        self.refresh()

    def set_hover(self, key: Key, inside: bool) -> None:
        if not self.closed:
            card = self.cards.get(key)
            changed = card is not None and card.hovered != inside
            if card is not None:
                card.hovered = inside
                if changed and card.needs_reading():
                    card.reader = reading.Paused(card.reader.offset)
            updated = hover(self.stack, key, inside, self.clock())
            if updated != self.stack:
                self.stack = updated
                self.schedule_expiry()
            if changed:
                self.tick()

    def schedule_pointer(self) -> None:
        # Color-keyed glass passes mouse events through its empty pixels.
        # Check the card bounds while a reading timer can be paused/resumed.
        watching = not self.closed and (bool(self.wheel.regions) or
                                       any(toast.lifetime is not Held.ACTIVE and toast.key in self.cards and self.cards[toast.key].glass
                                           for toast in self.stack.toasts))
        if watching and self.pointer_timer is None:
            self.pointer_timer = self.root.after(16 if self.wheel.regions else 125, self.poll_pointer)
        elif not watching and self.pointer_timer is not None:
            self.root.after_cancel(self.pointer_timer)
            self.pointer_timer = None

    def observe_pointer(self) -> None:
        x, y = self.root.winfo_pointerxy()
        for card in self.cards.values():
            if card.glass and not card.retiring:
                bounds = windows.outer_rect(card.handle)
                self.set_hover(card.key, bounds.left <= x < bounds.right and bounds.top <= y < bounds.bottom)

    def poll_pointer(self) -> None:
        self.pointer_timer = None
        if not self.closed:
            targets = {card.text.winfo_id(): card for card in self.cards.values() if not card.retiring}
            for event in self.wheel.drain():
                if (card := targets.get(event.target)) is not None:
                    card.scroll_delta(event.delta)
            self.observe_pointer()
            self.schedule_pointer()

    def area(self) -> Rect:
        areas = [monitor.work for monitor in screen.monitors()]
        if self.work is not None:
            return nearest_work_area((self.work.right - 1, self.work.bottom - 1), areas)
        if self.preferences.bounds is not None:
            bounds = self.preferences.bounds
            return nearest_work_area((bounds.left, bounds.top), areas)
        return screen.monitor_of(windows.foreground()).work

    def height_limits(self, width: int, scale: float, available: int) -> dict[Key, int]:
        line_height = int(self.root.tk.call("font", "metrics", FONT, "-displayof", self.measure.text, "-linespace"))
        spaces: list[Space] = []
        for toast in self.stack.toasts:
            prepared = self.cards[toast.key].measure(toast.content, width)
            rank = priority(toast.content)
            preferred = min(prepared.preferred, round((480 if rank == 2 else 360) * scale))
            preview = min(prepared.body_height, line_height * 2 + 16)
            minimum = min(preferred, prepared.header_height + preview + prepared.hint_height + 12)
            spaces.append(Space(minimum, preferred, rank))
        return dict(zip((toast.key for toast in self.stack.toasts), allocate(tuple(spaces), available), strict=True))

    def sync(self, now: float) -> None:
        if not self.cards and not self.stack.toasts:
            self.work = None
            return
        animate = self.motion_enabled()
        preference = self.material_preference()
        work = self.area()
        scale = windows.scale(self.root.winfo_id())
        immediate = self.work is not None and (self.work != work or self.scale != scale)
        self.work, self.scale = work, scale
        margin, gap = round(16 * scale), round(10 * scale)
        width = min(round(WIDTH * scale), max(1, self.work.width - margin * 2))
        desired = {toast.key for toast in self.stack.toasts}
        created: set[Key] = set()
        # Create and measure before changing any visible card's contents/size.
        for toast in self.stack.toasts:
            if toast.key not in self.cards:
                self.cards[toast.key] = Card(self, toast, width)
                created.add(toast.key)
        retiring = sorted((card for key, card in self.cards.items() if key not in desired and not isinstance(card.content, Working)),
                          key=lambda card: card.order, reverse=True)
        reserved = retiring[:max(0, MAX_VISIBLE - len(desired))]
        available = self.work.height - margin * 2 - gap * max(0, len(desired) + len(reserved) - 1) - sum(card.height for card in reserved)
        limits = self.height_limits(width, scale, available)
        self.stack = replace(self.stack, toasts=tuple(toast for toast in self.stack.toasts if limits[toast.key]))
        desired = {toast.key for toast in self.stack.toasts}
        for key, card in tuple(self.cards.items()):
            if key in desired:
                card.retiring = False
            elif not card.retiring:
                card.retiring = True
                card.target(card.y - round(16 * scale), 0, now, animate=animate)
                if not animate or key in created or isinstance(card.content, Working):
                    card.close()
                    del self.cards[key]
        retiring = sorted((card for card in self.cards.values() if card.retiring), key=lambda card: card.order, reverse=True)
        for card in retiring[max(0, MAX_VISIBLE - len(desired)):]:
            card.close()
            del self.cards[card.key]
        for toast in self.stack.toasts:
            current = self.cards[toast.key]
            before = current.width, current.height
            current.render(toast.content, width, limits[toast.key], preference)
            # A recording's controls become usable immediately when it starts.
            immediate = immediate or (toast.key not in created and before != (current.width, current.height)
                                      and isinstance(toast.content, Recording))
            if toast.key in created:
                current.position(self.work.right - margin,
                                 Frame(self.work.bottom - margin - current.height + round(16 * scale), current.width, current.height, 0))
                current.reveal()
            current.order = order_of(toast)
            if current.hover_timer is None:
                current.hover_timer = current.window.after_idle(current.check_pointer)
        self.layout(now, animate=animate and not immediate)
        self.tick()

    def layout(self, now: float, *, animate: bool) -> None:
        if self.work is None:
            return
        scale = windows.scale(self.root.winfo_id())
        margin, gap = round(16 * scale), round(10 * scale)
        bottom = self.work.bottom - margin
        targets: list[tuple[Card, Frame]] = []
        for card in sorted(self.cards.values(), key=lambda card: card.order, reverse=True):
            bottom -= card.height
            right = self.work.right - margin
            if card.right != right:
                card.position(right, card.frame)
            if not card.retiring:
                targets.append((card, Frame(bottom, card.width, card.height, 1)))
            bottom -= gap
        changed = any(target != (card.motion.end if card.motion is not None else card.frame) for card, target in targets)
        # All active rectangles retarget on the same clock. Interpolating top
        # and bottom together preserves the gap between neighboring cards;
        # applying target heights before moving their tops would overlap them.
        for card, target in targets:
            card.target(target.y, target.alpha, now, animate=animate, retarget=changed)

    def tick(self) -> None:
        if self.animation_timer is not None:
            self.root.after_cancel(self.animation_timer)
            self.animation_timer = None
        if self.closed:
            return
        now = self.clock()
        removed = False
        spin = False
        revealing = False
        animate = self.motion_enabled()
        reader = next((card for card in sorted(self.cards.values(), key=lambda card: card.order) if card.needs_reading()), None)
        before = self.stack
        for key, card in tuple(self.cards.items()):
            if card.motion is not None:
                card.position(card.right, card.motion.sample(now) if animate else card.motion.end)
                if card.motion.finished(now) or not animate:
                    card.motion = None
                    if card.retiring:
                        card.close()
                        del self.cards[key]
                        removed = True
            if not card.closed:
                revealing = card.fade_text(now, animate=animate and card.look is not None
                                           and not isinstance(card.look[1], appearance.Contrast)) or revealing
                reading_now = card.read(now, paused=card.hovered or card is not reader, smooth=animate)
                self.stack = reading_hold(self.stack, key, reading_now, now, busy=self.session.busy or self.session.waiting, hovered=card.hovered)
            if not card.closed and not card.retiring and card.content is not None and spinning(card.content):
                card.pulse(now if animate else 0)
                spin = spin or animate
        if removed:
            self.layout(now, animate=self.motion_enabled())
        self.wheel.update(tuple(Region(card.text.winfo_id(), card.bounds) for card in self.cards.values()
                                if not card.retiring and card.bounds is not None and card.overflow()))
        self.schedule_pointer()
        moving = any(card.motion is not None for card in self.cards.values())
        scrolling = reader is not None and not reader.hovered
        sliding = reader is not None and scrolling and animate and isinstance(reader.reader, reading.Scrolling)
        self.timing.update(moving or revealing or sliding)
        if self.stack is not before:
            self.schedule_expiry()
            self.schedule_pointer()
        if moving or revealing or spin or scrolling:
            interval = 1 / 60 if moving or revealing or sliding else .05
            delay = max(1, round((now + interval - self.clock()) * 1000))
            self.animation_timer = self.root.after(delay, self.tick)
        if not self.cards:
            self.work = None

    def show(self, *, focus: bool = False) -> None:
        if self.closed:
            return
        if not focus:
            self.refresh()
            return
        if self.history is None:
            self.history = AgentPanel(self.root, self.agent, preferences=self.preferences, remember=self.remember, on_hide=self.resume)
        self.history_visible = True
        for toast in self.stack.toasts:
            self.stack = reading_hold(self.stack, toast.key, False, self.clock(),
                                      busy=self.session.busy or self.session.waiting, hovered=False)
            self.stack = hover(self.stack, toast.key, False, self.clock())
        self.schedule_expiry()
        self.clear_cards()
        self.history.render(self.session)
        self.history.show(focus=True)

    def resume(self) -> None:
        if self.history is not None:
            self.preferences = self.history.preferences
        self.history_visible = False
        self.refresh()

    def clear_cards(self) -> None:
        self.timing.update(False)
        self.wheel.update(())
        if self.pointer_timer is not None:
            self.root.after_cancel(self.pointer_timer)
            self.pointer_timer = None
        if self.animation_timer is not None:
            self.root.after_cancel(self.animation_timer)
            self.animation_timer = None
        for card in self.cards.values():
            card.close()
        self.cards.clear()
        self.work = None

    def close(self) -> None:
        self.closed = True
        if self.expiry_timer is not None:
            self.root.after_cancel(self.expiry_timer)
            self.expiry_timer = None
        self.clear_cards()
        self.wheel.close()
        self.measure.close()
        if self.history is not None:
            self.history.close()
        self.menu.destroy()
