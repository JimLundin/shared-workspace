"""A bounded notification projection; expiry never edits the conversation.

Session observations and monotonic timestamps enter here. Tk owns the windows,
timer callbacks and motion; the model remembers which content has been seen so
an expired reply cannot reappear on the next status update.
"""

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Final, assert_never

from .agent_session import (
    Answer,
    Answered,
    Approval,
    ChatMessage,
    Failed,
    MeetingMessage,
    Question,
    ReasoningSummary,
    Snapshot,
    StreamState,
    ToolActivity,
    ToolStatus,
    pending,
    presentation,
)
from .recording_state import RecordingState, duration, phase_label

MAX_VISIBLE: Final = 5
WIDTH: Final = 280  # maximum logical width, independent of the full history window
MIN_SECONDS: Final = 12.0
MAX_SECONDS: Final = 30.0
MOTION_SECONDS: Final = .28  # Vault of Glass: motion.base
REVEAL_SECONDS: Final = .16  # Vault of Glass: motion.fast


class Kind(Enum):
    WORK = auto()
    RECORDING = auto()


class Busy(Enum):
    THINKING = auto()
    WAITING = auto()
    STOPPING = auto()


class Tone(Enum):
    USER = auto()
    THINKING = auto()
    ACTION = auto()
    REPLY = auto()
    QUESTION = auto()
    ERROR = auto()
    RECORDING = auto()


@dataclass(frozen=True, slots=True)
class MessageKey:
    index: int


@dataclass(frozen=True, slots=True)
class ResponseKey:
    index: int


type Key = Kind | MessageKey | ResponseKey


@dataclass(frozen=True, slots=True)
class Working:
    generation: int
    phase: Busy
    queued: int


@dataclass(frozen=True, slots=True)
class Entry:
    index: int
    message: ChatMessage | Answer | ToolActivity | Question | Approval


@dataclass(frozen=True, slots=True)
class Response:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class Recording:
    state: RecordingState


type Content = Working | Entry | Response | Recording


class Held(Enum):
    ACTIVE = auto()
    READING = auto()


@dataclass(frozen=True, slots=True)
class Expires:
    at: float


@dataclass(frozen=True, slots=True)
class Paused:
    remaining: float


type Lifetime = Held | Expires | Paused


@dataclass(frozen=True, slots=True)
class Toast:
    key: Key
    content: Content
    lifetime: Lifetime
    order: int


@dataclass(frozen=True, slots=True)
class Stack:
    identity: str = ""
    observed: tuple[Content, ...] = ()
    toasts: tuple[Toast, ...] = ()
    serial: int = 0


def key_of(content: Content) -> Key:
    match content:
        case Working():
            return Kind.WORK
        case Recording():
            return Kind.RECORDING
        case Entry(index):
            return MessageKey(index)
        case Response(index):
            return ResponseKey(index)
        case _:
            assert_never(content)


def active(content: Content) -> bool:
    match content:
        case Working():
            return True
        case Recording(state):
            return state.phase in ("starting", "recording", "stopping", "finishing", "paused")
        case Entry(message=Answer(state=StreamState.RUNNING) | ToolActivity(outcome=ToolStatus.RUNNING)):
            return True
        case Entry(message=Question() | Approval() as message):
            return pending(message)
        case Entry() | Response():
            return False
        case _:
            assert_never(content)


def can_stop(content: Content) -> bool:
    match content:
        case Working():
            return True
        case Recording(state):
            return state.capturing
        case Entry() | Response():
            return False
        case _:
            assert_never(content)


def observe(session: Snapshot) -> tuple[Content, ...]:
    result: list[Content] = []
    for index, message in enumerate(session.messages):
        match message:
            case ChatMessage() | Answer() | ToolActivity() | Question() | Approval():
                result.append(Entry(index, message))
                match message:
                    case Question(reply=Answered(text)):
                        result.append(Response(index, text))
                    case Approval(decision="approved" | "declined" as decision):
                        result.append(Response(index, decision.capitalize()))
                    case _:
                        pass
            case MeetingMessage() | ReasoningSummary():
                pass
            case _:
                assert_never(message)
    if session.busy or session.waiting:
        phase = Busy.STOPPING if session.stopping else Busy.WAITING if session.waiting else Busy.THINKING
        result.append(Working(session.generation, phase, len(session.queued)))
    if session.recording.visible:
        result.append(Recording(session.recording))
    return tuple(result)


def reading_seconds(content: Content) -> float:
    text = body(content)
    minimum = 20.0 if tone(content) is Tone.ERROR else MIN_SECONDS
    return min(MAX_SECONDS, max(minimum, 4 + len(text.split()) / 3))


def advance(stack: Stack, session: Snapshot, now: float) -> Stack:
    if stack.identity != session.identity:
        stack = Stack(identity=session.identity)
    previous = {key_of(content): content for content in stack.observed}
    existing = {toast.key: toast for toast in stack.toasts}
    observed = observe(session)
    toasts: list[Toast] = []
    serial = stack.serial
    for content in observed:
        key = key_of(content)
        toast = existing.get(key)
        changed = previous.get(key) != content
        if toast is None and not changed:
            continue
        if toast is None:
            serial += 1
            toast = Toast(key, content, Held.ACTIVE, serial)
        lifetime = toast.lifetime
        if active(content) or ((session.busy or session.waiting) and isinstance(content, (Entry, Response))):
            if lifetime is not Held.READING:
                lifetime = Held.ACTIVE
        elif lifetime is Held.READING:
            pass  # The view releases this after the last line becomes readable.
        elif changed or lifetime is Held.ACTIVE:
            seconds = reading_seconds(content)
            lifetime = Paused(seconds) if isinstance(lifetime, Paused) else Expires(now + seconds)
        if isinstance(lifetime, Expires) and lifetime.at <= now:
            continue
        toasts.append(replace(toast, content=content, lifetime=lifetime))
    # Keep controls and pending input, then the newest conversation entries. Overflow only
    # leaves this projection; every message remains in the session's history.
    toasts.sort(key=lambda toast: (priority(toast.content), toast.order), reverse=True)
    visible = tuple(sorted(toasts[:MAX_VISIBLE], key=order_of))
    return Stack(session.identity, observed, visible, serial)


def reading_hold(stack: Stack, key: Key, reading: bool, now: float, *, busy: bool, hovered: bool) -> Stack:
    toasts: list[Toast] = []
    changed = False
    for toast in stack.toasts:
        lifetime = toast.lifetime
        if toast.key == key:
            if reading:
                lifetime = Held.READING
            elif lifetime is Held.READING:
                seconds = reading_seconds(toast.content)
                lifetime = Held.ACTIVE if busy or active(toast.content) else Paused(seconds) if hovered else Expires(now + seconds)
        changed = changed or lifetime != toast.lifetime
        toasts.append(toast if lifetime == toast.lifetime else replace(toast, lifetime=lifetime))
    return replace(stack, toasts=tuple(toasts)) if changed else stack


def priority(content: Content) -> int:
    match content:
        case Working() | Recording() if active(content):
            return 3
        case Entry(message=Question() | Approval() as message) if pending(message):
            return 2
        case _:
            return int(active(content))


def order_of(toast: Toast) -> tuple[int, int]:
    """The conversation grows above the compact controls at the bottom."""
    return (2 if isinstance(toast.content, Working) else 1 if isinstance(toast.content, Recording) else 0, toast.order)


@dataclass(frozen=True, slots=True)
class Space:
    minimum: int
    preferred: int
    priority: int


def allocate(spaces: tuple[Space, ...], available: int) -> tuple[int, ...]:
    """Reserve readable previews, then give pending input and recent entries room.

    If even the previews cannot fit, the oldest low-priority entry leaves the
    viewport. Returning zero lets the owner retire it without reviving it later.
    """
    heights = [space.minimum for space in spaces]
    remaining = max(0, available) - sum(heights)

    def rank(index: int) -> tuple[int, int]:
        return spaces[index].priority, index

    ranked = sorted(range(len(spaces)), key=rank)
    for index in ranked:
        if remaining >= 0:
            break
        remaining += heights[index]
        heights[index] = 0
    for index in reversed(ranked):
        if heights[index]:
            extra = min(remaining, max(0, spaces[index].preferred - heights[index]))
            heights[index] += extra
            remaining -= extra
    return tuple(heights)


def hover(stack: Stack, key: Key, inside: bool, now: float) -> Stack:
    def update(toast: Toast) -> Toast:
        if toast.key != key:
            return toast
        match toast.lifetime, inside:
            case Expires(at), True:
                return replace(toast, lifetime=Paused(max(0, at - now)))
            case Paused(remaining), False:
                return replace(toast, lifetime=Expires(now + remaining))
            case _:
                return toast

    return replace(stack, toasts=tuple(update(toast) for toast in stack.toasts))


def next_expiry(stack: Stack) -> float | None:
    return min((toast.lifetime.at for toast in stack.toasts if isinstance(toast.lifetime, Expires)), default=None)


def body(content: Content) -> str:
    match content:
        case Entry(message=Question() | Approval() as message):
            return message.text + ("\n\n" + message.detail if message.detail else "")
        case Working():
            return ""
        case Entry(message=message):
            return message.text
        case Response(text=text):
            return text
        case Recording(state):
            return state.detail or state.preview or state.title
        case _:
            assert_never(content)


def heading(content: Content) -> str:
    match content:
        case Working(phase=Busy.STOPPING):
            return "Stopping…"
        case Working(queued=queued):
            return "Toolkit" + (f" · {queued} queued" if queued else "")
        case Entry(message=message):
            return presentation(message).role
        case Response():
            return "You"
        case Recording(state):
            return f"{phase_label(state.phase)} · {duration(state.seconds)}"
        case _:
            assert_never(content)


def hint(content: Content) -> str:
    match content:
        case Entry(message=Question() as message) if pending(message):
            return "Hold N to reply by voice"
        case Entry(message=Approval() as message) if pending(message):
            return "Hold N and say “approve” or “decline”"
        case Recording(state=RecordingState(phase="paused")):
            return "Transcription paused · Retry in Agent session"
        case _:
            return ""


def plain(content: Content) -> bool:
    match content:
        case Entry(message=ToolActivity() | ChatMessage(speaker="You" | "Error")) | Response():
            return True
        case _:
            return False


def spinning(content: Content) -> bool:
    match content:
        case Working(phase=Busy.WAITING):
            return False
        case Working():
            return True
        case Recording(state=RecordingState(phase="starting" | "stopping" | "finishing")):
            return True
        case Entry() | Response() | Recording():
            return False
        case _:
            assert_never(content)


def tone(content: Content) -> Tone:
    match content:
        case Entry(message=ChatMessage(speaker="You")) | Response():
            return Tone.USER
        case Working(phase=Busy.WAITING) | Entry(message=Question() | Approval()):
            return Tone.QUESTION
        case Entry(message=ToolActivity(outcome=Failed()) | ChatMessage(speaker="Error")):
            return Tone.ERROR
        case Entry(message=ToolActivity()):
            return Tone.ACTION
        case Working():
            return Tone.THINKING
        case Entry():
            return Tone.REPLY
        case Recording(state=RecordingState(phase="paused" | "interrupted")):
            return Tone.QUESTION
        case Recording():
            return Tone.RECORDING
        case _:
            assert_never(content)


@dataclass(frozen=True, slots=True)
class Frame:
    y: float
    width: float
    height: float
    alpha: float


@dataclass(frozen=True, slots=True)
class Motion:
    start: Frame
    end: Frame
    started: float

    def sample(self, now: float) -> Frame:
        progress = max(0.0, min(1.0, (now - self.started) / MOTION_SECONDS))
        if progress == 0:
            return self.start
        if progress == 1:
            return self.end
        amount = ease_out(progress)
        return Frame(self.start.y + (self.end.y - self.start.y) * amount,
                     self.start.width + (self.end.width - self.start.width) * amount,
                     self.start.height + (self.end.height - self.start.height) * amount,
                     self.start.alpha + (self.end.alpha - self.start.alpha) * amount)

    def finished(self, now: float) -> bool:
        return now >= self.started + MOTION_SECONDS


def ease_out(progress: float) -> float:
    """Vault of Glass's cubic-bezier(0.32, 0.72, 0, 1), without overshoot."""
    if progress <= 0 or progress >= 1:
        return max(0.0, min(1.0, progress))
    low, high = 0.0, 1.0
    for _ in range(18):
        t = (low + high) / 2
        x = 3 * (1 - t) ** 2 * t * .32 + t ** 3
        if x < progress:
            low = t
        else:
            high = t
    t = (low + high) / 2
    return 3 * (1 - t) ** 2 * t * .72 + 3 * (1 - t) * t ** 2 + t ** 3
