"""The answer and optional steps of a conversation bubble, without UI effects."""

from .action_text import title
from .agent_session import Answer, Failed, Succeeded, ToolActivity, ToolStatus
from .notifications import Content, Entry, Turn, body, heading, plain, results, steps
from .rich_text import Span, formatted


def answer(content: Content | None) -> Answer | None:
    match content:
        case Entry(message=Answer() as message):
            return message
        case Turn():
            return next((entry.message for entry in results(content) if isinstance(entry, Entry) and isinstance(entry.message, Answer)), None)
        case _:
            return None


def spans(content: Content) -> tuple[Span, ...]:
    if isinstance(content, Turn):
        parts = [spans(entry) for entry in results(content)]
        return tuple(span for index, part in enumerate(parts) for span in ((Span("\n\n"),) if index else ()) + part)
    text = body(content)
    return ((Span(text),) if plain(content) else tuple(formatted(text))) if text else ()


def details(content: Content) -> tuple[Span, ...]:
    if not isinstance(content, Turn):
        return ()
    result: list[Span] = []
    for entry in steps(content):
        if result:
            result.append(Span("\n\n"))
        label, symbol, style = heading(entry), "·", "step"
        match entry:
            case Entry(message=ToolActivity(name=name, outcome=outcome)):
                label = title(name)
                match outcome:
                    case Succeeded():
                        symbol = "✓"
                    case Failed():
                        symbol, style = "!", "step_error"
                    case ToolStatus.CANCELLED:
                        symbol = "\u2013"
                    case ToolStatus.RUNNING:
                        pass
            case _:
                pass
        result.append(Span(f"{symbol}  {label}\n", ("bold", style)))
        result.append(Span(body(entry), ("step_detail",)))
    return tuple(result)
