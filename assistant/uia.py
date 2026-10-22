"""Use Windows' accessibility API through its installed PowerShell/.NET bridge."""

import base64
import json
from pathlib import Path

from .llm import Json


def script(request: dict[str, Json]) -> str:
    """Data is encoded separately from code, including text entered into controls."""
    payload = base64.b64encode(json.dumps(request, ensure_ascii=False).encode("utf-8")).decode("ascii")
    source = Path(__file__).with_suffix(".ps1").read_text(encoding="utf-8")
    return f"$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json\n{source}"
