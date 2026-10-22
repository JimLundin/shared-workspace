"""The usage log: which features get used, so unused ones can be removed. One JSON
object per line in `%LOCALAPPDATA%/toolkit/usage.jsonl`. It records that a feature
was used (and how long it took, whether it failed), never what was typed or said.
Plain Python: importable anywhere.
"""

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, cast

DIRECTORY: Final = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "toolkit"
"""Where the toolkit keeps its logs."""
USAGE_LOG: Final = DIRECTORY / "usage.jsonl"

type Detail = str | int | float | bool


def _append(entry: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record(source: str, feature: str, details: Mapping[str, Detail] | None = None, *, path: Path = USAGE_LOG) -> None:
    """One use of a feature. `details` may say `ok` (False for a failure), `seconds`..."""
    _append({"t": _now(), "source": source, "feature": feature, **(details or {})}, path)


def record_counts(source: str, counts: Mapping[str, int], *, path: Path = USAGE_LOG) -> None:
    """Uses counted since the last call, for features used too often to log one by one."""
    if counts:
        _append({"t": _now(), "source": source, "counts": dict(counts)}, path)


@dataclass(slots=True)
class Use:
    uses: int = 0
    failures: int = 0
    last: str = ""
    """When it was last used (ISO time)."""


def _entry(line: str) -> dict[str, object] | None:
    try:
        data: object = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {key: value for key, value in cast("dict[object, object]", data).items() if isinstance(key, str)}


def summarize(lines: Iterable[str], since: datetime | None = None) -> dict[str, Use]:
    """Uses per feature (`source/feature`). Lines that aren't log entries are skipped."""
    summary: dict[str, Use] = {}
    cutoff = since.isoformat() if since else ""
    for line in lines:
        entry = _entry(line)
        if entry is None:
            continue
        t, source = entry.get("t"), entry.get("source")
        if not isinstance(t, str) or not isinstance(source, str) or t < cutoff:
            continue
        feature, counts = entry.get("feature"), entry.get("counts")
        found: list[tuple[str, int]] = []
        if isinstance(feature, str):
            found.append((feature, 1))
        if isinstance(counts, dict):
            found += [(n, c) for n, c in cast("dict[object, object]", counts).items() if isinstance(n, str) and isinstance(c, int)]
        for name, count in found:
            use = summary.setdefault(f"{source}/{name}", Use())
            use.uses += count
            use.failures += 1 if entry.get("ok") is False else 0
            use.last = max(use.last, t)
    return summary


def _most_used_first(item: tuple[str, Use]) -> tuple[int, str]:
    return -item[1].uses, item[0]


def report(summary: Mapping[str, Use], known: Iterable[str]) -> str:
    """A plain-text table, most used first, then the known features never used."""
    rows = sorted(summary.items(), key=_most_used_first)
    width = max((len(name) for name, _ in rows), default=10)
    lines = [f"{'Feature':<{width}}  {'Uses':>6}  {'Failed':>6}  Last used", ""]
    lines += [f"{name:<{width}}  {use.uses:>6}  {use.failures:>6}  {use.last.replace('T', ' ')}" for name, use in rows]
    unused = sorted(set(known) - set(summary))
    if unused:
        lines += ["", "Never used:", *(f"  {name}" for name in unused)]
    return "\n".join(lines) + "\n"
