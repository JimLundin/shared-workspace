"""The pieces both programs share: the message protocol and the usage log."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config import APPS
from protocol import App, Request, Tool, decode, encode
from usage import record, record_counts, report, summarize

# ---- protocol --------------------------------------------------------------------------


def test_tools_and_apps_round_trip() -> None:
    requests: list[Request] = [*list(Tool), *APPS]
    for request in requests:
        assert decode(encode(request, APPS), APPS) == request


def test_unknown_codes_decode_to_nothing() -> None:
    assert decode(0, APPS) is None
    assert decode(999, APPS) is None
    assert decode(1000 + len(APPS), APPS) is None


def test_an_app_not_in_the_list_cannot_be_sent() -> None:
    stranger = App("stranger", "stranger.exe", "stranger.exe")
    try:
        encode(stranger, APPS)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ---- usage log -------------------------------------------------------------------------


def test_usage_is_summarized_per_feature(tmp_path: Path) -> None:
    log = tmp_path / "usage.jsonl"
    record("assistant", "dictate", {"ok": True, "seconds": 1.5}, path=log)
    record("assistant", "dictate", {"ok": False, "error": "HTTP 401"}, path=log)
    record_counts("layout", {"nav:left": 12, "snippet:,,date": 1}, path=log)
    record_counts("layout", {"nav:left": 3}, path=log)
    summary = summarize(log.read_text(encoding="utf-8").splitlines())
    assert summary["assistant/dictate"].uses == 2
    assert summary["assistant/dictate"].failures == 1
    assert summary["layout/nav:left"].uses == 15
    assert summary["layout/snippet:,,date"].uses == 1


def test_usage_ignores_broken_lines_and_old_entries(tmp_path: Path) -> None:
    lines = [
        "not json",
        '{"t": "2020-01-01T00:00:00", "source": "assistant", "feature": "ask"}',
        '{"t": "2026-10-01T00:00:00", "source": "assistant", "feature": "ask"}',
        '{"t": "2026-10-01T00:00:00", "source": "assistant"}',
    ]
    summary = summarize(lines, since=datetime(2026, 1, 1))
    assert list(summary) == ["assistant/ask"]
    assert summary["assistant/ask"].uses == 1


def test_report_lists_features_never_used() -> None:
    lines = ['{"t": "2026-10-01T12:00:00", "source": "assistant", "feature": "ask"}']
    text = report(summarize(lines), known=["assistant/ask", "assistant/agent"])
    assert "assistant/ask" in text
    assert text.index("Never used") < text.index("assistant/agent")
