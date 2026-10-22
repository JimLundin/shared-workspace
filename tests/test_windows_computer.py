"""Native automation against an isolated fixture, without user input injection."""

import base64
import json
import sys
import threading
import time
import unittest
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows computer automation")

from assistant import commands, uia
from assistant.app import WinMachine
from assistant.desktop import WinDesktop
from assistant.geometry import Shot, fit
from assistant.llm import Json
from assistant.ui import Ui
from winapi.screen import Monitor
from winapi.shapes import Rect


def decoded(text: str) -> dict[str, Json]:
    data = cast(Json, json.loads(text))
    assert isinstance(data, dict), text
    return data


def powershell_path(path: Path) -> str:
    encoded = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
    return f"[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))"


@contextmanager
def fixture(folder: Path) -> Generator[int]:
    cancelled = threading.Event()
    source = Path(__file__).with_name("uia_fixture.ps1").read_text(encoding="utf-8")
    script = f"$fixtureRoot = {powershell_path(folder)}\n{source}"
    with ThreadPoolExecutor(max_workers=1) as worker:
        running = worker.submit(commands.powershell, script, timeout_s=100, cancel=cancelled)
        try:
            deadline = time.monotonic() + 15
            while not (folder / "ready").exists():
                assert not running.done(), running.result() if running.done() else "Fixture exited."
                assert time.monotonic() < deadline, "The fixture window did not start."
                time.sleep(.05)
            handle = decoded((folder / "window.json").read_text())["handle"]
            assert isinstance(handle, int)
            yield handle
        finally:
            (folder / "stop").touch()
            try:
                result = running.result(timeout=5)
            finally:
                cancelled.set()
            assert result[0] == 0, result


def inspect(handle: int, *, query: str = "", limit: int = 100, offset: int = 0) -> dict[str, Json]:
    request: dict[str, Json] = {"operation": "inspect", "handle": handle, "query": query, "limit": limit, "offset": offset, "depth": 10}
    code, text = WinMachine.__new__(WinMachine).inspect_ui(request, threading.Event())
    assert code == 0, text
    return decoded(text)


def controls(page: dict[str, Json]) -> list[dict[str, Json]]:
    rows = page["controls"]
    assert isinstance(rows, list)
    return [row for row in rows if isinstance(row, dict)]


def control(page: dict[str, Json], name: str) -> dict[str, Json]:
    return next(row for row in controls(page) if row["automation_id"] == f"fixture_{name}")


def batch(folder: Path, requests: list[dict[str, Json]]) -> list[tuple[bool, str]]:
    """Run real UIA scripts in fresh scopes, sharing only PowerShell startup.

    IDs come from an earlier process; a later process verifies the resulting UI.
    Large read results go to a fixture file so combined output cannot be truncated.
    """
    destination = folder / "uia-results.json"
    blocks = [
        "try { $response = & {\n" + uia.script(request) +
        "\n}; @{ok=$true; text=[string]$response} } catch { @{ok=$false; text=$_.Exception.Message} }\n"
        for request in requests
    ]
    script = "$results = @(\n" + "".join(blocks) + ")\n"
    script += (
        f"[IO.File]::WriteAllText({powershell_path(destination)}, "
        "(ConvertTo-Json -InputObject $results -Depth 4 -Compress), [Text.UTF8Encoding]::new($false))"
    )
    code, output = commands.powershell(script)
    assert code == 0, output
    data = cast(Json, json.loads(destination.read_text(encoding="utf-8")))
    assert isinstance(data, list) and len(data) == len(requests)
    results: list[tuple[bool, str]] = []
    for row in data:
        assert isinstance(row, dict)
        ok, text = row["ok"], row["text"]
        assert isinstance(ok, bool) and isinstance(text, str)
        results.append((ok, text))
    return results


def test_powershell_preserves_utf8_large_scripts_cwd_and_failure_status(tmp_path: Path) -> None:
    value = 'Åäö "quotes" $literal `value`'
    script = f"$value = '{value}'\n" + "# padding\n" * 5000 + "[Console]::WriteLine($value); [Console]::WriteLine((Get-Location).Path)"
    code, output = commands.powershell(script, cwd=str(tmp_path))
    assert code == 0 and len(output.splitlines()) == 2, output
    assert output.splitlines()[0] == value and Path(output.splitlines()[1]).samefile(tmp_path)
    code, output = commands.powershell("throw 'Fixture error'")
    assert code == 1 and "Fixture error" in output
    code, output = commands.powershell("& $env:ComSpec /c 'exit 7'")
    assert code == 7, output
    code, output = commands.powershell("Write-Warning 'Fixture warning'; Write-Output 'Result'")
    assert code == 0 and "Fixture warning" in output and "Result" in output and "CLIXML" not in output
    code, output = commands.powershell("Start-Sleep -Seconds 60", timeout_s=.2)
    assert code != 0 and "Timed out" in output


def test_screenshot_sources_use_physical_bounds_and_scale_only_the_image() -> None:
    desktop = Rect(-1920, 0, 1920, 1080)
    left = Rect(-1920, 0, 0, 1080)
    work = Rect(-1700, 100, -900, 700)
    monitors = [Monitor(left, left, False), Monitor(Rect(0, 0, 1920, 1080), desktop, True)]
    machine = WinMachine(WinDesktop.__new__(WinDesktop), Ui.__new__(Ui))

    def capture(area: Rect, longest: int) -> tuple[bytes, Shot]:
        return b"fixture", Shot(area, *fit(area.width, area.height, longest))

    with (
        patch("assistant.app.windows.foreground", return_value=7),
        patch("assistant.app.windows.exists", return_value=True),
        patch("assistant.app.windows.is_minimized", return_value=False),
        patch("assistant.app.windows.visible_rect", return_value=work),
        patch("assistant.app.screen.virtual_screen", return_value=desktop),
        patch("assistant.app.screen.monitors", return_value=monitors),
        patch.object(WinDesktop, "capture", side_effect=capture),
    ):
        _, shot = machine.screenshot(handle=7, longest=400)
        assert shot.area == work and (shot.width, shot.height) == (400, 300)
        assert shot.to_screen(200, 150) == (-1300, 400)
        assert machine.screenshot(monitor=0)[1].area == left
        assert machine.screenshot(monitor=-2)[1].area == desktop
        assert machine.screenshot(area=Rect(-2000, -100, -1000, 500))[1].area == Rect(-1920, 0, -1000, 500)
        with patch("assistant.app.windows.foreground", return_value=0):
            try:
                machine.screenshot()
            except OSError as error:
                assert "locked" in str(error)
            else:
                raise AssertionError("An unavailable desktop must not produce a screenshot.")


def test_uia_controls_are_inspected_and_operated_across_separate_processes(tmp_path: Path) -> None:
    with fixture(tmp_path) as handle:
        page = inspect(handle)
        rows = controls(page)
        assert len(rows) >= 10
        assert len({str(row["id"]) for row in rows}) == len(rows)
        editor, button = control(page, "editor"), control(page, "button")
        assert "set_value" in cast("list[Json]", editor["actions"])
        value = 'Åäö "literal" $value `value`\nSecond line'
        checkbox = control(page, "checkbox")
        slider = control(page, "slider")
        password = control(page, "password")
        assert password["password"] is True and password["value"] is None, password
        assert "fixture-only-secret" not in json.dumps(page)

        def action(row: dict[str, Json], operation: str, text: str = "") -> dict[str, Json]:
            return {"operation": "act", "handle": handle, "id": row["id"], "action": operation, "value": text}

        results = batch(tmp_path, [
            action(editor, "set_value", value), action(editor, "read"), action(button, "invoke"),
            action(checkbox, "toggle"), action(slider, "set_range", "65"), action(control(page, "beta"), "select"),
            action(control(page, "choices"), "expand"), action(control(page, "choices"), "collapse"),
            action(password, "read"), action(control(page, "disabled"), "invoke"),
            action({"id": "42.2147483647.2147483646"}, "invoke"),
            {"operation": "inspect", "handle": handle, "limit": 2, "offset": 0, "depth": 10},
            {"operation": "inspect", "handle": handle, "limit": 2, "offset": 2, "depth": 10},
            action(editor, "set_value", "'\"\n" * 10000), action(editor, "read"),
        ])
        assert [ok for ok, _text in results] == [True] * 8 + [False] * 3 + [True] * 4, results
        assert decoded(results[1][1])["text"] == value
        selected = decoded(results[5][1])["control"]
        assert isinstance(selected, dict) and selected["value"] is True
        for index, expected in ((6, "Expanded"), (7, "Collapsed")):
            changed = decoded(results[index][1])["control"]
            assert isinstance(changed, dict) and changed["value"] == expected
        for index, expected in ((8, "Unsupported action"), (9, "disabled"), (10, "changed or disappeared")):
            assert expected in results[index][1]
        first, second = decoded(results[11][1]), decoded(results[12][1])
        assert first["next_offset"] == 2
        assert {str(row["id"]) for row in controls(first)}.isdisjoint(str(row["id"]) for row in controls(second))
        long_read = results[14][1]
        assert len(long_read) < 19000 and decoded(long_read)["truncated"] is True
        # A third process must see the effects of actions made with the first
        # process's control IDs; no AutomationElement instances are shared.
        after = inspect(handle)
        assert control(after, "status")["name"] == "Pressed 1"
        assert control(after, "checkbox")["value"] == "On"
        slider_value = control(after, "slider")["value"]
        assert isinstance(slider_value, dict) and slider_value["value"] == 65
