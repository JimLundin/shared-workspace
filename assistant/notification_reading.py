"""Reading-paced notification playback; geometry and time are supplied by the UI."""

from dataclasses import dataclass
from typing import Final, assert_never

START_SECONDS: Final = 4.0


@dataclass(frozen=True, slots=True)
class Waiting:
    offset: float
    until: float


@dataclass(frozen=True, slots=True)
class Scrolling:
    offset: float
    at: float


@dataclass(frozen=True, slots=True)
class Paused:
    offset: float


@dataclass(frozen=True, slots=True)
class Finished:
    offset: float


type State = Waiting | Scrolling | Paused | Finished


def pace(text: str, height: int, line_height: int) -> float:
    """About 180 words/minute, with bounds for code and sparse paragraphs."""
    words = max(1, len(text.split()), len(text) / 6)
    return max(line_height / 4, min(line_height, height * 3 / words))


def advance(state: State, now: float, limit: float, speed: float, *, paused: bool) -> State:
    offset = max(0.0, min(limit, state.offset))
    if offset >= limit:
        return Finished(offset)
    if paused:
        return Paused(offset)
    match state:
        case Waiting(until=until) if now < until:
            return Waiting(offset, until)
        case Waiting(until=until):
            elapsed = max(0.0, now - until)
        case Scrolling(at=at):
            elapsed = max(0.0, now - at)
        case Paused() | Finished():
            return Waiting(offset, now + START_SECONDS)
        case _:
            assert_never(state)
    offset = min(limit, offset + elapsed * speed)
    return Finished(offset) if offset >= limit else Scrolling(offset, now)


def displayed(offset: float, limit: float, viewport: int, line_height: int, *, smooth: bool, origin: float = 0) -> float:
    """Reduced motion changes pages with an overlapping line, without sliding."""
    if smooth or offset >= limit:
        return min(limit, offset)
    page = max(line_height, viewport - line_height)
    return min(limit, origin + max(0.0, offset - origin) // page * page)
