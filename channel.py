"""How the toolkit's programs talk to each other, through named Windows events
(which work between processes, need no admin rights and cost nothing to wait on).

The layout (miryoku) owns the push-to-talk key; the dictation tool listens. The
key's state is kept in two manual-reset events, one set while it's held and one
while it isn't, so the listener can wait for either edge and never sees a stale
press: if the key went down and up while it was busy, both are back as they were.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import winapi

MAGIC: Final = 0x4D49_524F
"""Tags keys the toolkit sends, so the layout's hook lets them through untouched."""

_TALKING: Final = "Local\\toolkit-talking"
_IDLE: Final = "Local\\toolkit-idle"
_QUIT: Final = "Local\\toolkit-dictate-quit"


@dataclass(frozen=True, slots=True)
class Channel:
    talking: winapi.EventHandle
    """Set while the push-to-talk key is held."""
    idle: winapi.EventHandle
    """Set while it isn't."""
    quit: winapi.EventHandle
    """Set once by the layout when it quits, so the dictation tool quits too."""

    @staticmethod
    def open() -> Channel | None:
        """Creates the events, or opens them if the other program already has."""
        talking = winapi.create_event(_TALKING, manual_reset=True, initially_set=False)
        idle = winapi.create_event(_IDLE, manual_reset=True, initially_set=True)
        quit_ = winapi.create_event(_QUIT, manual_reset=False, initially_set=False)
        if talking is None or idle is None or quit_ is None:
            for event in (talking, idle, quit_):
                if event is not None:
                    winapi.close_event(event)
            return None
        return Channel(talking, idle, quit_)

    def set_talking(self, talking: bool) -> None:
        # The new state's event is set last, so for a moment neither is set, never both.
        winapi.reset_event(self.idle if talking else self.talking)
        winapi.set_event(self.talking if talking else self.idle)

    def ask_to_quit(self) -> None:
        winapi.set_event(self.quit)

    def close(self) -> None:
        for event in (self.talking, self.idle, self.quit):
            winapi.close_event(event)
