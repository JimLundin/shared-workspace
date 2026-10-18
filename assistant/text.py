"""Making model output safe and tidy before it's typed or pasted."""

from __future__ import annotations

import re
from typing import Final

_FENCE: Final = re.compile(r"^```[\w-]*\n(.*?)\n?```$", re.DOTALL)


def typed_text(transcript: str) -> str:
    """What to type for dictation: one line, with a space after it so the next
    dictation doesn't run into this one. Newlines become spaces (a newline would
    press Enter, and in a terminal run the command); control characters go."""
    line = " ".join("".join(c for c in transcript if c.isprintable() or c.isspace()).split())
    return f"{line} " if line else ""


def unwrap(text: str) -> str:
    """The text without a code fence or quotes a model put around all of it."""
    text = text.strip()
    if match := _FENCE.match(text):
        text = match.group(1).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    return text


def one_line_command(text: str) -> str:
    """A suggested command as one line, so typing it never presses Enter."""
    line = " ".join(unwrap(text).split())
    return line.removeprefix("PS> ").removeprefix("> ").removeprefix("$ ")
