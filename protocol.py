"""How the toolkit's two programs talk. The layout (miryoku) owns the keyboard;
the assistant does everything slow. Keys on the layout send the assistant
requests, as window messages: they are queued, ordered, cost the layout nothing
to send, and need no admin rights. Plain Python: importable anywhere.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

# The keeper owns these (it stops the programs); re-exported here for the programs.
from keeper import ASSISTANT_WINDOW as ASSISTANT_WINDOW  # noqa: PLC0414 (an explicit re-export)
from keeper import AUTOMATION_WINDOW as AUTOMATION_WINDOW  # noqa: PLC0414 (an explicit re-export)
from keeper import LAYOUT_WINDOW as LAYOUT_WINDOW  # noqa: PLC0414 (an explicit re-export)
from keeper import WM_QUIT_LAYOUT as WM_QUIT_LAYOUT  # noqa: PLC0414 (an explicit re-export)

LAYOUT_MAGIC: Final = 0x4D49_5250
"""Layout output: bypass remapping without invalidating its own snippet history."""

MAGIC: Final = 0x4D49_524F
"""Tags input the toolkit sends, so the layout's hook lets it through untouched."""


_WM_APP: Final = 0x8000
WM_REQUEST: Final = _WM_APP + 1
"""Layout -> assistant: a key went down (lparam 1) or up (lparam 0); wparam is `encode(request)`."""
WM_LAYOUT_STATE: Final = _WM_APP + 2
"""Layout -> assistant: wparam is a `LayoutState` value."""
WM_INDICATOR: Final = _WM_APP + 4
"""Layout -> assistant: what the layer indicator shows; wparam is `encode_indicator(...)`."""
WM_TOGGLE_LAYOUT: Final = _WM_APP + 5
"""Assistant -> layout: toggle on/off, as if the escape key was tapped."""
WM_QUERY_LAYOUT_STATE: Final = _WM_APP + 6
"""Assistant -> layout: reply with the layout, layer and physically held keys."""
WM_KEY_STATE: Final = _WM_APP + 7
"""Layout -> assistant: physical scancode in wparam, down/up in lparam (1/0).
Scancode 0 clears the held keys, before a state replay or when the layout stops."""


class LayoutState(Enum):
    OFF = 0
    """Switched off with the escape key or tray toggle: the keyboard is plain."""
    ON = 1
    QUIT = 2


class Tool(Enum):
    """What a tool key asks the assistant to do. Hold-to-talk tools use the key's
    press and release; the rest act on press."""

    DICTATE = 1
    """Hold and speak: the words are typed as said."""
    DICTATE_CLEAN = 2
    """Hold and speak: typed tidied up (no filler words, proper punctuation)."""
    DICTATE_COMMAND = 3
    """Hold and describe a command: the PowerShell command is typed, not run."""
    REWRITE_VOICE = 10
    """Hold and say how to change the selected text."""
    FIX = 11
    """Fix spelling and grammar in the selected text."""
    TRANSLATE = 12
    """Translate the selected text between Swedish and English."""
    SHORTER = 13
    """Make the selected text shorter."""
    FORMAL = 14
    """Make the selected text more formal."""
    ASK = 20
    """Ask a question in a box (with the selected text, if any); the answer is shown."""
    SCREEN_TEXT = 21
    """Drag over part of the screen: its text is copied."""
    SCREEN_ASK = 22
    """Drag over part of the screen, then ask about it."""
    AGENT_VOICE = 30
    """Hold and say a task for the agent."""
    AGENT_TYPED = 31
    """Type a task for the agent in a box."""
    STOP = 32
    """Stop the agent."""
    SNAP_LEFT = 40
    SNAP_RIGHT = 41
    MAXIMIZE = 42
    """Maximize the window, or restore it if it already is."""
    MINIMIZE = 43
    NEXT_MONITOR = 44
    """Move the window to the next monitor, same place relative to the screen."""


@dataclass(frozen=True, slots=True)
class App:
    """A program an app key brings to the front, starting it if it isn't running.
    Pressed again while it's in front, it goes to the program's next window."""

    name: str
    exe: str
    """The program's file name, as Task Manager shows it: `WindowsTerminal.exe`."""
    command: str
    """How to start it: anything the Run dialog (Win+R) accepts."""
    title: str = ""
    """Only windows whose title contains this (for programs that host several apps)."""


type Request = Tool | App

_APP_BASE: Final = 1000


def encode(request: Request, apps: Sequence[App]) -> int:
    """The request as a message's wparam. Apps are sent by their place in the list."""
    match request:
        case Tool():
            value: int = request.value
            return value
        case App():
            return _APP_BASE + apps.index(request)  # ValueError if it isn't in the list


def decode(code: int, apps: Sequence[App]) -> Request | None:
    if code >= _APP_BASE:
        index = code - _APP_BASE
        return apps[index] if index < len(apps) else None
    try:
        return Tool(code)
    except ValueError:
        return None


def encode_indicator(layer: int, locked: bool, caps_word: bool) -> int:
    """The indicator's state as a message's wparam. `layer`: the layer's value, 0 for the base layer."""
    return layer | locked << 4 | caps_word << 5


def decode_indicator(code: int) -> tuple[int, bool, bool]:
    return code & 0xF, bool(code & 0x10), bool(code & 0x20)
