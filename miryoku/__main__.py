"""Run the layout: `pythonw -m miryoku` (or double-click `miryoku.pyw`).

Set the Windows keyboard to English (US). The key left of 1 (§) is the escape
key: tap it to switch between this layout and your plain keyboard, hold it for
2 seconds to quit. B, between the thumb keys, is push-to-talk for the
dictation tool (`dictate`), if it's running.
"""

from __future__ import annotations

import sys


def main() -> None:
    if sys.platform != "win32":
        sys.exit("miryoku only runs on Windows")
    # Imported here: the module declares Win32 functions, which only exist on Windows.
    from .win import Exit, run, show_error  # noqa: PLC0415

    match run():
        case Exit.QUIT:
            pass
        case Exit.ALREADY_RUNNING:
            show_error("Miryoku is already running.")
        case Exit.HOOK_REFUSED:
            show_error("Windows refused the keyboard hook.")


if __name__ == "__main__":
    main()
