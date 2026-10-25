"""Recording values and decisions, independent of devices, storage, threads, and widgets."""

from dataclasses import dataclass
from enum import Enum, auto
from math import isfinite
from typing import Literal, assert_never

from .llm import RATE

type AudioSource = Literal["both", "microphone"]
type Phase = Literal["idle", "starting", "recording", "stopping", "finishing", "paused", "saved", "interrupted"]
type RecordingAction = Literal["start", "start_microphone", "stop", "open", "folder", "retry"]
type CompletionPhase = Literal["finishing", "interrupted", "saved"]


class Exhausted(Enum):
    PAUSED = auto()


@dataclass(frozen=True, slots=True)
class Retry:
    failures: int
    delay: float


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    delays: tuple[float, ...] = (2, 4, 8, 16, 30)
    finished_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.delays or any(not isfinite(delay) or delay < 0 for delay in self.delays) or self.finished_attempts < 1:
            raise ValueError("Retries need finite, nonnegative delays and at least one attempt.")

    def failed(self, failures: int, *, finished: bool) -> Retry | Exhausted:
        failures += 1
        if finished and failures >= self.finished_attempts:
            return Exhausted.PAUSED
        return Retry(failures, self.delays[min(failures - 1, len(self.delays) - 1)])


@dataclass(frozen=True, slots=True)
class RecordingState:
    """The view's progress snapshot; a starting capture may not yet have a journal."""

    phase: Phase = "idle"
    identity: str = ""
    title: str = "Meeting"
    source: AudioSource = "both"
    seconds: float = 0
    pending_seconds: float = 0
    path: str = ""
    detail: str = ""
    preview: str = ""
    revision: int = -1
    exported_revision: int = -1

    @property
    def capturing(self) -> bool:
        return self.phase in ("starting", "recording", "stopping")

    @property
    def visible(self) -> bool:
        return self.phase != "idle"

    @property
    def control(self) -> Literal["stop", "retry", "open"]:
        match self.phase:
            case "starting" | "recording" | "stopping":
                return "stop"
            case "paused":
                return "retry"
            case "idle" | "finishing" | "saved" | "interrupted":
                return "open"
            case _:
                assert_never(self.phase)


@dataclass(frozen=True, slots=True)
class StoredState:
    title: str
    source: AudioSource
    started: float
    frames: int
    pending: int
    finished: bool
    problem: str
    revision: int
    exported_revision: int


@dataclass(frozen=True, slots=True)
class Batch:
    start: int
    pcm: bytes
    context: str = ""

    @property
    def end(self) -> int:
        return self.start + len(self.pcm) // 2


@dataclass(frozen=True, slots=True)
class Publication:
    identity: str
    path: str
    saved: StoredState
    preview: str


def completion_phase(saved: StoredState) -> CompletionPhase:
    """Decide progress after capture finishes, unless an explicit pause overrides it."""
    match saved:
        case StoredState(pending=pending, revision=revision, exported_revision=exported) if pending or revision != exported:
            return "finishing"
        case StoredState(problem=problem) if problem:
            return "interrupted"
        case _:
            return "saved"


def published(
    current: RecordingState, incoming: Publication, *, stopping: bool, paused: str | None = None,
    phase: Phase | None = None, detail: str | None = None,
) -> RecordingState | None:
    """Reject stale observations before deriving the next immutable view snapshot."""
    saved = incoming.saved
    if current.phase == "starting" and not current.identity:
        return None  # a new capture reserved the view before its journal exists
    if current.identity and current.identity != incoming.identity:
        return None
    if ((saved.revision, saved.exported_revision) < (current.revision, current.exported_revision)
            or saved.frames < round(current.seconds * RATE)
            or saved.frames - saved.pending < round((current.seconds - current.pending_seconds) * RATE)
            or (not saved.finished and current.phase in ("finishing", "paused", "saved", "interrupted"))):
        return None
    if not saved.finished:
        phase = "stopping" if stopping else "starting" if current.phase == "starting" else "recording"
    elif paused is not None:
        phase, detail = "paused", paused
    elif phase is None:
        phase = completion_phase(saved)
    return RecordingState(
        phase, incoming.identity, saved.title, saved.source, saved.frames / RATE, saved.pending / RATE, incoming.path,
        saved.problem or (detail if detail is not None else current.detail), incoming.preview, saved.revision, saved.exported_revision,
    )


def duration(seconds: float) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def source_name(source: AudioSource) -> str:
    match source:
        case "both":
            return "Microphone + computer audio"
        case "microphone":
            return "Microphone"
        case _:
            assert_never(source)


def phase_label(phase: Phase) -> str:
    match phase:
        case "idle":
            return ""
        case "starting":
            return "Starting"
        case "recording":
            return "Recording"
        case "stopping":
            return "Stopping"
        case "finishing":
            return "Transcribing"
        case "paused":
            return "Paused"
        case "saved":
            return "Saved"
        case "interrupted":
            return "Interrupted"
        case _:
            assert_never(phase)
