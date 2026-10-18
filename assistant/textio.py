"""Clipboard replacement: do not send Paste until the replacement is available."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Clipboard(Protocol):
    def get_text(self, owner: int, /) -> str | None: ...
    def set_text(self, owner: int, text: str, /) -> bool: ...


def paste_text(owner: int, text: str, clipboard: Clipboard, paste: Callable[[], None], wait: Callable[[], None]) -> None:
    saved = clipboard.get_text(owner)
    if not clipboard.set_text(owner, text):
        raise OSError("The clipboard is busy; the selection was not replaced. Try again.")
    try:
        paste()
        wait()
    finally:
        if saved is not None and not clipboard.set_text(owner, saved):
            raise OSError("Text was pasted, but the previous clipboard text could not be restored.")
