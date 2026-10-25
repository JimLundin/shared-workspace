"""Deliver published meeting completions without coupling recording to automation.

The transcription worker calls this owner before recovery and after its final
export. A first scan baselines old transcripts; subsequent scans repair the gap
between publishing a transcript and committing its routine occurrence.
"""

import stat
import time
from collections.abc import Callable
from pathlib import Path

from automation.routines import MAX_TRANSCRIPTS, RoutineStore, TranscriptReady, transcript_ready

from .meeting_store import MeetingArchive, MeetingStore


class TranscriptEvents:
    __slots__ = ("archive", "clock", "on_change", "on_error", "routines")

    def __init__(
        self, archive: MeetingArchive, routines: RoutineStore, *, on_error: Callable[[str], None],
        on_change: Callable[[], None] | None = None, clock: Callable[[], float] = time.time,
    ) -> None:
        self.archive, self.routines = archive, routines
        self.on_error, self.on_change, self.clock = on_error, on_change, clock

    def _read(self, store: MeetingStore, *, completed_at: float | None = None) -> TranscriptReady | None:
        state = store.state()
        try:
            published_file = store.transcript.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(published_file.st_mode):
            return None
        # Recovery observes the file's publication time, not the restart time.
        # Otherwise a later routine enable could retroactively select an event.
        return transcript_ready(
            store.folder.name, state.title, str(store.transcript.absolute()), finished=state.finished, pending=state.pending,
            revision=state.revision, exported_revision=state.exported_revision,
            completed_at=published_file.st_mtime if completed_at is None else completed_at,
        )

    def _failed(self, operation: str, error: Exception) -> None:
        self.on_error(f"Standing instructions could not {operation}: {error}. Saved transcripts remain available; startup will retry delivery.")

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception as error:
                self._failed("notify the work process", error)

    def recover(self, journals: tuple[Path, ...]) -> None:
        """Scan the journals present at composition, before any new completion.

        The first baseline is all-or-nothing. A damaged or unreadable historical
        journal keeps transcript routines unavailable until its history can be
        assessed; it must not later appear as a newly completed meeting.
        """
        try:
            initialized = self.routines.transcripts_initialized
            if len(journals) > MAX_TRANSCRIPTS:
                raise ValueError("The meeting archive exceeds the supported transcript history capacity")
            historical: list[TranscriptReady] = []
            complete_scan = True
            for path in journals:
                try:
                    event = self._read(self.archive.open(path.parent))
                    if event is None:
                        continue
                    if initialized:
                        self.routines.observe_transcript(event)
                    else:
                        historical.append(event)
                except Exception as error:
                    complete_scan = False
                    self._failed("recover a transcript completion", error)
            if not initialized and complete_scan:
                self.routines.baseline_transcripts(tuple(historical))
            self._changed()
        except Exception as error:
            self._failed("recover transcript history", error)

    def completed(self, store: MeetingStore) -> None:
        """Observe only a finished, fully transcribed, successfully exported file.

        Delivery errors belong to automation. They must not change a successful
        recording into a failed transcription or cause its audio to be retried.
        """
        try:
            event = self._read(store, completed_at=self.clock())
            if event is not None:
                self.routines.observe_transcript(event)
                self._changed()
        except Exception as error:
            self._failed("deliver a transcript completion", error)
