"""The assistant's windows, drawn with Tk (part of Python on Windows): a text box,
a screen-area picker, an answer window and the agent's window.

Tk runs on a thread of its own. Every method here may be called from any other
thread; the ones that ask the user something block until they've answered.

Like `winapi` for ctypes, this module is the boundary to Tk, whose type stubs
leave some values untyped: what it exports is fully typed; inside, some Tk values
aren't, so the "no Any" checks are relaxed here only.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

import queue
import threading
import tkinter as tk
import traceback
from collections.abc import Callable
from tkinter import ttk
from typing import Final, TypeVar, cast

from miryoku.layout import Layer
from winapi import windows
from winapi.shapes import Rect

from .geometry import region
from .keymap import Cap, Kind, keymap

T = TypeVar("T")
Job = Callable[[tk.Tk], None]

FONT: Final = ("Segoe UI", 11)
MONO: Final = ("Cascadia Mono", 10)
BACKGROUND, FOREGROUND, MUTED, ACCENT, ERROR = "#1f2124", "#e8e6e3", "#9a9893", "#6aa9ff", "#ff7b72"


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
    __slots__ = ("_indicator", "_jobs", "_keymap", "_ready", "_thread")

    def __init__(self) -> None:
        self._indicator: Indicator | None = None
        """Made on first use, on the Tk thread."""
        self._keymap: KeymapWindow | None = None
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ui", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        root = tk.Tk()
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

    def _ask(self, build: Callable[[tk.Tk, Callable[[T], None]], None]) -> T:
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

    def agent(self, task: str, stop: Callable[[], None]) -> AgentWindow:
        def build(root: tk.Tk, done: Callable[[AgentWindow], None]) -> None:
            done(AgentWindow(self, root, task, stop))

        return self._ask(build)


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

    def _ask(self, _: tk.Event[tk.Entry]) -> None:
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


class AgentWindow(_TextWindow):
    """What the agent is doing, step by step, with a Stop button."""

    __slots__ = ("button",)

    def __init__(self, ui: Ui, root: tk.Tk, task: str, stop: Callable[[], None]) -> None:
        super().__init__(ui, root, f"Agent: {task}", 820, 600)
        self.add(f"{task}\n\n", "accent")
        bottom = tk.Frame(self.window, background=BACKGROUND)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        self.button = ttk.Button(bottom, text="Stop", command=stop)
        self.button.pack(side="right")
        ttk.Button(bottom, text="Copy", command=self.copy_all).pack(side="right", padx=(0, 8))

        def close() -> None:
            stop()
            self.window.destroy()

        self.window.protocol("WM_DELETE_WINDOW", close)
        _bring_forward(self.window, self.text)

    def step(self, text: str) -> None:
        self.add(f"{text}\n", "muted")

    def say(self, text: str) -> None:
        self.add(f"{text}\n\n")

    def problem(self, text: str) -> None:
        self.add(f"{text}\n", "error")

    def done(self, text: str) -> None:
        self.add(f"\n{text}\n", "accent")

        def job(_: tk.Tk) -> None:
            if self.window.winfo_exists():
                self.button.configure(text="Close", command=self.window.destroy)

        self.ui.post(job)


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
            "Hold a thumb for its layer (it's shown highlighted).  \u00a7 tap: layout on/off, hold: quit.  "
            "B: hold to talk.  Keys 1\u20136 switch layer."
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
        self.canvas.delete("all")
        for y, (row, start) in enumerate(zip(keymap(layer), _ROW_START, strict=True)):
            x = start * _UNIT
            for cap in row:
                size = 1.75 if cap.physical == "Caps" else 1.0
                self._key(cap, x, y * (_HEIGHT + _GAP), size * _UNIT - _GAP)
                x += size * _UNIT

    def _key(self, cap: Cap, x: float, y: float, width: float) -> None:
        fill, ink = _COLOURS[cap.kind]
        self.canvas.create_rectangle(x, y, x + width, y + _HEIGHT, fill=fill, outline="#3a3d42" if cap.kind is Kind.BLANK else fill, width=1)
        self.canvas.create_text(x + 5, y + 4, text=cap.physical, anchor="nw", fill=MUTED, font=("Segoe UI", 7))
        if cap.label:
            size = 11 if len(cap.label) <= 5 else 9 if len(cap.label) <= 8 else 8 if len(cap.label) <= 10 else 7
            self.canvas.create_text(x + width / 2, y + _HEIGHT / 2 + 1, text=cap.label, fill=ink, font=("Segoe UI Semibold", size), width=width - 6)
        if cap.hold:
            self.canvas.create_text(x + width / 2, y + _HEIGHT - 9, text=cap.hold, fill=ink if cap.kind is Kind.HELD else MUTED, font=("Segoe UI", 7))


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
