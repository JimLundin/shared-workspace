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
from collections.abc import Callable
from tkinter import ttk
from typing import Final, TypeVar

from winapi import windows
from winapi.shapes import Rect

from .geometry import region

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
    __slots__ = ("_jobs", "_ready", "_thread")

    def __init__(self) -> None:
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
            job(root)
        root.after(20, lambda: self._poll(root))

    def post(self, job: Job) -> None:
        """Runs a job on the Tk thread, soon; doesn't wait."""
        self._jobs.put(job)

    def _ask(self, build: Callable[[tk.Tk, Callable[[T], None]], None]) -> T:
        """Builds a window on the Tk thread and waits for the answer it gives."""
        answers: queue.Queue[T] = queue.Queue()
        self.post(lambda root: build(root, answers.put))
        return answers.get()

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
