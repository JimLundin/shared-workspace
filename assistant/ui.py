"""All Toolkit windows share one Tk UI thread.

Tk runs on a thread of its own. Every method here may be called from any other
thread; the ones that ask the user something block until answered or cancelled.
Agent work and inline questions remain on worker threads and never wait for painting.

Like `winapi` for ctypes, this module is the boundary to Tk, whose type stubs
leave some values untyped: what it exports is fully typed; inside, some Tk values
aren't, so the "no Any" checks are relaxed here only.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import queue
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from tkinter import ttk
from typing import Final, cast

from miryoku.layout import Layer, Scancode
from winapi import screen, windows
from winapi.shapes import Rect

from .agent_toasts import AgentToasts
from .agent_window import AgentWindow
from .geometry import region
from .keymap import PHYSICAL_NAMES, Cap, Kind, keymap
from .overlay import KeyHighlights, fit_position, load_position, nearest_work_area, save_position
from .recording_state import RecordingAction
from .theme import ACCENT, BACKGROUND, ERROR, FONT, FOREGROUND, MONO, MUTED

__all__ = ["AgentWindow", "Ui"]

type Job = Callable[[tk.Tk], None]

def _bring_forward(window: tk.Toplevel, focus: tk.Widget) -> None:
    """Puts a window in front and gives it the keyboard. Windows only lets the program
    the user is using do that; `windows.focus` gets around it."""
    window.update_idletasks()
    window.deiconify()
    window.lift()
    window.attributes("-topmost", True)
    frame = int(window.wm_frame(), 16)
    threading.Thread(target=lambda: windows.focus(frame), name="bring forward", daemon=True).start()  # never wait here
    window.after(200, lambda: window.attributes("-topmost", False))
    focus.focus_set()


def _near_pointer(window: tk.Toplevel, width: int, height: int) -> None:
    """Centres a window of this size on the mouse, kept on its screen."""
    x, y = window.winfo_pointerxy()
    left = max(window.winfo_vrootx(), x - width // 2)
    top = max(window.winfo_vrooty(), y - height // 3)
    window.geometry(f"{width}x{height}+{left}+{top}")


class Ui:
    __slots__ = (
        "_agent", "_agent_lock", "_agent_waiting", "_indicator", "_jobs", "_keyboard_overlay", "_keymap", "_ready", "_recording", "_root", "_thread",
    )

    def __init__(
        self, *, on_agent_waiting: Callable[[bool], None] | None = None, on_recording: Callable[[RecordingAction], None] | None = None,
    ) -> None:
        self._agent: AgentWindow | None = None
        self._agent_lock = threading.Lock()
        self._agent_waiting = on_agent_waiting
        self._recording = on_recording
        self._indicator: Indicator | None = None
        """Made on first use, on the Tk thread."""
        self._keymap: KeymapWindow | None = None
        self._keyboard_overlay: KeymapOverlay | None = None
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ui", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        root = self._root = tk.Tk()
        root.withdraw()
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        root.after(20, lambda: self._poll(root))
        self._ready.set()
        root.mainloop()

    def _poll(self, root: tk.Tk) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            try:
                job(root)
            except Exception:
                traceback.print_exc()
        root.after(20, lambda: self._poll(root))

    def post(self, job: Job) -> None:
        """Runs a job on the Tk thread, soon; doesn't wait."""
        self._jobs.put(job)

    def _ask[T](self, build: Callable[[tk.Tk, Callable[[T], None]], None]) -> T:
        """Builds a window on the Tk thread and waits for the answer it gives."""
        answers: queue.Queue[tuple[T | None, Exception | None]] = queue.Queue()
        def job(root: tk.Tk) -> None:
            try:
                build(root, lambda value: answers.put((value, None)))
            except Exception as error:
                answers.put((None, error))

        self.post(job)
        answer, error = answers.get()
        if error is not None:
            raise error
        return cast("T", answer)

    # ---- a text box --------------------------------------------------------------------

    def prompt(self, title: str) -> str | None:
        def build(root: tk.Tk, done: Callable[[str | None], None]) -> None:
            window = tk.Toplevel(root, background=BACKGROUND, padx=14, pady=12)
            window.title(title)
            window.resizable(width=True, height=False)
            ttk.Label(window, text=title, background=BACKGROUND, foreground=MUTED, font=FONT).pack(anchor="w")
            entry = tk.Entry(window, font=FONT, background="#2b2e33", foreground=FOREGROUND, insertbackground=FOREGROUND, relief="flat")
            entry.pack(fill="x", pady=(6, 0), ipady=6)
            finished = False

            def finish(value: str | None) -> None:
                nonlocal finished
                if not finished:
                    finished = True
                    window.destroy()
                    done(value)

            entry.bind("<Return>", lambda _: finish(entry.get().strip()))
            entry.bind("<Escape>", lambda _: finish(None))
            window.protocol("WM_DELETE_WINDOW", lambda: finish(None))
            _near_pointer(window, 640, 96)
            _bring_forward(window, entry)

        return self._ask(build)

    # ---- choosing part of the screen ---------------------------------------------------

    def choose_region(self, screen: Rect) -> Rect | None:
        """Darkens the screen; the user drags out an area (Escape or right-click cancels)."""

        def build(root: tk.Tk, done: Callable[[Rect | None], None]) -> None:
            window = tk.Toplevel(root, background="black", cursor="crosshair")
            window.overrideredirect(True)
            window.geometry(f"{screen.width}x{screen.height}+{screen.left}+{screen.top}")
            window.attributes("-alpha", 0.3)
            canvas = tk.Canvas(window, background="black", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            start: list[tuple[int, int]] = []
            box = canvas.create_rectangle(0, 0, 0, 0, outline="white", width=2)
            finished = False

            def finish(area: Rect | None) -> None:
                nonlocal finished
                if not finished:
                    finished = True
                    window.destroy()
                    root.after(150, lambda: done(area))  # let the dimming disappear before the capture

            def press(event: tk.Event[tk.Canvas]) -> None:
                start[:] = [(event.x_root, event.y_root)]

            def drag(event: tk.Event[tk.Canvas]) -> None:
                if start:
                    (x, y) = start[0]
                    canvas.coords(box, x - screen.left, y - screen.top, event.x_root - screen.left, event.y_root - screen.top)

            def release(event: tk.Event[tk.Canvas]) -> None:
                if start:
                    area = region(start[0], (event.x_root, event.y_root))
                    finish(area if area.width > 4 and area.height > 4 else None)

            canvas.bind("<ButtonPress-1>", press)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", release)
            canvas.bind("<ButtonPress-3>", lambda _: finish(None))
            window.bind("<Escape>", lambda _: finish(None))
            _bring_forward(window, canvas)

        return self._ask(build)

    # ---- an answer, with follow-ups ----------------------------------------------------

    def answer(self, title: str, follow_up: Callable[[str], None]) -> AnswerWindow:
        def build(root: tk.Tk, done: Callable[[AnswerWindow], None]) -> None:
            done(AnswerWindow(self, root, title, follow_up))

        return self._ask(build)

    # ---- the key map and the layer indicator ------------------------------------------

    def show_keymap(self) -> None:
        def job(root: tk.Tk) -> None:
            if self._keymap is None or not self._keymap.window.winfo_exists():
                self._keymap = KeymapWindow(root)
            else:
                _bring_forward(self._keymap.window, self._keymap.canvas)

        self.post(job)

    def show_keyboard_overlay(
        self, layer: Layer | None, title: str, work: Rect, *, visible: bool, pressed: frozenset[Scancode] = frozenset(),
    ) -> None:
        """A faded key map that follows the current layer without taking keyboard focus."""
        def job(root: tk.Tk) -> None:
            if self._keyboard_overlay is None:
                if not visible:
                    return
                self._keyboard_overlay = KeymapOverlay(root)
            self._keyboard_overlay.show(layer, title, work, visible=visible, pressed=pressed)

        self.post(job)

    def show_pressed_keys(self, pressed: frozenset[Scancode], *, reset: bool = False) -> None:
        """Update feedback on the Tk thread without opening a hidden overlay."""
        def job(root: tk.Tk) -> None:
            if self._keyboard_overlay is not None:
                self._keyboard_overlay.set_pressed(pressed, reset=reset)

        self.post(job)

    def _overlay(self, root: tk.Tk) -> Indicator:
        if self._indicator is None:
            self._indicator = Indicator(root)
        return self._indicator

    def indicate(self, text: str | None, work: Rect) -> None:
        """What the layer indicator shows from now on (None: nothing), at the bottom of `work`."""
        self.post(lambda root: self._overlay(root).set(text, work))

    def flash(self, text: str, work: Rect, seconds: float = 1.2) -> None:
        """Shows a message in the indicator for a moment."""
        self.post(lambda root: self._overlay(root).flash(text, work, seconds))

    def review(self, summary: str, diff: str) -> bool:
        """Shows a change to the toolkit; True if the user chooses Install."""

        def build(root: tk.Tk, done: Callable[[bool], None]) -> None:
            ReviewWindow(root, summary, diff, done)

        return self._ask(build)

    def agent(
        self, task: str | None, stop: Callable[[], None], *, on_problem: Callable[[str], None] | None = None,
        on_reset: Callable[[], bool] | None = None, on_submit: Callable[[str], None] | None = None,
        focus: bool = False, reveal: bool = True, queued: bool = False,
    ) -> AgentWindow:
        with self._agent_lock:
            if self._agent is None:
                self._agent = AgentWindow(
                    stop, post=lambda job: self.post(lambda root: job()), create_view=lambda agent: AgentToasts(self._root, agent),
                    on_problem=on_problem, on_reset=on_reset, on_submit=on_submit, on_waiting=self._agent_waiting, on_recording=self._recording,
                )
            panel = self._agent
        if task is not None:
            panel.begin(task, stop, queued=queued)
        if reveal:
            panel.show(focus=focus)
        return panel

    def agent_input(
        self, title: str, detail: str = "", *, question: bool = False, cancel: threading.Event | None = None,
    ) -> str | None:
        """Wait on the agent's inline request, without displaying or focusing a window."""
        if self._agent is None:
            return None
        return self._agent.input(title, detail, question=question, cancel=cancel)

    def close_agent(self) -> None:
        if self._agent is not None:
            self._agent.close()
            if threading.current_thread() is not self._thread:
                self._agent.closed_ready.wait(timeout=3)


class _TextWindow:
    """A window with a read-only text area; text is added from any thread."""

    __slots__ = ("text", "ui", "window")

    def __init__(self, ui: Ui, root: tk.Tk, title: str, width: int, height: int) -> None:
        self.ui = ui
        self.window = tk.Toplevel(root, background=BACKGROUND)
        self.window.title(title[:80])
        frame = tk.Frame(self.window, background=BACKGROUND)
        frame.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        scroll = ttk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        self.text = tk.Text(
            frame,
            wrap="word",
            font=FONT,
            background=BACKGROUND,
            foreground=FOREGROUND,
            insertbackground=FOREGROUND,
            relief="flat",
            padx=6,
            pady=6,
            yscrollcommand=scroll.set,
        )
        self.text.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.text.yview)
        self.text.tag_configure("muted", foreground=MUTED)
        self.text.tag_configure("accent", foreground=ACCENT)
        self.text.tag_configure("error", foreground=ERROR)
        self.text.tag_configure("code", font=MONO)
        self.text.bind("<Key>", self._read_only)
        self.window.bind("<Escape>", lambda _: self.window.destroy())
        _near_pointer(self.window, width, height)

    def _read_only(self, event: tk.Event[tk.Text]) -> str | None:
        """The text can be selected and copied, not edited."""
        if isinstance(event.state, int) and event.state & 0x4 and event.keysym.lower() in ("c", "a"):  # Ctrl+C, Ctrl+A
            return None
        return "break" if event.keysym not in ("Up", "Down", "Prior", "Next", "Home", "End") else None

    def add(self, text: str, tag: str = "") -> None:
        def job(_: tk.Tk) -> None:
            if self.window.winfo_exists():
                self.text.insert("end", text, tag)
                self.text.see("end")

        self.ui.post(job)

    def all_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def copy_all(self) -> None:
        self.window.clipboard_clear()
        self.window.clipboard_append(self.all_text())


class AnswerWindow(_TextWindow):
    """An answer as it's written; a box at the bottom asks a follow-up."""

    __slots__ = ("entry", "follow_up")

    def __init__(self, ui: Ui, root: tk.Tk, title: str, follow_up: Callable[[str], None]) -> None:
        super().__init__(ui, root, title, 760, 540)
        self.follow_up = follow_up
        self.add(f"{title}\n\n", "accent")
        bottom = tk.Frame(self.window, background=BACKGROUND)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        self.entry = tk.Entry(bottom, font=FONT, background="#2b2e33", foreground=FOREGROUND, insertbackground=FOREGROUND, relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.configure(state="disabled")
        self.entry.bind("<Return>", self._ask)
        ttk.Button(bottom, text="Copy answer", command=self.copy_all).pack(side="right", padx=(8, 0))
        _bring_forward(self.window, self.text)

    def _ask[T](self, _: tk.Event[tk.Entry]) -> None:
        question = self.entry.get().strip()
        if question:
            self.entry.delete(0, "end")
            self.entry.configure(state="disabled")
            self.text.insert("end", f"\n\n{question}\n\n", "accent")
            self.follow_up(question)

    def write(self, text: str) -> None:
        self.add(text)

    def finish(self) -> None:
        def job(_: tk.Tk) -> None:
            if self.window.winfo_exists():
                self.entry.configure(state="normal")
                self.entry.focus_set()

        self.ui.post(job)

    def fail(self, message: str) -> None:
        self.add(f"\n{message}\n", "error")
        self.finish()



class Indicator:
    """A small label at the bottom of the screen: the layer, caps word, layout on/off.
    It's shown and hidden with Windows calls that never activate it, and it can't be
    clicked, so it never takes the keyboard from what you're typing in."""

    __slots__ = ("_flash", "_frame", "_label", "_steady", "_window")

    def __init__(self, root: tk.Tk) -> None:
        self._window = tk.Toplevel(root, background=BACKGROUND)
        self._window.overrideredirect(True)
        self._window.attributes("-alpha", 0.9)
        self._label = tk.Label(self._window, font=("Segoe UI Semibold", 13), background=BACKGROUND, foreground=FOREGROUND, padx=16, pady=6)
        self._label.pack()
        self._window.update_idletasks()
        self._frame = int(self._window.wm_frame(), 16)
        windows.make_overlay(self._frame)
        windows.show_quietly(self._frame, visible=False)
        self._steady: tuple[str | None, Rect] | None = None
        """What to show when no message is flashing."""
        self._flash: str | None = None
        """The pending end of a flash (a Tk `after` id)."""

    def _show(self, text: str | None, work: Rect, colour: str = FOREGROUND) -> None:
        if text is None:
            windows.show_quietly(self._frame, visible=False)
            return
        self._label.configure(text=text, foreground=colour)
        self._window.update_idletasks()
        width, height = self._label.winfo_reqwidth(), self._label.winfo_reqheight()
        self._window.geometry(f"{width}x{height}+{work.left + (work.width - width) // 2}+{work.bottom - height - 28}")
        self._window.update_idletasks()
        windows.show_quietly(self._frame, visible=True)

    def set(self, text: str | None, work: Rect) -> None:
        self._steady = (text, work)
        if self._flash is None:
            self._show(text, work)

    def flash(self, text: str, work: Rect, seconds: float) -> None:
        if self._flash is not None:
            self._window.after_cancel(self._flash)
        self._show(text, work, ACCENT)
        self._flash = self._window.after(round(seconds * 1000), self._end_flash)

    def _end_flash(self) -> None:
        self._flash = None
        if self._steady is not None:
            self._show(*self._steady)
        else:
            windows.show_quietly(self._frame, visible=False)


_COLOURS: Final = {
    Kind.KEY: ("#2b2e33", FOREGROUND),
    Kind.MOD: ("#21403a", "#8fe3c6"),
    Kind.LAYER: ("#22324d", "#9cc3ff"),
    Kind.TOOL: ("#47381c", "#ffcf7a"),
    Kind.APP: ("#2c3f23", "#b6e39a"),
    Kind.SPECIAL: ("#3b2948", "#d7b4ff"),
    Kind.HELD: (ACCENT, BACKGROUND),
    Kind.BLANK: ("#191b1e", MUTED),
}
_LAYERS: Final[tuple[tuple[str, Layer | None], ...]] = (("Base", None), *((layer.name.title(), layer) for layer in Layer))
_ROW_START: Final = (0.0, 1.5, 0.0, 1.25)
"""Where each row starts, in key widths, as on an ISO keyboard."""
_UNIT, _GAP, _HEIGHT = 64, 5, 56


def _draw_keymap(canvas: tk.Canvas, layer: Layer | None, scale: float = 1.0) -> dict[str, Cap]:
    """Shared drawing for the reference window and the live overlay."""
    canvas.delete("all")
    caps: dict[str, Cap] = {}
    for row_number, (row, start) in enumerate(zip(keymap(layer), _ROW_START, strict=True)):
        x = start * _UNIT * scale
        y = row_number * (_HEIGHT + _GAP) * scale
        for cap in row:
            size = 1.75 if cap.physical == "Caps" else 1.0
            _draw_key(canvas, cap, x, y, (size * _UNIT - _GAP) * scale, scale=scale)
            caps[cap.physical] = cap
            x += size * _UNIT * scale
    return caps


def _draw_key(canvas: tk.Canvas, cap: Cap, x: float, y: float, width: float, *, scale: float) -> None:
    tag = f"key:{cap.physical}"
    height = _HEIGHT * scale
    small = -max(8, round(10 * scale))  # negative font sizes are pixels, like the key geometry
    canvas.create_rectangle(x, y, x + width, y + height, tags=(f"{tag}:background",))
    canvas.create_text(
        x + 5 * scale, y + 4 * scale, text=cap.physical, anchor="nw", font=("Segoe UI", small), tags=(f"{tag}:physical",),
    )
    if cap.label:
        size = 15 if len(cap.label) <= 5 else 12 if len(cap.label) <= 8 else 11 if len(cap.label) <= 10 else 10
        canvas.create_text(
            x + width / 2, y + height / 2 + scale, text=cap.label,
            font=("Segoe UI Semibold", -max(8, round(size * scale))), width=width - 6 * scale, tags=(f"{tag}:label",),
        )
    if cap.hold:
        canvas.create_text(
            x + width / 2, y + height - 9 * scale, text=cap.hold,
            font=("Segoe UI", small), tags=(f"{tag}:hold",),
        )
    _colour_key(canvas, cap, pressed=False)


def _colour_key(canvas: tk.Canvas, cap: Cap, *, pressed: bool) -> None:
    """Recolour existing items, keeping keypresses out of the layout/geometry path."""
    tag = f"key:{cap.physical}"
    fill, ink = ("#ffcf7a", BACKGROUND) if pressed else _COLOURS[cap.kind]
    outline = "#fff0cc" if pressed else "#3a3d42" if cap.kind is Kind.BLANK else fill
    canvas.itemconfigure(f"{tag}:background", fill=fill, outline=outline, width=2 if pressed else 1)
    canvas.itemconfigure(f"{tag}:physical", fill=ink if pressed else MUTED)
    canvas.itemconfigure(f"{tag}:label", fill=ink)
    canvas.itemconfigure(f"{tag}:hold", fill=ink if pressed or cap.kind is Kind.HELD else MUTED)


class KeymapOverlay:
    """A click-through keyboard with a separate, nonactivating drag handle."""

    __slots__ = (
        "_canvas", "_caps", "_drag", "_frame", "_handle", "_handle_frame", "_heading", "_highlight_timer", "_highlighted", "_highlights",
        "_position", "_view", "_window",
    )

    def __init__(self, root: tk.Tk) -> None:
        self._window = tk.Toplevel(root, background=BACKGROUND, padx=8, pady=8)
        self._window.withdraw()
        self._window.title("Toolkit keyboard overlay")
        self._window.overrideredirect(True)
        self._window.attributes("-alpha", 0.45)
        self._heading = tk.Label(self._window, background=BACKGROUND, foreground=FOREGROUND, font=("Segoe UI Semibold", 10))
        self._heading.pack(anchor="w", pady=(0, 6))
        self._canvas = tk.Canvas(self._window, background=BACKGROUND, highlightthickness=0)
        self._canvas.pack()
        self._window.update_idletasks()
        self._frame = int(self._window.wm_frame(), 16)
        windows.make_overlay(self._frame)

        # A click-through window cannot receive mouse events, even on its children.
        self._handle = tk.Toplevel(root, background="#3a3d42", padx=1, pady=1)
        self._handle.withdraw()
        self._handle.title("Toolkit keyboard overlay handle")
        self._handle.overrideredirect(True)
        self._handle.attributes("-alpha", 0.75)
        tk.Label(
            self._handle, text="≡  Drag to move", font=("Segoe UI", 9), cursor="fleur",
            background="#2b2e33", foreground=FOREGROUND, padx=10, pady=4,
        ).pack()
        self._handle.bind("<ButtonPress-1>", self._start_drag)
        self._handle.bind("<B1-Motion>", self._drag_to)
        self._handle.bind("<ButtonRelease-1>", self._end_drag)
        self._handle.update_idletasks()
        self._handle_frame = int(self._handle.wm_frame(), 16)
        windows.make_overlay(self._handle_frame, click_through=False)
        self._position = load_position()
        self._drag: tuple[int, int] | None = None
        self._view: tuple[Layer | None, str, tuple[Rect, ...]] | None = None
        self._caps: dict[str, Cap] = {}
        self._highlights = KeyHighlights()
        self._highlighted: frozenset[str] = frozenset()
        self._highlight_timer: str | None = None

    def show(
        self, layer: Layer | None, title: str, work: Rect, *, visible: bool, pressed: frozenset[Scancode] = frozenset(),
    ) -> None:
        if not visible:
            self._finish_drag()
            self.set_pressed(frozenset(), reset=True)
            windows.show_quietly(self._handle_frame, visible=False)
            windows.show_quietly(self._frame, visible=False)
            self._view = None
            return
        areas = tuple(m.work for m in screen.monitors()) or (work,)
        view = (layer, title, areas)
        if view == self._view:
            self.set_pressed(pressed)
            return
        if self._position is not None:
            work = nearest_work_area(self._position, areas)
        density = max(1.0, self._window.winfo_fpixels("1i") / 96)
        margin = round(84 * density)
        handle_height = self._handle.winfo_reqheight() + 4
        scale = max(
            0.2, min(0.85 * density, (work.width - 32) / (14 * _UNIT), (work.height - margin - handle_height - 64) / (4 * (_HEIGHT + _GAP))),
        )
        self._heading.configure(text=f"Toolkit keyboard · {title}")
        self._canvas.configure(width=round(14 * _UNIT * scale), height=round(4 * (_HEIGHT + _GAP) * scale))
        self._caps = _draw_keymap(self._canvas, layer, scale)
        self._highlighted = frozenset()
        self._view = view
        self.set_pressed(pressed)
        self._window.update_idletasks()
        width, height = self._window.winfo_reqwidth(), self._window.winfo_reqheight()
        if self._position is None:
            self._position = (work.left + work.width // 2, work.bottom - height - handle_height - margin)
        self._window.geometry(f"{width}x{height}")
        self._handle.geometry(f"{self._handle.winfo_reqwidth()}x{self._handle.winfo_reqheight()}")
        self._window.update_idletasks()
        self._move(fit_position(self._position, width, height + handle_height, work))
        windows.show_quietly(self._frame, visible=True)
        windows.show_quietly(self._handle_frame, visible=True)

    def set_pressed(self, pressed: frozenset[Scancode], *, reset: bool = False) -> None:
        if reset:
            self._highlights.clear()
        if self._view is None:
            return
        self._highlights.update(pressed, time.monotonic())
        self._paint_highlights()

    def _paint_highlights(self) -> None:
        if self._highlight_timer is not None:
            self._window.after_cancel(self._highlight_timer)
            self._highlight_timer = None
        now = time.monotonic()
        highlighted = frozenset(PHYSICAL_NAMES[key] for key in self._highlights.visible(now))
        for name in highlighted ^ self._highlighted:
            _colour_key(self._canvas, self._caps[name], pressed=name in highlighted)
        self._highlighted = highlighted
        if (delay := self._highlights.delay(now)) is not None:
            self._highlight_timer = self._window.after(delay, self._expire_highlights)

    def _expire_highlights(self) -> None:
        self._highlight_timer = None
        self._paint_highlights()

    def _move(self, position: tuple[int, int]) -> None:
        self._position = position
        x, y = position
        width, height = self._window.winfo_reqwidth(), self._window.winfo_reqheight()
        handle_width, handle_height = self._handle.winfo_reqwidth(), self._handle.winfo_reqheight()
        left, top = x - width // 2, y + handle_height + 4
        windows.move(self._frame, Rect(left, top, left + width, top + height))
        left = x - handle_width // 2
        windows.move(self._handle_frame, Rect(left, y, left + handle_width, y + handle_height))

    def _start_drag(self, event: tk.Event[tk.Misc]) -> None:
        if self._position is not None and self._view is not None:
            x, y = self._position
            self._drag = (event.x_root - x, event.y_root - y)
            self._handle.grab_set()

    def _drag_to(self, event: tk.Event[tk.Misc]) -> None:
        if self._drag is not None:
            x, y = self._drag
            self._move((event.x_root - x, event.y_root - y))

    def _end_drag(self, _event: tk.Event[tk.Misc]) -> None:
        self._finish_drag()

    def _finish_drag(self) -> None:
        if self._drag is None:
            return
        self._drag = None
        self._handle.grab_release()
        if self._position is not None:
            areas = tuple(m.work for m in screen.monitors())
            if areas:
                work = nearest_work_area(self._position, areas)
                height = self._window.winfo_reqheight() + self._handle.winfo_reqheight() + 4
                self._move(fit_position(self._position, self._window.winfo_reqwidth(), height, work))
            try:
                save_position(self._position)
            except OSError:
                traceback.print_exc()


class KeymapWindow:
    """The keyboard as each layer has it. Buttons (or keys 1-6) switch layer."""

    __slots__ = ("buttons", "canvas", "layer", "window")

    def __init__(self, root: tk.Tk) -> None:
        self.window = tk.Toplevel(root, background=BACKGROUND, padx=14, pady=12)
        self.window.title("Toolkit key map")
        self.window.resizable(width=False, height=False)
        bar = tk.Frame(self.window, background=BACKGROUND)
        bar.pack(fill="x", pady=(0, 10))
        self.buttons: list[tk.Button] = []
        for i, (name, layer) in enumerate(_LAYERS):
            button = tk.Button(bar, text=name, font=FONT, relief="flat", padx=12, pady=3, borderwidth=0, command=self._chooser(layer))
            button.pack(side="left", padx=(0, 6))
            self.buttons.append(button)
            self.window.bind(str(i + 1), self._key_chooser(layer))
        width = round(14 * _UNIT)
        self.canvas = tk.Canvas(self.window, width=width, height=4 * (_HEIGHT + _GAP), background=BACKGROUND, highlightthickness=0)
        self.canvas.pack()
        legend = (
            "Hold a thumb for its layer.  \u00a7: tap on/off, hold to quit.  "
            "Hold B to dictate or N for an agent task.  Keys 1\u20136 switch layer."
        )
        tk.Label(self.window, text=legend, font=("Segoe UI", 9), background=BACKGROUND, foreground=MUTED).pack(anchor="w", pady=(10, 0))
        self.window.bind("<Escape>", lambda _: self.window.destroy())
        self.layer: Layer | None = None
        self.draw(None)
        _near_pointer(self.window, width + 28, 4 * (_HEIGHT + _GAP) + 110)
        _bring_forward(self.window, self.canvas)

    def _chooser(self, layer: Layer | None) -> Callable[[], None]:
        return lambda: self.draw(layer)

    def _key_chooser(self, layer: Layer | None) -> Callable[[tk.Event[tk.Misc]], None]:
        return lambda _: self.draw(layer)

    def draw(self, layer: Layer | None) -> None:
        self.layer = layer
        for button, (_, shown) in zip(self.buttons, _LAYERS, strict=True):
            selected = shown is layer
            button.configure(background=ACCENT if selected else "#2b2e33", foreground=BACKGROUND if selected else FOREGROUND, activebackground=ACCENT)
        _draw_keymap(self.canvas, layer)


class ReviewWindow:
    """A change the agent wants to make to the toolkit: its summary and diff, with
    Install and Cancel. Closing the window cancels."""

    __slots__ = ("done", "finished", "window")

    def __init__(self, root: tk.Tk, summary: str, diff: str, done: Callable[[bool], None]) -> None:
        self.done, self.finished = done, False
        self.window = tk.Toplevel(root, background=BACKGROUND, padx=12, pady=12)
        self.window.title("Change the toolkit?")
        tk.Label(self.window, text=summary, font=("Segoe UI Semibold", 12), background=BACKGROUND, foreground=FOREGROUND, anchor="w").pack(fill="x")
        note = "Approve running this code and its tests with your Windows account, then installing it if the checks pass."
        tk.Label(self.window, text=note, font=("Segoe UI", 9), background=BACKGROUND, foreground=MUTED, anchor="w").pack(fill="x", pady=(2, 8))
        frame = tk.Frame(self.window, background=BACKGROUND)
        frame.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        text = tk.Text(frame, wrap="none", font=MONO, background="#17191c", foreground=FOREGROUND, relief="flat", yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.configure(command=text.yview)
        text.tag_configure("added", foreground="#8fe3a0")
        text.tag_configure("removed", foreground=ERROR)
        text.tag_configure("file", foreground=ACCENT)
        for line in diff.splitlines():
            tag = "file" if line.startswith(("+++", "---", "@@")) else "added" if line.startswith("+") else "removed" if line.startswith("-") else ""
            text.insert("end", line + "\n", tag)
        text.configure(state="disabled")
        buttons = tk.Frame(self.window, background=BACKGROUND)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Run checks and install", command=lambda: self.finish(True)).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=lambda: self.finish(False)).pack(side="right", padx=(0, 8))
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))
        self.window.bind("<Escape>", lambda _: self.finish(False))
        _near_pointer(self.window, 900, 640)
        _bring_forward(self.window, text)

    def finish(self, install: bool) -> None:
        if not self.finished:
            self.finished = True
            self.window.destroy()
            self.done(install)
