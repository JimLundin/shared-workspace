"""The agent's minimal native panel, on the same Tk thread as every other window."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import threading
import time
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Final, assert_never

from winapi import screen, windows
from winapi.shapes import Rect

from .agent_session import ApprovalControls, Message, ResponseText, Snapshot, presentation
from .agent_window import AgentWindow, log_view
from .overlay import nearest_work_area
from .panel import Edge, PanelPreferences, dock_panel, fit_panel, load_preferences, save_preferences, snap_panel
from .recording_state import RecordingAction, RecordingState, duration, phase_label, source_name
from .rich_text import Span, formatted
from .theme import ACCENT, BACKGROUND, ERROR, FONT, FOREGROUND, MONO, MUTED, SURFACE
from .tk_text import configure_markdown, span_tags

TRANSPARENT: Final = "#010203"


@dataclass(frozen=True, slots=True)
class TextAnchor:
    mark: str
    offset: int


@dataclass(frozen=True, slots=True)
class Selection:
    start: TextAnchor
    end: TextAnchor
    text: str


def button(parent: tk.Misc, text: str, command: Callable[[], object]) -> tk.Button:
    return tk.Button(
        parent, text=text, command=command, font=("Segoe UI", 10), relief="flat", borderwidth=0,
        background=BACKGROUND, foreground=FOREGROUND, activebackground=SURFACE, activeforeground=FOREGROUND,
        disabledforeground=MUTED, highlightthickness=1, highlightbackground=BACKGROUND, highlightcolor=ACCENT,
        padx=8, pady=5, cursor="hand2", takefocus=True,
    )


class Tooltip:
    """Hints stay out of the layout and never take focus."""

    __slots__ = ("text", "timer", "widget", "window")

    def __init__(self, widget: tk.Widget, text: Callable[[], str]) -> None:
        self.widget, self.text = widget, text
        self.window: tk.Toplevel | None = None
        self.timer: str | None = None
        widget.bind("<Enter>", self.enter, add=True)
        widget.bind("<Leave>", self.hide, add=True)
        widget.bind("<ButtonPress>", self.hide, add=True)
        widget.bind("<Destroy>", self.hide, add=True)

    def enter(self, _event: tk.Event[tk.Misc]) -> None:
        self.hide()
        self.timer = self.widget.after(600, self.show)

    def show(self) -> None:
        self.timer = None
        if not (text := self.text()) or not self.widget.winfo_viewable():
            return
        window = self.window = tk.Toplevel(self.widget, background=SURFACE)
        window.withdraw()
        window.overrideredirect(True)
        tk.Label(window, text=text, font=("Segoe UI", 9), background=SURFACE, foreground=FOREGROUND,
                 padx=8, pady=6, wraplength=360, justify="left").pack()
        window.update_idletasks()
        handle = int(window.wm_frame(), 16)
        work = screen.monitor_of(handle).work
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        x = max(work.left, min(self.widget.winfo_rootx(), work.right - width))
        y = min(self.widget.winfo_rooty() + self.widget.winfo_height() + 4, work.bottom - height)
        window.geometry(f"{width}x{height}")
        window.update_idletasks()
        windows.make_overlay(handle)
        windows.move(handle, Rect(x, y, x + width, y + height))
        windows.show_quietly(handle, visible=True)

    def hide(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class AgentPanel:
    """Owns widgets only. The controller owns conversation, requests and callbacks."""

    __slots__ = (
        "activity_count", "activity_text", "agent", "body", "composing", "controls", "corner", "details", "drag", "drag_handle", "floating_height",
        "handle", "identity", "input", "items", "latest", "links", "menu", "on_hide", "options", "pinned", "placeholder", "placement_timer",
        "preferences", "question", "queue", "queued", "recording_bar", "recording_button", "recording_label", "recording_menu",
        "recording_state", "remember_enabled", "reply", "resize", "resize_handle", "scroll", "send",
        "show_activity", "status", "stop", "stopping", "styles", "text", "translucent", "window",
    )

    def __init__(
        self, root: tk.Tk, agent: AgentWindow, *, preferences: PanelPreferences | None = None, remember: bool = True,
        on_hide: Callable[[], None] | None = None,
    ) -> None:
        self.agent = agent
        self.on_hide = on_hide
        self.preferences = preferences if preferences is not None else load_preferences()
        self.remember_enabled = remember
        self.identity = ""
        self.items: list[Message] = []
        self.controls: dict[int, tk.Frame] = {}
        self.links: dict[str, str] = {}
        self.styles: set[str] = set()
        self.queued: tuple[str, ...] = ()
        self.activity_count = 0
        self.stopping = False
        self.composing = False
        self.question = ""
        self.recording_state = RecordingState()
        self.recording_menu: int | None = None
        self.drag: tuple[int, int, Rect, Edge | None] | None = None
        self.resize: tuple[int, int, Rect, bool] | None = None
        self.floating_height = 0
        self.placement_timer: str | None = None
        self.window = tk.Toplevel(root, background=BACKGROUND)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.title("Toolkit · Agent timeline")
        self.window.minsize(300, 360)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Escape>", self.escape)
        self.window.bind("<Configure>", self.configure)
        self.window.bind("<B1-Motion>", self.pointer_moved)
        self.window.bind("<ButtonRelease-1>", self.pointer_released)

        self.build_header()
        self.build_transcript()
        self.build_activity()
        self.build_composer()
        self.build_resize_handles()
        self.window.update_idletasks()
        self.handle = int(self.window.wm_frame(), 16)
        self.floating_height = round(640 * windows.scale(self.handle))
        self.window.attributes("-topmost", self.preferences.pinned)
        self.set_transparency()

    def build_header(self) -> None:
        header = self.drag_handle = tk.Frame(self.window, background=BACKGROUND, cursor="fleur")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        header.columnconfigure(0, weight=1)
        title = tk.Label(header, text="Agent", font=("Segoe UI", 10, "bold"), background=BACKGROUND, foreground=FOREGROUND, cursor="fleur")
        title.grid(row=0, column=0, sticky="ew", padx=4)
        title.configure(anchor="w")
        for handle in (header, title):
            handle.bind("<ButtonPress-1>", self.start_drag)
        Tooltip(title, lambda: "Drag to move · Release near either screen edge to snap")
        self.status = tk.Label(header, font=("Segoe UI", 9), background=BACKGROUND, foreground=MUTED)
        self.status.grid(row=0, column=1, padx=6)
        self.queue = tk.Label(header, font=("Segoe UI", 8), background=BACKGROUND, foreground=MUTED)
        self.queue.grid(row=0, column=2, padx=4)
        Tooltip(self.queue, lambda: "Queued\n\n" + "\n\n".join(self.queued) if self.queued else "")
        self.stop = button(header, "Stop", self.cancel)
        self.stop.grid(row=0, column=3)
        self.stop.grid_remove()
        self.options = tk.Menubutton(
            header, text="⋯", font=("Segoe UI", 13), background=BACKGROUND, foreground=MUTED,
            activebackground=SURFACE, activeforeground=FOREGROUND, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=BACKGROUND, highlightcolor=ACCENT, takefocus=True, padx=8, pady=1,
        )
        self.options.grid(row=0, column=4)
        Tooltip(self.options, lambda: "Session options")
        self.pinned = tk.BooleanVar(self.window, self.preferences.pinned)
        self.translucent = tk.BooleanVar(self.window, self.preferences.translucent)
        self.show_activity = tk.BooleanVar(self.window, False)
        self.menu = tk.Menu(self.options, tearoff=False, font=("Segoe UI", 10), background=SURFACE, foreground=FOREGROUND,
                            activebackground="#3a3d42", activeforeground=FOREGROUND, borderwidth=0)
        self.options.configure(menu=self.menu)
        self.menu.add_command(label="New chat", command=self.new)
        self.menu.add_command(label="Write a message…", command=self.compose)
        self.menu.add_checkbutton(label="Keep on top", variable=self.pinned, command=self.pin)
        self.menu.add_checkbutton(label="Transparent background", variable=self.translucent, command=self.set_transparency)
        self.menu.add_checkbutton(label="Show activity", variable=self.show_activity, command=self.activity)
        self.menu.add_command(label="Hide", accelerator="Esc", command=self.hide)
        if self.agent.on_recording is not None:
            self.menu.add_separator()
            self.menu.add_command(label="Record a meeting", command=self.toggle_recording)
            self.recording_menu = self.menu.index("end")
            self.menu.add_command(label="Record microphone only", command=lambda: self.recording_action("start_microphone"))
            self.menu.add_command(label="Transcripts", command=lambda: self.recording_action("folder"))
        self.options.bind("<Return>", self.open_menu)
        self.options.bind("<space>", self.open_menu)
        self.options.bind("<Down>", self.open_menu)
        self.recording_bar = tk.Frame(header, background=BACKGROUND)
        self.recording_bar.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(2, 0))
        self.recording_bar.columnconfigure(0, weight=1)
        self.recording_label = tk.Label(
            self.recording_bar, font=("Segoe UI", 9), background=BACKGROUND, foreground=ERROR, anchor="w", cursor="hand2",
        )
        self.recording_label.grid(row=0, column=0, sticky="ew", padx=4)
        self.recording_label.bind("<Button-1>", lambda event: self.recording_action("open"))
        Tooltip(self.recording_label, lambda: source_name(self.recording_state.source) + "\nClick to open the full transcript")
        self.recording_button = button(self.recording_bar, "Stop", self.recording_control)
        self.recording_button.grid(row=0, column=1, columnspan=2, sticky="e")
        self.recording_bar.grid_remove()

    def recording_action(self, action: RecordingAction) -> None:
        if self.agent.on_recording is not None:
            self.agent.on_recording(action)

    def toggle_recording(self) -> None:
        self.recording_action("stop" if self.recording_state.capturing else "start")

    def recording_control(self) -> None:
        self.recording_action(self.recording_state.control)

    def render_recording(self, state: RecordingState) -> None:
        self.recording_state = state
        if state.visible:
            self.recording_bar.grid()
        else:
            self.recording_bar.grid_remove()
        label = phase_label(state.phase)
        self.recording_label.configure(text=f"● {label} · {duration(state.seconds)}", foreground=ERROR if state.capturing else MUTED)
        self.recording_button.configure(text=state.control.capitalize(),
                                        state="disabled" if state.phase == "stopping" else "normal")
        if self.recording_menu is not None:
            self.menu.entryconfigure(self.recording_menu, label="Stop recording" if state.capturing else "Record a meeting")
            self.menu.entryconfigure(self.recording_menu + 1, state="disabled" if state.capturing else "normal")

    def build_transcript(self) -> None:
        body = self.body = tk.Frame(self.window, background=BACKGROUND)
        body.grid(row=1, column=0, sticky="nsew", padx=12)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.scroll = tk.Scrollbar(body, orient="vertical", background=SURFACE, troughcolor=BACKGROUND, relief="flat", borderwidth=0)
        self.text = tk.Text(body, wrap="word", font=FONT, background=BACKGROUND, foreground=FOREGROUND, relief="flat",
                            borderwidth=0, padx=4, pady=12, state="disabled", cursor="arrow", exportselection=False,
                            highlightthickness=0, yscrollcommand=self.scrolled, selectbackground="#3d5374", selectforeground=FOREGROUND)
        self.text.grid(row=0, column=0, sticky="nsew")
        self.scroll.configure(command=self.text.yview)
        self.text.tag_configure("bubble", background=BACKGROUND, lmargincolor=BACKGROUND, rmargincolor=BACKGROUND)
        configure_markdown(self.text)
        self.text.tag_configure("stamp", font=("Segoe UI", 8), foreground=MUTED, lmargin1=16, lmargin2=16, rmargin=6, spacing1=8, spacing3=4)
        self.text.tag_configure("user", foreground="#ced3db")
        self.text.tag_configure("error", foreground=ERROR)
        self.text.tag_configure("bubble_end", spacing3=8)
        self.text.tag_raise("sel")
        self.text.tag_bind("link", "<Button-1>", self.open_link)
        self.text.tag_bind("link", "<Enter>", lambda event: self.text.configure(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda event: self.text.configure(cursor="arrow"))
        self.text.bind("<Control-a>", self.select_all)
        self.text.bind("<Tab>", lambda event: self.next_focus(self.text))
        self.latest = button(body, "↓", lambda: self.text.see("end"))
        Tooltip(self.latest, lambda: "Latest message")

    def build_activity(self) -> None:
        self.details = tk.Frame(self.window, background=BACKGROUND, padx=16, pady=8)
        self.details.grid(row=2, column=0, sticky="ew")
        tk.Label(self.details, text="Activity", font=("Segoe UI", 9), background=BACKGROUND, foreground=MUTED).pack(anchor="w")
        self.activity_text = tk.Text(self.details, height=5, wrap="word", font=MONO, background=SURFACE, foreground=MUTED,
                                    relief="flat", borderwidth=0, padx=8, pady=6, state="disabled", exportselection=False)
        self.activity_text.pack(fill="x")
        self.details.grid_remove()

    def build_composer(self) -> None:
        composer = self.reply = tk.Frame(self.window, background=SURFACE, highlightthickness=1, highlightbackground="#41454c")
        composer.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 16))
        composer.grid_remove()
        composer.columnconfigure(0, weight=1)
        entry = tk.Frame(composer, background=SURFACE)
        entry.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=9)
        self.input = tk.Text(entry, height=1, wrap="word", font=FONT, background=SURFACE, foreground=FOREGROUND,
                             insertbackground=FOREGROUND, relief="flat", borderwidth=0, highlightthickness=0,
                             exportselection=False, undo=True, padx=0, pady=2)
        self.input.pack(fill="both", expand=True)
        self.placeholder = tk.Label(entry, text="Message…", font=FONT, background=SURFACE, foreground=MUTED, padx=0, pady=2)
        self.placeholder.bind("<Button-1>", lambda event: self.input.focus_set())
        self.input.bind("<Return>", self.enter)
        self.input.bind("<KP_Enter>", self.enter)
        self.input.bind("<<Modified>>", self.edited)
        self.input.bind("<Configure>", self.resize_input)
        self.input.bind("<FocusIn>", lambda event: composer.configure(highlightbackground=ACCENT))
        self.input.bind("<FocusOut>", lambda event: composer.configure(highlightbackground="#41454c"))
        self.input.bind("<Tab>", lambda event: self.next_focus(self.input))
        self.input.bind("<Control-a>", self.select_input)
        self.send = button(composer, "Send", self.submit)
        self.send.configure(background=SURFACE, highlightbackground=SURFACE)
        self.send.grid(row=0, column=1, sticky="s", padx=7, pady=6)
        Tooltip(self.send, lambda: "Enter to send · Shift+Enter for a new line")
        self.resize_input()

    def build_resize_handles(self) -> None:
        self.resize_handle = tk.Frame(self.window, background=BACKGROUND, cursor="sb_h_double_arrow")
        self.corner = tk.Frame(self.window, background=SURFACE, cursor="sizing")
        self.resize_handle.bind("<ButtonPress-1>", self.start_resize)
        self.corner.bind("<ButtonPress-1>", lambda event: self.start_resize(event, corner=True))
        self.position_handles()

    def position_handles(self) -> None:
        left = self.preferences.edge == "right"
        self.resize_handle.place(relx=0 if left else 1, x=0 if left else -5, rely=.5, width=5, height=48)
        if self.preferences.edge is None:
            self.corner.place(relx=1, rely=1, anchor="se", width=10, height=10)
        else:
            self.corner.place_forget()

    def cancel(self) -> None:
        self.agent.stop()

    def next_focus(self, widget: tk.Widget) -> str:
        if target := widget.tk_focusNext():
            target.focus_set()
        return "break"

    def select_all(self, _event: tk.Event[tk.Misc]) -> str:
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def select_input(self, _event: tk.Event[tk.Misc]) -> str:
        self.input.tag_add("sel", "1.0", "end-1c")
        return "break"

    def open_menu(self, _event: tk.Event[tk.Misc]) -> str:
        self.menu.tk_popup(self.options.winfo_rootx(), self.options.winfo_rooty() + self.options.winfo_height())
        return "break"

    def escape(self, _event: tk.Event[tk.Misc]) -> str:
        if self.drag is not None or self.resize is not None:
            self.cancel_interaction()
        elif self.composing and not self.question:
            self.composing = False
            self.reply.grid_remove()
            self.text.focus_set()
        else:
            self.hide()
        return "break"

    def open_link(self, event: tk.Event[tk.Misc]) -> None:
        for tag in self.text.tag_names(f"@{event.x},{event.y}"):
            if target := self.links.get(tag):
                webbrowser.open(target)
                break

    def enter(self, event: tk.Event[tk.Misc]) -> str | None:
        if isinstance(event.state, int) and event.state & 1:
            return None
        self.submit()
        return "break"

    def submit(self) -> None:
        if not (self.question or self.composing):
            return
        try:
            if self.agent.submit(self.input.get("1.0", "end-1c")):
                self.input.delete("1.0", "end")
                self.input.edit_reset()
                self.composing = False
                if not self.question:
                    self.reply.grid_remove()
                    self.text.focus_set()
        except Exception:
            log_view(traceback.format_exc())
            self.agent.problem("Couldn't send that message. Your draft is still in the message box.")
        self.resize_input()

    def compose(self) -> None:
        self.composing = not bool(self.question)
        self.reply.grid()
        self.input.focus_set()

    def update_reply(self, state: Snapshot) -> None:
        question = state.question.request_id if state.question is not None else ""
        if self.question and question != self.question:
            self.input.delete("1.0", "end")
            self.input.edit_reset()
        self.question = question
        self.placeholder.configure(text="Reply…" if question else "Message…")
        if question or self.composing:
            self.reply.grid()
        else:
            self.reply.grid_remove()

    def edited(self, _event: tk.Event[tk.Misc]) -> None:
        if self.input.edit_modified():
            self.input.edit_modified(False)
            self.resize_input()

    def resize_input(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        count = self.input.count("1.0", "end-1c", "displaylines")
        lines = max(1, min(6, (count[0] if count else 0) + 1))
        if int(self.input.cget("height")) != lines:
            self.input.configure(height=lines)
        text = self.input.get("1.0", "end-1c")
        self.send.configure(state="normal" if text.strip() and not self.stopping else "disabled")
        if text:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=0, y=0)

    def new(self) -> None:
        if self.agent.new():
            self.input.delete("1.0", "end")
            self.input.edit_reset()
            self.composing = False
            self.reply.grid_remove()
            self.text.focus_set()

    def scrolled(self, first: str | float, last: str | float) -> None:
        first, last = float(first), float(last)
        self.scroll.set(first, last)
        if float(first) > 0 or float(last) < 1:
            self.scroll.grid(row=0, column=1, sticky="ns")
        else:
            self.scroll.grid_remove()
        if float(last) < .995:
            self.latest.place(relx=1, rely=1, anchor="se", x=-18, y=-6)
        else:
            self.latest.place_forget()

    def paint(self, index: int, message: Message) -> None:
        start, end = f"message-{index}", f"message-{index}-end"
        if index < len(self.items):
            if control := self.controls.pop(index, None):
                control.destroy()
            self.text.delete(start, end)
            for tag in tuple(self.links):
                if tag.startswith(f"href-{index}-"):
                    self.links.pop(tag)
                    self.text.tag_delete(tag)
        else:
            self.text.mark_set(start, "end-1c")
            self.text.mark_gravity(start, "left")
        following = f"message-{index + 1}"
        if index + 1 < len(self.items):
            self.text.mark_gravity(following, "right")
        self.text.mark_set("writing", start)
        self.text.mark_gravity("writing", "right")
        shown = presentation(message)
        tags = ("user",) if shown.style == "plain" else ("error",) if shown.style == "error" else ()
        muted = ("muted",) if shown.muted else ()
        timestamp = time.strftime("%H:%M", time.localtime(message.created_at))
        self.text.insert("writing", f"●  {shown.role}   {timestamp}\n", ("stamp",))
        spans = (Span(message.text),) if tags else formatted(message.text)
        for number, span in enumerate(spans):
            link: tuple[str, ...] = ()
            if span.link:
                name = f"href-{index}-{number}"
                self.links[name] = span.link
                link = (name,)
            self.text.insert("writing", span.text, ("entry", *tags, *muted, *span_tags(self.text, span, self.styles), *link))
        if shown.detail:
            detail = Span("\n" + shown.detail, ("code", "muted"))
            self.text.insert("writing", detail.text, ("entry", *span_tags(self.text, detail, self.styles)))
        match shown.response:
            case ResponseText(text):
                self.text.insert("writing", "\n\n" + text, ("entry", "muted"))
            case ApprovalControls(identity):
                self.text.insert("writing", "\n")
                frame = self.controls[index] = tk.Frame(self.text, background=BACKGROUND)
                for answer, label in (("approve", "Approve"), ("decline", "Decline")):
                    button(frame, label, partial(self.respond, index, identity, answer)).pack(side="left", padx=(0, 8))
                self.text.window_create("writing", window=frame)
            case None:
                pass
            case _:
                assert_never(shown.response)
        self.finish_bubble(start)
        self.text.mark_set(end, "writing")
        self.text.mark_gravity(end, "left")
        if index + 1 < len(self.items):
            self.text.mark_gravity(following, "left")

    def finish_bubble(self, start: str) -> None:
        position = self.text.index("writing")
        if self.text.get(f"{position}-1c", "writing") != "\n":
            self.text.insert("writing", "\n", ())
        position = self.text.index("writing")
        self.text.tag_add("bubble_end", f"{position}-1c linestart", "writing")
        # Include line endings and margins so the entire card stays opaque,
        # including wrapped lines and embedded controls. Only its gap is clear.
        self.text.tag_add("bubble", start, "writing")
        self.text.insert("writing", "\n", ())

    def respond(self, index: int, identity: str, answer: str) -> None:
        if self.agent.inputs.respond(identity, answer) and (frame := self.controls.get(index)) is not None:
            for child in frame.winfo_children():
                if isinstance(child, tk.Button):
                    child.configure(state="disabled")

    def selection_anchor(self, position: str) -> TextAnchor:
        for index in range(len(self.items) - 1, -1, -1):
            mark = f"message-{index}"
            if self.text.compare(position, ">=", mark):
                offset = self.text.count(mark, position, "chars")
                return TextAnchor(mark, offset[0] if offset else 0)
        return TextAnchor(self.text.index(position), 0)

    def anchored_index(self, anchor: TextAnchor) -> str:
        return f"{self.text.index(anchor.mark)}+{anchor.offset}c"

    def selection(self) -> Selection | None:
        if not self.text.tag_ranges("sel"):
            return None
        return Selection(self.selection_anchor("sel.first"), self.selection_anchor("sel.last"), self.text.get("sel.first", "sel.last"))

    def restore_selection(self, selected: Selection | None) -> None:
        if selected is not None:
            # Repainting a live row removes Tk's selection tag even when its text
            # only grew. Restore the same text relative to the row's stable mark.
            start, end = self.anchored_index(selected.start), self.anchored_index(selected.end)
            if self.text.get(start, end) == selected.text:
                self.text.tag_remove("sel", "1.0", "end")
                self.text.tag_add("sel", start, end)

    def render(self, state: Snapshot) -> None:
        self.render_recording(state.recording)
        selected = self.selection()
        follow = self.text.yview()[1] >= .995 and selected is None
        self.text.configure(state="normal")
        if self.identity != state.identity:
            for control in self.controls.values():
                control.destroy()
            self.controls.clear()
            self.text.delete("1.0", "end")
            for tag in self.links:
                self.text.tag_delete(tag)
            self.links.clear()
            self.items.clear()
            self.identity = state.identity
            self.activity_count = 0
            self.activity_text.configure(state="normal")
            self.activity_text.delete("1.0", "end")
            self.activity_text.configure(state="disabled")
            selected = None
            follow = True
        for index, message in enumerate(state.messages):
            if index >= len(self.items):
                self.paint(index, message)
                self.items.append(message)
            elif self.items[index] != message:
                self.paint(index, message)
                self.items[index] = message
        self.restore_selection(selected)
        self.text.configure(state="disabled")
        status = state.status if state.busy or state.waiting or state.status == "Needs attention" else ""
        if status == "Waiting for your reply":
            status = "Your reply"
        elif status == "Needs your approval":
            status = "Approval"
        self.status.configure(text=status, foreground="#efce8b" if state.waiting else MUTED)
        self.queued = state.queued
        self.queue.configure(text=f"+{len(state.queued)}" if state.queued else "")
        if state.busy:
            self.stop.grid()
        else:
            self.stop.grid_remove()
        self.stop.configure(state="disabled" if state.stopping else "normal")
        self.stopping = state.stopping
        self.menu.entryconfigure(0, state="disabled" if state.busy else "normal")
        self.menu.entryconfigure(1, state="disabled" if state.stopping else "normal")
        self.update_reply(state)
        self.resize_input()
        self.render_activity(state.activity)
        if follow:
            self.window.update_idletasks()
            self.text.see("end")

    def render_activity(self, entries: tuple[str, ...]) -> None:
        if len(entries) <= self.activity_count:
            return
        follow = self.activity_text.yview()[1] >= .995
        self.activity_text.configure(state="normal")
        for text in entries[self.activity_count:]:
            self.activity_text.insert("end", text + "\n\n")
        self.activity_text.configure(state="disabled")
        self.activity_count = len(entries)
        if follow:
            self.activity_text.see("end")

    def activity(self) -> None:
        if self.show_activity.get():
            self.details.grid()
        else:
            self.details.grid_remove()

    def pin(self) -> None:
        self.preferences = replace(self.preferences, pinned=self.pinned.get())
        self.window.attributes("-topmost", self.preferences.pinned)
        self.remember()

    def set_transparency(self) -> None:
        translucent = self.translucent.get()
        # Windows color-keying clears the canvas without fading text or cards.
        background = TRANSPARENT if translucent else BACKGROUND
        self.window.attributes("-alpha", 1.0)
        self.window.attributes("-transparentcolor", TRANSPARENT if translucent else "")
        self.window.configure(background=background)
        self.body.configure(background=background)
        self.text.configure(background=background)
        self.scroll.configure(troughcolor=background)
        if translucent != self.preferences.translucent:
            self.preferences = replace(self.preferences, translucent=translucent)
            self.remember()

    def work_area(self, point: tuple[int, int]) -> Rect:
        return nearest_work_area(point, [monitor.work for monitor in screen.monitors()])

    def move_to(self, bounds: Rect) -> None:
        if windows.client_size(self.handle) != (bounds.width, bounds.height):
            self.window.geometry(f"{bounds.width}x{bounds.height}")
            self.window.update_idletasks()
        windows.move(self.handle, bounds)

    def pointer_moved(self, event: tk.Event[tk.Misc]) -> str | None:
        if self.drag is not None:
            self.drag_to(event)
        elif self.resize is not None:
            self.resize_to(event)
        else:
            return None
        return "break"

    def pointer_released(self, event: tk.Event[tk.Misc]) -> str | None:
        if self.drag is not None:
            self.end_drag(event)
        elif self.resize is not None:
            self.end_resize(event)
        else:
            return None
        return "break"

    def start_drag(self, event: tk.Event[tk.Misc]) -> None:
        self.cancel_interaction()
        self.drag = (event.x_root, event.y_root, windows.outer_rect(self.handle), self.preferences.edge)
        self.window.grab_set()

    def drag_to(self, event: tk.Event[tk.Misc]) -> None:
        if self.drag is None:
            return
        x, y, bounds, edge = self.drag
        dx, dy = event.x_root - x, event.y_root - y
        if abs(dx) + abs(dy) < 4:
            return
        height = self.floating_height if edge is not None else bounds.height
        if self.preferences.edge is not None:
            self.preferences = replace(self.preferences, edge=None)
            self.position_handles()
        self.move_to(Rect(bounds.left + dx, bounds.top + dy, bounds.right + dx, bounds.top + dy + height))

    def end_drag(self, event: tk.Event[tk.Misc]) -> None:
        if self.drag is None:
            return
        self.drag_to(event)
        origin = self.drag[2]
        self.drag = None
        self.window.grab_release()
        bounds = windows.outer_rect(self.handle)
        if bounds != origin:
            scale = windows.scale(self.handle)
            self.floating_height = bounds.height
            target, edge = snap_panel(
                bounds, self.work_area((event.x_root, event.y_root)), (round(300 * scale), round(360 * scale)), distance=round(28 * scale),
            )
            self.preferences = replace(self.preferences, edge=edge)
            self.move_to(target)
            self.position_handles()
        self.remember()

    def start_resize(self, event: tk.Event[tk.Misc], *, corner: bool = False) -> None:
        self.cancel_interaction()
        self.resize = (event.x_root, event.y_root, windows.outer_rect(self.handle), corner)
        self.window.grab_set()

    def resize_to(self, event: tk.Event[tk.Misc]) -> None:
        if self.resize is None:
            return
        x, y, bounds, corner = self.resize
        scale = windows.scale(self.handle)
        work = self.work_area((bounds.left + bounds.width // 2, bounds.top + 1))
        left = self.preferences.edge == "right"
        width = bounds.width + (x - event.x_root if left else event.x_root - x)
        width = min(max(round(300 * scale), width), round(640 * scale), work.width)
        if self.preferences.edge is not None:
            target = dock_panel(width, work, self.preferences.edge)
        else:
            height = bounds.height + event.y_root - y if corner else bounds.height
            height = min(max(round(360 * scale), height), max(1, work.bottom - bounds.top))
            width = min(width, max(1, work.right - bounds.left))
            target = Rect(bounds.left, bounds.top, bounds.left + width, bounds.top + height)
        self.move_to(target)

    def end_resize(self, event: tk.Event[tk.Misc]) -> None:
        if self.resize is None:
            return
        self.resize_to(event)
        self.resize = None
        self.window.grab_release()
        if self.preferences.edge is None:
            self.floating_height = windows.outer_rect(self.handle).height
        self.remember()

    def cancel_interaction(self) -> None:
        if self.drag is not None:
            bounds, edge = self.drag[2:]
            self.drag = None
            self.preferences = replace(self.preferences, edge=edge)
            self.move_to(bounds)
            self.position_handles()
        elif self.resize is not None:
            bounds = self.resize[2]
            self.resize = None
            self.move_to(bounds)
        else:
            return
        self.window.grab_release()

    def place(self) -> None:
        areas = [monitor.work for monitor in screen.monitors()]
        bounds = self.preferences.bounds
        position = (bounds.left, bounds.top) if bounds else (areas[-1].right, areas[-1].top)
        work = nearest_work_area(position, areas)
        scale = windows.scale(self.handle)
        self.window.minsize(round(300 * scale), round(360 * scale))
        if self.preferences.edge is not None:
            bounds = dock_panel(round(self.preferences.rail_width * scale), work, self.preferences.edge)
        else:
            if bounds is None:
                width = round(self.preferences.rail_width * scale)
                bounds = Rect(work.right - width - 16, work.top + 16, work.right - 16, work.top + 16 + self.floating_height)
            bounds = fit_panel(bounds, work, (round(300 * scale), round(360 * scale)), margin=8, title_height=0)
            self.floating_height = bounds.height
        self.move_to(bounds)

    def show(self, *, focus: bool = False) -> None:
        if not windows.is_visible(self.handle) and not windows.is_maximized(self.handle):
            self.place()
        windows.allow_activation(self.handle, False)
        try:
            self.window.deiconify()
            self.window.update_idletasks()
            windows.show_quietly(self.handle, visible=True, topmost=self.preferences.pinned)
        finally:
            windows.allow_activation(self.handle, True)
        if focus:
            threading.Thread(target=lambda: windows.focus(self.handle), name="focus agent", daemon=True).start()
            (self.input if self.question or self.composing else self.text).focus_set()

    def hide(self) -> None:
        self.cancel_interaction()
        self.remember()
        self.window.withdraw()
        if self.on_hide is not None:
            self.on_hide()

    def configure(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self.window and self.drag is None and self.resize is None:
            if self.placement_timer is not None:
                self.window.after_cancel(self.placement_timer)
            self.placement_timer = self.window.after(350, self.remember)

    def remember(self) -> None:
        if self.placement_timer is not None:
            self.window.after_cancel(self.placement_timer)
            self.placement_timer = None
        if self.drag is not None or self.resize is not None:
            return
        if windows.is_visible(self.handle) and not windows.is_maximized(self.handle) and not windows.is_minimized(self.handle):
            outer = windows.outer_rect(self.handle)
            self.preferences = replace(
                self.preferences, bounds=outer, rail_width=min(640, max(300, round(outer.width / windows.scale(self.handle)))),
            )
        if not self.remember_enabled:
            return
        try:
            save_preferences(self.preferences)
        except OSError:
            log_view(traceback.format_exc())

    def close(self) -> None:
        self.cancel_interaction()
        self.remember()
        self.window.destroy()
