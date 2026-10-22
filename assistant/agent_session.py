"""Immutable conversation values and pure transitions for the agent timeline.

Clocks, IDs, waiting, locking, and widgets belong to the controller and input
broker. Replaying the same events against a session always gives the same result.
"""

from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Literal, assert_never

from .recording_state import RecordingState

type Decision = Literal["pending", "approved", "declined", "cancelled"]
type Result = Literal["Ready", "Stopped", "Needs attention"]


@dataclass(frozen=True, slots=True)
class Pending:
    pass


@dataclass(frozen=True, slots=True)
class Answered:
    text: str


@dataclass(frozen=True, slots=True)
class Cancelled:
    pass


type Reply = Pending | Answered | Cancelled


@dataclass(frozen=True, slots=True)
class ChatMessage:
    speaker: Literal["You", "Toolkit", "Error"]
    text: str
    created_at: float


class StreamState(Enum):
    RUNNING = auto()
    COMPLETE = auto()
    INTERRUPTED = auto()


@dataclass(frozen=True, slots=True)
class Answer:
    identity: str
    text: str
    created_at: float
    state: StreamState = StreamState.RUNNING


@dataclass(frozen=True, slots=True)
class ReasoningSummary:
    identity: str
    text: str
    created_at: float
    state: StreamState = StreamState.RUNNING


class ToolStatus(Enum):
    RUNNING = auto()
    CANCELLED = auto()


@dataclass(frozen=True, slots=True)
class Succeeded:
    text: str


@dataclass(frozen=True, slots=True)
class Failed:
    text: str


type ToolOutcome = ToolStatus | Succeeded | Failed


@dataclass(frozen=True, slots=True)
class ToolActivity:
    identity: str
    name: str
    detail: str
    created_at: float
    outcome: ToolOutcome = ToolStatus.RUNNING

    @property
    def text(self) -> str:
        match self.outcome:
            case ToolStatus.RUNNING:
                return self.detail
            case Succeeded(text) | Failed(text):
                return "\n".join(part for part in (self.detail, text) if part)
            case ToolStatus.CANCELLED:
                return self.detail
            case _:
                assert_never(self.outcome)


type LiveMessage = Answer | ReasoningSummary | ToolActivity


@dataclass(frozen=True, slots=True)
class MeetingMessage:
    recording_id: str
    text: str
    created_at: float


@dataclass(frozen=True, slots=True)
class Question:
    request_id: str
    text: str
    detail: str
    created_at: float
    reply: Reply = Pending()


@dataclass(frozen=True, slots=True)
class Approval:
    request_id: str
    text: str
    detail: str
    created_at: float
    decision: Decision = "pending"


type Message = ChatMessage | MeetingMessage | Question | Approval | LiveMessage


@dataclass(frozen=True, slots=True)
class Begin:
    text: str
    at: float
    queued: bool = False


@dataclass(frozen=True, slots=True)
class Enqueued:
    text: str


@dataclass(frozen=True, slots=True)
class Progress:
    text: str


@dataclass(frozen=True, slots=True)
class Said:
    text: str
    at: float


@dataclass(frozen=True, slots=True)
class Done:
    text: str
    at: float


@dataclass(frozen=True, slots=True)
class Problem:
    text: str
    at: float


@dataclass(frozen=True, slots=True)
class Finished:
    pass


@dataclass(frozen=True, slots=True)
class StopRequested:
    pass


@dataclass(frozen=True, slots=True)
class QuestionAnswered:
    identity: str
    text: str


@dataclass(frozen=True, slots=True)
class ApprovalDecided:
    identity: str
    decision: Literal["approved", "declined"]


@dataclass(frozen=True, slots=True)
class InputCancelled:
    identity: str


@dataclass(frozen=True, slots=True)
class RecordingUpdated:
    state: RecordingState
    at: float


type Resolution = QuestionAnswered | ApprovalDecided | InputCancelled
type Event = (
    Begin | Enqueued | Progress | Said | Done | Problem | Finished | StopRequested | Question | Approval | Resolution | RecordingUpdated | LiveMessage
)


def pending(message: Message) -> bool:
    match message:
        case Question(reply=Pending()) | Approval(decision="pending"):
            return True
        case ChatMessage() | MeetingMessage() | Question() | Approval() | Answer() | ReasoningSummary() | ToolActivity():
            return False
        case _:
            assert_never(message)


@dataclass(frozen=True, slots=True)
class Session:
    identity: str = ""
    generation: int = 0
    messages: tuple[Message, ...] = ()
    queued: tuple[str, ...] = ()
    activity: tuple[str, ...] = ()
    phase: Literal["idle", "running", "stopping"] = "idle"
    status: str = "Ready"
    last_said: str = ""
    result: Result = "Ready"
    recording: RecordingState = field(default_factory=RecordingState)

    @property
    def busy(self) -> bool:
        return self.phase != "idle"

    @property
    def stopping(self) -> bool:
        return self.phase == "stopping"

    @property
    def waiting(self) -> bool:
        return any(pending(message) for message in self.messages)

    @property
    def question(self) -> Question | None:
        return next((message for message in self.messages if isinstance(message, Question) and isinstance(message.reply, Pending)), None)

    def all_text(self) -> str:
        return "\n\n".join(message_text(message) for message in self.messages)


# The view receives the immutable session itself, without a second state model.
type Snapshot = Session


def transition(state: Session, event: Event) -> Session:
    match event:
        case Begin(text, at, queued):
            return replace(state, generation=state.generation + 1, phase="running", status="Starting…", result="Ready", last_said="",
                           queued=state.queued[1:] if queued else state.queued,
                           messages=(*state.messages, ChatMessage("You", text.strip(), at)))
        case Enqueued(text):
            return replace(state, queued=(*state.queued, text.strip()))
        case Progress(text):
            text = text.strip()
            status = state.status if state.stopping or state.waiting else "Thinking…" if text == "Waiting for the model..." else "Working…"
            return replace(state, status=status, activity=state.activity if text == "Waiting for the model..." else (*state.activity, text))
        case Said(text, at):
            text = text.strip()
            return replace(state, messages=(*state.messages, ChatMessage("Toolkit", text, at)), last_said=text) if text else state
        case Done(text, at):
            text = text.strip()
            messages = (*state.messages, ChatMessage("Toolkit", text, at)) if text and text != state.last_said else state.messages
            return replace(state, messages=messages, result="Stopped" if text.startswith("Stopped") else "Ready")
        case Problem(text, at):
            return replace(state, messages=(*state.messages, ChatMessage("Error", text.strip(), at)),
                           status="Needs attention", result="Needs attention")
        case Finished():
            return replace(state, phase="idle", queued=(), status=state.result, messages=tuple(interrupt(message) for message in state.messages))
        case StopRequested():
            return replace(state, queued=(), phase="stopping" if state.busy else state.phase, status="Stopping…" if state.busy else state.status)
        case Question() | Approval():
            return replace(state, messages=(*state.messages, event),
                           status="Waiting for your reply" if isinstance(event, Question) else "Needs your approval")
        case QuestionAnswered() | ApprovalDecided() | InputCancelled():
            return resolve(state, event)
        case RecordingUpdated(recording, at):
            return update_recording(state, recording, at)
        case Answer() | ReasoningSummary() | ToolActivity():
            return update_live(state, event)
        case _:
            assert_never(event)


def interrupt(message: Message) -> Message:
    match message:
        case Answer(state=StreamState.RUNNING) | ReasoningSummary(state=StreamState.RUNNING):
            return replace(message, state=StreamState.INTERRUPTED)
        case ToolActivity(outcome=ToolStatus.RUNNING):
            return replace(message, outcome=ToolStatus.CANCELLED)
        case _:
            return message


def update_live(state: Session, event: LiveMessage) -> Session:
    """Replace one live row without duplicating it when the final item arrives."""
    if not state.busy or (state.stopping and not isinstance(event, ToolActivity)):
        return state
    if state.stopping and isinstance(event, ToolActivity) and event.outcome is ToolStatus.RUNNING:
        return state
    for index, message in enumerate(state.messages):
        if (not isinstance(message, (Answer, ReasoningSummary, ToolActivity))
                or message.identity != event.identity or type(message) is not type(event)):
            continue
        match message:
            case (Answer(state=StreamState.COMPLETE | StreamState.INTERRUPTED)
                  | ReasoningSummary(state=StreamState.COMPLETE | StreamState.INTERRUPTED)):
                return state
            case ToolActivity(outcome=Succeeded() | Failed() | ToolStatus.CANCELLED):
                return state
            case Answer() | ReasoningSummary() | ToolActivity():
                pass
        updated = replace(event, created_at=message.created_at)
        if updated == message:
            return state
        messages = (*state.messages[:index], updated, *state.messages[index + 1:])
        break
    else:
        if state.stopping:
            return state
        messages = (*state.messages, event)
    last_said = event.text.strip() if isinstance(event, Answer) and event.state is StreamState.COMPLETE else state.last_said
    return replace(state, messages=messages, last_said=last_said)


def resolve(state: Session, event: Resolution) -> Session:
    for index, message in enumerate(state.messages):
        if not isinstance(message, (Question, Approval)) or message.request_id != event.identity or not pending(message):
            continue
        updated: Message
        match message, event:
            case Question(), QuestionAnswered(text=text):
                updated = replace(message, reply=Answered(text))
            case Approval(), ApprovalDecided(decision=decision):
                updated = replace(message, decision=decision)
            case Question(), InputCancelled():
                updated = replace(message, reply=Cancelled())
            case Approval(), InputCancelled():
                updated = replace(message, decision="cancelled")
            case _:
                return state  # a response for another kind of input cannot resolve this one
        return replace(state, messages=(*state.messages[:index], updated, *state.messages[index + 1:]),
                       status="Stopping…" if state.stopping else "Working…")
    return state


def update_recording(state: Session, recording: RecordingState, at: float) -> Session:
    if not recording.identity:
        return replace(state, recording=recording)
    empty = "No speech was transcribed." if recording.phase == "saved" else "The transcript will appear here as speech is transcribed."
    text = f"**{recording.title}**\n\n" + (recording.preview or empty)
    if recording.detail:
        text += "\n\n" + recording.detail
    match recording.phase:
        case "saved":
            text += "\n\n*Transcript saved. Use Open for the full text.*"
        case "interrupted":
            text += "\n\n*Open the transcript for the saved part.*"
        case "idle" | "starting" | "recording" | "stopping" | "finishing" | "paused":
            pass
        case _:
            assert_never(recording.phase)
    for index, message in enumerate(state.messages):
        if isinstance(message, MeetingMessage) and message.recording_id == recording.identity:
            messages = state.messages if message.text == text else (*state.messages[:index], replace(message, text=text), *state.messages[index + 1:])
            return replace(state, recording=recording, messages=messages)
    return replace(state, recording=recording, messages=(*state.messages, MeetingMessage(recording.identity, text, at)))


def question_response(reply: Reply) -> str:
    match reply:
        case Pending():
            return "pending"
        case Answered(text):
            return text
        case Cancelled():
            return "cancelled"
        case _:
            assert_never(reply)


def message_text(message: Message) -> str:
    parts: tuple[str, ...]
    match message:
        case ChatMessage(speaker, text):
            parts = (speaker, text)
        case Answer(text=text):
            parts = ("Toolkit", text)
        case ReasoningSummary(text=text):
            parts = ("Reasoning summary", text)
        case ToolActivity(name=name):
            parts = (name, message.text)
        case MeetingMessage(text=text):
            parts = ("Meeting", text)
        case Question(text=text, detail=detail, reply=reply):
            parts = ("You", text, detail, "answered", reply.text) if isinstance(reply, Answered) else ("You", text, detail, question_response(reply))
        case Approval(text=text, detail=detail, decision=decision):
            parts = ("You", text, detail, decision)
        case _:
            assert_never(message)
    return "\n".join(part for part in parts if part)


@dataclass(frozen=True, slots=True)
class ApprovalControls:
    identity: str


@dataclass(frozen=True, slots=True)
class ResponseText:
    text: str


@dataclass(frozen=True, slots=True)
class Presentation:
    role: str
    style: Literal["plain", "error", "markdown"] = "markdown"
    detail: str = ""
    response: ApprovalControls | ResponseText | None = None
    muted: bool = False


def question_presentation(detail: str, reply: Reply) -> Presentation:
    match reply:
        case Pending():
            return Presentation("Question", detail=detail)
        case Answered(text):
            return Presentation("Question", detail=detail, response=ResponseText(text))
        case Cancelled():
            return Presentation("Question", detail=detail, response=ResponseText("Cancelled"))
        case _:
            assert_never(reply)


def presentation(message: Message) -> Presentation:
    match message:
        case ChatMessage(speaker="You"):
            return Presentation("You", "plain")
        case ChatMessage(speaker="Error"):
            return Presentation("Error", "error")
        case ChatMessage():
            return Presentation("Agent")
        case Answer(state=state):
            return Presentation("Agent · interrupted" if state is StreamState.INTERRUPTED else "Agent")
        case ReasoningSummary(state=state):
            label = "Reasoning summary · interrupted" if state is StreamState.INTERRUPTED else "Reasoning summary"
            return Presentation(label, muted=True)
        case ToolActivity(name=name, outcome=outcome):
            match outcome:
                case ToolStatus.RUNNING:
                    status = "running"
                case Succeeded():
                    status = "done"
                case Failed():
                    status = "failed"
                case ToolStatus.CANCELLED:
                    status = "stopped"
                case _:
                    assert_never(outcome)
            return Presentation(f"{name.replace('_', ' ').capitalize()} · {status}", "plain", muted=True)
        case MeetingMessage():
            return Presentation("Meeting")
        case Question(detail=detail, reply=reply):
            return question_presentation(detail, reply)
        case Approval(request_id=identity, detail=detail, decision="pending"):
            return Presentation("Approval", detail=detail, response=ApprovalControls(identity))
        case Approval(detail=detail, decision=decision):
            return Presentation("Approval", detail=detail, response=ResponseText(decision.capitalize()))
        case _:
            assert_never(message)
