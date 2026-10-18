"""Windows glue for dictation: waits for the push-to-talk key, records the
microphone while it's held, has the recording transcribed, and types the text
wherever the cursor is."""

from __future__ import annotations

import tempfile
import time
import traceback
from enum import Enum, auto
from pathlib import Path
from typing import Final

import winapi
from channel import MAGIC, Channel

from .core import MAX_SECONDS, RATE, Failure, Settings, long_enough, transcribe, typed_text, utf16_units, wav

LOCK_NAME: Final = "Local\\toolkit-dictate"
LOG: Final = Path(tempfile.gettempdir()) / "dictate.log"
"""Problems are written here (there's no console under pythonw), with a warning sound."""


class Exit(Enum):
    QUIT = auto()
    """The layout quit, so this did too."""
    ALREADY_RUNNING = auto()
    NO_CHANNEL = auto()
    """Windows wouldn't create the events the layout signals through."""


def report(problem: str) -> None:
    winapi.beep(winapi.MB_ICONEXCLAMATION)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {problem}\n")


def dictate_once(channel: Channel, mic: winapi.Microphone, settings: Settings) -> bool:
    """One press of the talk key, start to finish. False once it's time to quit."""
    if winapi.wait_any((channel.quit, channel.talking)) == 0:
        return False
    if error := mic.start():
        report(f"can't open the microphone (Windows error {error}). Is microphone access on for desktop apps?")
        return winapi.wait_any((channel.quit, channel.idle)) != 0
    # Record until the key is released or the buffer is full. The mic's event also
    # fires when it opens and closes, so a wake-up isn't always "full".
    while (woken := winapi.wait_any((channel.quit, channel.idle, mic.event))) == 2 and not mic.full():
        pass
    pcm = mic.stop()
    if woken == 0:
        return False
    if not long_enough(pcm):
        return True
    match transcribe(settings, wav(pcm, RATE)):
        case Failure(message):
            report(message)
        case str(transcript):
            if text := typed_text(transcript):
                winapi.send_unicode(utf16_units(text), MAGIC)
    return True


def run(settings: Settings) -> Exit:
    """Blocks until the layout quits."""
    lock = winapi.create_lock(LOCK_NAME)
    if lock is None:
        return Exit.ALREADY_RUNNING
    try:
        channel = Channel.open()
        mic_event = winapi.create_event(None, manual_reset=False, initially_set=False)
        if channel is None or mic_event is None:
            return Exit.NO_CHANNEL
        mic = winapi.Microphone(RATE, MAX_SECONDS, mic_event)
        try:
            while True:
                try:
                    if not dictate_once(channel, mic, settings):
                        return Exit.QUIT
                except Exception:
                    report(traceback.format_exc())  # a bug costs one dictation, not the tool
        finally:
            channel.close()
            winapi.close_event(mic_event)
    finally:
        winapi.release_lock(lock)


def show_error(message: str) -> None:
    winapi.message_box(message, "Dictate", winapi.MB_ICONHAND)
