"""The usage report: every feature, how often it was used, and which never were."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from config import APPS, SNIPPETS
from miryoku.layout import TABLE, Command, Layer, Mod, describe
from protocol import Tool
from usage import DIRECTORY, USAGE_LOG, report, summarize


def known_features() -> Iterator[str]:
    """Everything either program can log, as `source/feature`."""
    yield from (f"assistant/{tool.name.lower()}" for tool in Tool if tool is not Tool.STOP)
    yield from (f"assistant/app:{app.name}" for app in APPS)
    yield "assistant/agent"
    for layer, actions in TABLE.items():
        if layer is not None:
            yield from sorted({f"layout/{layer.name.lower()}:{describe(a)}" for a in actions if a is not Command.NOTHING})
    yield from (f"layout/hold:{mod.name.lower()}" for mod in Mod)
    yield from (f"layout/layer:{layer.name.lower()}" for layer in Layer)
    yield from (f"layout/snippet:{trigger}" for trigger in SNIPPETS)
    yield from ("layout/caps_word", "layout/layer_lock", "layout/switch_off")


def write_report(days: int = 30) -> Path:
    """The report for the last `days` days, as a text file; its path."""
    lines = USAGE_LOG.read_text(encoding="utf-8").splitlines() if USAGE_LOG.exists() else []
    summary = summarize(lines, since=datetime.now() - timedelta(days=days))
    path = DIRECTORY / "usage report.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"Toolkit usage, last {days} days\n\n" + report(summary, known_features()), encoding="utf-8")
    return path
