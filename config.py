"""Your settings: the apps on the Apps layer, text snippets, what each rewrite key
asks for, and which commands the agent may run without asking. Edit freely; it's
checked when the toolkit starts. (Secrets and the AI endpoint are environment
variables instead; see the README.)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from protocol import App, Tool

# ---- Apps layer ------------------------------------------------------------------------
# Up to ten, in key order: the top row of the left hand, then the home row.

APPS: Final[tuple[App, ...]] = (
    App("terminal", "windowsterminal.exe", "wt.exe"),
    App("browser", "msedge.exe", "msedge.exe"),
    App("outlook", "olk.exe", "olk.exe"),
    App("teams", "ms-teams.exe", "msteams:"),
    App("files", "explorer.exe", "explorer.exe"),
    App("code", "code.exe", "code"),
    App("notepad", "notepad.exe", "notepad.exe"),
    App("excel", "excel.exe", "excel.exe"),
    App("word", "winword.exe", "winword.exe"),
    App("powerpoint", "powerpnt.exe", "powerpnt.exe"),
)

# ---- Snippets --------------------------------------------------------------------------
# Type the trigger and it's replaced by the text. A function is called each time.
# Triggers start with ",," so they never turn up in normal writing.


def _now(fmt: str) -> Callable[[], str]:
    return lambda: datetime.now().strftime(fmt)


SNIPPETS: Final[dict[str, str | Callable[[], str]]] = {
    ",,date": _now("%Y-%m-%d"),
    ",,time": _now("%H:%M"),
    ",,now": _now("%Y-%m-%d %H:%M"),
    ",,week": _now("week %V"),
    ",,shrug": "¯\\_(ツ)_/¯",
    ",,thx": "Thanks!",
    ",,mvh": "Med vänliga hälsningar",
    ",,br": "Best regards",
}

# ---- Rewrite keys ----------------------------------------------------------------------
# What each key asks the model to do with the selected text.

REWRITES: Final[dict[Tool, str]] = {
    Tool.FIX: "Fix spelling, grammar and punctuation. Keep the language, wording, tone and formatting otherwise unchanged.",
    Tool.TRANSLATE: "Translate it: Swedish to English, or anything else to Swedish. Keep the tone and formatting.",
    Tool.SHORTER: "Make it shorter and clearer without losing any information. Keep the language and tone.",
    Tool.FORMAL: "Make it more formal and professional, suitable for work email. Keep the language and meaning.",
}

# ---- Agent -----------------------------------------------------------------------------
# Commands the agent may run without asking. A command is allowed only if it starts
# with one of these and contains nothing that could chain another command after it.

ALLOWED_COMMANDS: Final[tuple[str, ...]] = (
    "Get-ChildItem",
    "Get-Content",
    "Get-Date",
    "Get-Location",
    "Get-Process",
    "Select-String",
    "git status",
    "git log",
    "git diff",
    "git branch",
)

AGENT_MAX_STEPS: Final = 40
"""The agent stops after this many actions, finished or not."""
