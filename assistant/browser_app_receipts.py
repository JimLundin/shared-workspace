"""Verify a saved application card from the UI owner's concrete receipt."""

import json
from typing import cast

from automation.task import ActionUncertain, ActionVerified, identifier


def opened_card(requested_url: str, result: str) -> ActionVerified | ActionUncertain:
    """A requested or loading page is distinct from a persisted canvas card."""
    try:
        value: object = json.loads(result)
        if not isinstance(value, dict):
            raise ValueError("Missing saved application receipt.")
        fields = cast(dict[str, object], value)
        if fields.get("operation") != "open" or fields.get("saved") is not True or fields.get("requested_url") != requested_url:
            raise ValueError("The application receipt does not match the requested open.")
        identity = identifier(fields.get("item_id"), "saved application card")
    except (ValueError, RecursionError):
        return ActionUncertain("The application did not return a matching saved-card receipt. Inspect the canvas before opening it again.")
    return ActionVerified(f"The canvas saved application card {identity}. Its page may still be loading.")
