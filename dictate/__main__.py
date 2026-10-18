"""Run dictation: `pythonw -m dictate` (or double-click `dictate.pyw`), next to the
layout. Hold the talk key (B, between the thumb keys), speak, and release: the
words are typed where the cursor is. It quits when the layout quits.

Settings are environment variables (set them under "Edit environment variables
for your account", which needs no admin rights):
    OPENAI_BASE_URL   the endpoint's base, e.g. https://ai.example.com/v1
    OPENAI_API_KEY    its key
    DICTATE_MODEL     optional; default gpt-4o-transcribe
    DICTATE_PROMPT    optional; names and terms to expect
    DICTATE_LANGUAGE  optional; e.g. sv or en, default detected
"""

from __future__ import annotations

import os
import sys

from .core import Missing, settings_from


def main() -> None:
    if sys.platform != "win32":
        sys.exit("dictate only runs on Windows")
    # Imported here: the module declares Win32 functions, which only exist on Windows.
    from .win import Exit, run, show_error  # noqa: PLC0415

    settings = settings_from(os.environ)
    if isinstance(settings, Missing):
        show_error("Set these environment variables first:\n" + "\n".join(settings.names))
        return
    match run(settings):
        case Exit.QUIT:
            pass
        case Exit.ALREADY_RUNNING:
            show_error("Dictate is already running.")
        case Exit.NO_CHANNEL:
            show_error("Windows wouldn't create the events the layout signals through.")


if __name__ == "__main__":
    main()
