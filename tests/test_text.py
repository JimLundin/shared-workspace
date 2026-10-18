"""What the assistant types or pastes, made safe and tidy."""

from __future__ import annotations

from assistant.text import one_line_command, typed_text, unwrap
from winapi.shapes import utf16_units


def test_typed_text_never_contains_a_newline() -> None:
    # A newline would press Enter, and in a terminal run whatever was dictated.
    assert typed_text("git status\nrm -rf build\r\n") == "git status rm -rf build "


def test_typed_text_collapses_whitespace_and_drops_control_characters() -> None:
    assert typed_text("  hej\t\tdå \x1b[2J ") == "hej då [2J "


def test_typed_text_of_nothing_is_nothing() -> None:
    assert typed_text(" \n ") == ""


def test_one_line_command_strips_what_models_wrap_commands_in() -> None:
    assert one_line_command("```powershell\nGet-ChildItem -Recurse\n```") == "Get-ChildItem -Recurse"
    assert one_line_command("`git status`") == "git status"
    assert one_line_command("PS> Get-Date") == "Get-Date"


def test_one_line_command_joins_lines_so_nothing_runs_early() -> None:
    assert one_line_command("Get-Process |\n  Sort-Object CPU") == "Get-Process | Sort-Object CPU"


def test_unwrap_removes_fences_and_quotes_around_a_rewrite() -> None:
    assert unwrap("```\nHello,\nworld\n```") == "Hello,\nworld"
    assert unwrap('"Hello there."') == "Hello there."
    assert unwrap('Keep "inner" quotes') == 'Keep "inner" quotes'


def test_utf16_units() -> None:
    assert utf16_units("aå") == [0x61, 0xE5]
    assert utf16_units("😀") == [0xD83D, 0xDE00]  # outside the BMP: a surrogate pair
