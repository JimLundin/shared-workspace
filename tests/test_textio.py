"""Clipboard contention must not replace the selection with old clipboard text."""

from __future__ import annotations

from assistant.textio import paste_text
from winapi.shapes import require_input_count


class Clipboard:
    __slots__ = ("accept", "text")

    def __init__(self, accept: bool) -> None:
        self.accept = accept
        self.text = "old contents"

    def get_text(self, owner: int) -> str:
        return self.text

    def set_text(self, owner: int, text: str) -> bool:
        if self.accept:
            self.text = text
        return self.accept


def test_failed_clipboard_write_never_pastes() -> None:
    clipboard = Clipboard(False)
    pasted: list[str] = []
    try:
        paste_text(1, "replacement", clipboard, lambda: pasted.append(clipboard.text), lambda: None)
    except OSError:
        pass
    else:
        raise AssertionError("busy clipboard must be reported")
    assert pasted == []
    assert clipboard.text == "old contents"


def test_successful_paste_restores_previous_text() -> None:
    clipboard = Clipboard(True)
    pasted: list[str] = []
    paste_text(1, "replacement", clipboard, lambda: pasted.append(clipboard.text), lambda: None)
    assert pasted == ["replacement"]
    assert clipboard.text == "old contents"


def test_rejected_and_partial_input_are_reported() -> None:
    for sent in (0, 1):
        try:
            require_input_count(sent, 2)
        except OSError as error:
            assert str(sent) in str(error)
        else:
            raise AssertionError("incomplete input must fail")
    require_input_count(2, 2)
