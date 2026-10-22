"""Readable, bounded action details at the presentation boundary."""

import json
from itertools import islice
from typing import cast


def label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def title(name: str) -> str:
    return {"inspect_ui": "Inspect controls", "ui_action": "Use a control", "desktop_state": "Check desktop",
            "get_context": "Check recent work", "get_clipboard": "Read clipboard"}.get(name, label(name))


def shortened(text: str, limit: int) -> str:
    return text[:limit - 1].rstrip() + "…" if limit > 0 and len(text) > limit else text


def object_fields(text: str) -> dict[str, object] | None:
    if not text.lstrip().startswith("{"):
        return None
    try:
        value: object = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def arguments(name: str, text: str, *, limit: int = 0) -> str:
    """Keep the user's target visible and omit transport IDs and search knobs."""
    fields = object_fields(text)
    if fields is None:
        return readable(text, limit=limit)
    match name:
        case "inspect_ui":
            query = fields.get("query")
            description = f"Find controls matching “{query}”" if isinstance(query, str) and query else "Inspect the selected window."
        case "focus_window":
            description = "Bring the selected window forward."
        case "ui_action" | "manage_window":
            action = fields.get("action")
            target = "control" if name == "ui_action" else "window"
            verb = {"invoke": "Activate", "set_value": "Fill in", "set_range": "Set the value of", "scroll_into_view": "Scroll to"}
            if action in ("snap_left", "snap_right"):
                description = "Snap the selected window to the left." if action == "snap_left" else "Snap the selected window to the right."
            else:
                description = (f"{verb.get(action, label(action))} the selected {target}." if isinstance(action, str)
                               else f"Use the selected {target}.")
        case _:
            description = describe({key: value for key, value in fields.items()
                                    if key not in {"handle", "id", "target_id", "depth", "offset", "limit", "max_size"}})
    return shortened(description, limit)


def result(name: str, text: str, *, limit: int = 0) -> str:
    if name == "inspect_ui" and (fields := object_fields(text)) is not None and isinstance(controls := fields.get("controls"), list):
        items = cast(list[object], controls)
        names: list[str] = []
        for item in items[:4]:
            if isinstance(item, dict) and isinstance(value := cast(dict[str, object], item).get("name"), str) and value.strip():
                names.append(value)
        if names:
            summary = "Controls found:\n" + "\n".join("• " + value for value in names)
            if len(items) > len(names):
                summary += f"\n… {len(items) - len(names)} more controls"
            return shortened(summary, limit)
        if not items:
            return "No matching controls."
    return readable(text, limit=limit)


def describe(value: object, depth: int = 0) -> str:
    """Render JSON values as labels and lists, without serializing them again."""
    match value:
        case None:
            return "Not set"
        case bool():
            return "Yes" if value else "No"
        case str():
            return value
        case int() | float():
            return str(value)
        case dict():
            fields = cast(dict[str, object], value)
            if depth >= 3:
                return f"{len(fields)} fields"
            lines: list[str] = []
            for key, item in islice(fields.items(), 6):
                text = describe(item, depth + 1)
                lines.append(f"{label(key)}: {text}".replace("\n", "\n  "))
            if len(fields) > 6:
                lines.append(f"… {len(fields) - 6} more fields")
            return "\n".join(lines)
        case list():
            items = cast(list[object], value)
            if not items:
                return "No items"
            simple = all(isinstance(item, (str, int, float, bool, type(None))) for item in items[:4])
            if depth >= 3 and not simple:
                return f"{len(items)} items"
            texts = tuple(describe(item, depth + 1) for item in items[:4])
            result = ", ".join(texts) if simple else "\n" + "\n".join("• " + text.replace("\n", "\n  ") for text in texts)
            return result + (f"\n… {len(items) - 4} more items" if len(items) > 4 else "")
        case _:
            return "Unavailable"


def readable(text: str, *, limit: int = 0) -> str:
    """Format a complete payload before shortening it; keep ordinary prose intact."""
    text = text.strip()
    if text.startswith(("{", "[")):
        try:
            value: object = json.loads(text)
        except (ValueError, RecursionError):
            pass
        else:
            text = describe(value)
    return shortened(text, limit)
