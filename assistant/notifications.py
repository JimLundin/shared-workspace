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
    Cancelled,
    ChatMessage,
    Failed,
    MeetingMessage,
    Question,
    ReasoningSummary,
    Snapshot,
    StreamState,
    Succeeded,
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


@dataclass(frozen=True, slots=True)
class TurnKey:
    request: int


type Key = Kind | MessageKey | ResponseKey | TurnKey


@dataclass(frozen=True, slots=True)
class Working:
    generation: int
    phase: Busy
    queued: int


class Settled(Enum):
    FINISHED = auto()
    STOPPED = auto()
    ATTENTION = auto()


@dataclass(frozen=True, slots=True)
class Entry:
    index: int
    message: ChatMessage | Answer | ToolActivity | Question | Approval


@dataclass(frozen=True, slots=True)
class Response:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class Turn:
    """One assistant bubble owns its steps and result, even after completion."""

    request: int
    entries: tuple[Entry | Response, ...]
    state: Working | Settled


@dataclass(frozen=True, slots=True)
class Recording:
    state: RecordingState


type Content = Turn | Entry | Response | Recording


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
        case Turn(request):
            return TurnKey(request)
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
        case Turn(entries=entries, state=state):
            return isinstance(state, Working) or any(active(entry) for entry in entries)
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
        case Turn(state=state):
            return isinstance(state, Working)
        case Recording(state):
            return state.capturing
        case Entry() | Response():
            return False
        case _:
            assert_never(content)


def observe(session: Snapshot) -> tuple[Content, ...]:
    result: list[Content] = []
    requests = tuple(index for index, message in enumerate(session.messages) if isinstance(message, ChatMessage) and message.speaker == "You")
    latest = requests[-1] if requests else -1
    initial_input = any(isinstance(message, (Question, Approval)) for message in session.messages[:requests[0] if requests else None])
    request: int | None = -1 if initial_input or (latest == -1 and session.busy) else None
    entries: list[Entry | Response] = []

    def finish_turn() -> None:
        if request is None:
            return
        state: Working | Settled = settled(tuple(entries))
        if request == latest:
            if session.busy or session.waiting:
                phase = Busy.STOPPING if session.stopping else Busy.WAITING if session.waiting else Busy.THINKING
                state = Working(session.generation, phase, len(session.queued))
            elif session.result == "Stopped":
                state = Settled.STOPPED
            elif session.result == "Needs attention":
                state = Settled.ATTENTION
        result.append(Turn(request, tuple(entries), state))

    for index, message in enumerate(session.messages):
        match message:
            case ChatMessage(speaker="You"):
                finish_turn()
                result.append(Entry(index, message))
                request, entries = index, []
            case ChatMessage() | Answer() | ToolActivity() | Question() | Approval():
                target = result if request is None else entries
                target.append(Entry(index, message))
                match message:
                    case Question(reply=Answered(text)):
                        target.append(Response(index, text))
                    case Approval(decision="approved" | "declined" as decision):
                        target.append(Response(index, decision.capitalize()))
                    case _:
                        pass
            case MeetingMessage() | ReasoningSummary():
                pass
            case _:
                assert_never(message)
    finish_turn()
    if session.recording.visible:
        result.append(Recording(session.recording))
    return tuple(result)


def settled(entries: tuple[Entry | Response, ...]) -> Settled:
    for entry in reversed(entries):
        match entry:
            case Entry(message=ChatMessage(speaker="Error") | ToolActivity(outcome=Failed())):
                return Settled.ATTENTION
            case Entry(message=Answer(state=StreamState.INTERRUPTED) | ToolActivity(outcome=ToolStatus.CANCELLED)):
                return Settled.STOPPED
            case Entry(message=ChatMessage(speaker="Toolkit", text=text)) if text.startswith("Stopped"):
                return Settled.STOPPED
            case Entry(message=Question(reply=Cancelled()) | Approval(decision="cancelled")):
                return Settled.STOPPED
            case Entry(message=ChatMessage() | Answer() | ToolActivity(outcome=Succeeded())):
                return Settled.FINISHED
            case _:
                pass
    return Settled.FINISHED


def results(turn: Turn) -> tuple[Entry | Response, ...]:
    """Keep the latest reply and pending input attached when steps are collapsed.

    An answered question stays visible with its response until a new reply or
    question replaces it. Earlier replies and resolved input remain in steps.
    """
    latest = next((entry.index for entry in reversed(turn.entries) if isinstance(entry, Entry)
                   and isinstance(entry.message, (Answer, ChatMessage, Question, Approval))), -1)
    return tuple(entry for entry in turn.entries if entry.index == latest
                 or (isinstance(entry, Entry) and isinstance(entry.message, (Question, Approval)) and pending(entry.message)))


def steps(turn: Turn) -> tuple[Entry | Response, ...]:
    attached = {entry.index for entry in results(turn)}
    return tuple(entry for entry in turn.entries if entry.index not in attached)


def caption(content: Content) -> str:
    if not isinstance(content, Turn):
        return ""
    actions = tuple(entry.message for entry in content.entries if isinstance(entry, Entry) and isinstance(entry.message, ToolActivity))
    count = len(actions)
    if not count:
        return "Show steps" if steps(content) else ""
    complete = sum(isinstance(action.outcome, Succeeded) for action in actions)
    if complete == count:
        return f"{count} completed step" + ("s" if count != 1 else "")
    return f"{complete} of {count} steps complete"


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
    current = next((content.request for content in reversed(observed) if isinstance(content, Turn)), None)
    remembered: list[Content] = []
    toasts: list[Toast] = []
    serial = stack.serial
    for observation in observed:
        content = observation
        key = key_of(content)
        prior = previous.get(key)
        # Session.result belongs only to the newest turn. Preserve an older
        # completion once observed so a follow-up cannot revive it as changed.
        if (isinstance(content, Turn) and content.request != current and isinstance(prior, Turn)
                and isinstance(prior.state, Settled) and content.entries == prior.entries):
            content = replace(content, state=prior.state)
        remembered.append(content)
        toast = existing.get(key)
        changed = previous.get(key) != content
        if toast is None and not changed:
            continue
        if toast is None:
            serial += 1
            toast = Toast(key, content, Held.ACTIVE, serial)
        lifetime = toast.lifetime
        if active(content) or ((session.busy or session.waiting) and isinstance(content, (Turn, Entry, Response))):
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
    return Stack(session.identity, tuple(remembered), visible, serial)


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
        case Turn() if hint(content):
            return 2
        case Turn() | Recording() if active(content):
            return 3
        case Entry(message=Question() | Approval() as message) if pending(message):
            return 2
        case _:
            return int(active(content))


def order_of(toast: Toast) -> tuple[int, int, int]:
    """A turn starts with its request, then thinking and its chronological activity."""
    match toast.content:
        case Entry(index):
            return 0, index, 0
        case Response(index):
            return 0, index, 1
        case Turn(request=request):
            return 0, request, 2
        case Recording():
            return 1, 0, 0
        case _:
            assert_never(toast.content)


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
        case Turn():
            return "\n\n".join(body(entry) for entry in results(content))
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
        case Turn(state=progress):
            match progress:
                case Working(phase=Busy.STOPPING):
                    return "Stopping…"
                case Working(phase=Busy.WAITING):
                    return "Waiting for you"
                case Working(queued=queued):
                    return "Thinking" + (f" · {queued} queued" if queued else "")
                case Settled.FINISHED:
                    return "Finished"
                case Settled.STOPPED:
                    return "Stopped"
                case Settled.ATTENTION:
                    return "Needs attention"
                case _:
                    assert_never(progress)
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
        case Turn():
            return next((text for entry in results(content) if (text := hint(entry))), "")
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
        case Turn(state=Working(phase=Busy.WAITING)):
            return False
        case Turn(state=Working()):
            return True
        case Recording(state=RecordingState(phase="starting" | "stopping" | "finishing")):
            return True
        case Turn() | Entry() | Response() | Recording():
            return False
        case _:
            assert_never(content)


def tone(content: Content) -> Tone:
    match content:
        case Entry(message=ChatMessage(speaker="You")) | Response():
            return Tone.USER
        case Turn() if hint(content):
            return Tone.QUESTION
        case Entry(message=Question() | Approval()):
            return Tone.QUESTION
        case Entry(message=ToolActivity(outcome=Failed()) | ChatMessage(speaker="Error")):
            return Tone.ERROR
        case Entry(message=ToolActivity()):
            return Tone.ACTION
        case Turn(state=Settled.ATTENTION):
            return Tone.ERROR
        case Turn(state=Working()):
            return Tone.THINKING
        case Turn() | Entry():
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
    details: float = 0


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
                     self.start.alpha + (self.end.alpha - self.start.alpha) * amount,
                     self.start.details + (self.end.details - self.start.details) * amount)

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
