"""The real SDK, a held-open HTTP stream, and the actual session controller."""

import json
import queue
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager

from assistant.agent import Agent
from assistant.agent_session import Answer, LiveMessage, ReasoningSummary, StreamState, Succeeded, ToolActivity, ToolStatus
from assistant.llm import Json, Settings

from .fake_endpoint import FakeEndpoint, Received, fake_endpoint
from .test_agent import FakeMachine, FakeWindow, calls, message, response, says
from .test_agent_window import RecordingWindow


def event(kind: str, **fields: Json) -> bytes:
    payload: dict[str, Json] = {"type": kind, "sequence_number": 0}
    payload.update(fields)
    return ("data: " + json.dumps(payload) + "\n\n").encode()


class ObservedWindow(RecordingWindow):
    __slots__ = ("events",)

    def __init__(self, stop: Callable[[], None]) -> None:
        super().__init__(stop)
        self.events: queue.Queue[LiveMessage] = queue.Queue()

    def updates(self) -> Callable[[LiveMessage], None]:
        publish = super().updates()

        def observe(value: LiveMessage) -> None:
            publish(value)
            self.events.put(value)

        return observe


def make_agent(endpoint: FakeEndpoint, window: ObservedWindow, stop: threading.Event, *, machine: FakeMachine | None = None) -> Agent:
    window.begin("Read the workbook", stop.set)
    settings = Settings(endpoint.url, "k", "transcribe", "chat", "gpt-6-astra", "", "")
    return Agent(settings, machine if machine is not None else FakeMachine(), window, stop, journal=lambda _: None)


@contextmanager
def running(agent: Agent, panel: ObservedWindow) -> Generator[threading.Thread]:
    errors: list[Exception] = []

    def run() -> None:
        try:
            agent.run("Read the workbook")
        except Exception as error:
            errors.append(error)
        finally:
            panel.finish()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        yield worker
    finally:
        agent.stop.set()
        worker.join(timeout=3)
        assert not worker.is_alive(), "Stopping left the agent blocked on the stream."
        assert not errors, errors


def test_summaries_and_answers_are_visible_before_the_response_finishes() -> None:
    release, stop = threading.Event(), threading.Event()
    panel = ObservedWindow(stop.set)
    summary: Json = {"type": "reasoning", "id": "rs_live", "summary": [{"type": "summary_text", "text": "Checking **the workbook**."}]}
    answer: Json = {"type": "message", "id": "msg_live", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "Found **budget.xlsx**.", "annotations": []}]}

    def stream(_: Received) -> Generator[bytes]:
        yield event("response.reasoning_summary_text.delta", item_id="rs_live", output_index=0, summary_index=0,
                    delta="Checking **the workbook**.")
        yield event("response.output_text.delta", item_id="msg_live", output_index=1, content_index=0, delta="Found **budget")
        release.wait(timeout=5)
        yield event("response.output_text.delta", item_id="msg_live", output_index=1, content_index=0, delta=".xlsx**.")
        yield event("response.output_text.done", item_id="msg_live", output_index=1, content_index=0, text="Found **budget.xlsx**.")
        yield event("response.output_item.done", output_index=0, item=summary)
        yield event("response.output_item.done", output_index=1, item=answer)
        yield response(summary, answer).body

    with fake_endpoint() as endpoint:
        endpoint.stream = stream
        agent = make_agent(endpoint, panel, stop)
        with running(agent, panel) as worker:
            try:
                assert isinstance(panel.events.get(timeout=3), ReasoningSummary)
                assert isinstance(panel.events.get(timeout=3), Answer)
                assert worker.is_alive() and not release.is_set()
                panel.paint()
                partial = panel.view.states[-1]
                assert partial.messages[-1].text == "Found **budget"
                assert partial.busy and not panel.view.shown
            finally:
                release.set()
            worker.join(timeout=3)
            assert not worker.is_alive()
            assert len(panel.state.messages) == 3  # user, one summary, one answer
            assert isinstance(panel.state.messages[-1], Answer)
            assert panel.state.messages[-1].state is StreamState.COMPLETE
            assert panel.state.all_text().count("Found **budget.xlsx**.") == 1
            assert partial.messages[-1].text == "Found **budget"  # immutable snapshots


def test_summary_parts_stay_separate_and_private_reasoning_never_reaches_the_view() -> None:
    stop = threading.Event()
    panel = ObservedWindow(stop.set)
    summary: Json = {
        "type": "reasoning", "id": "rs_parts", "status": "completed", "encrypted_content": "opaque-private-payload",
        "content": [{"type": "reasoning_text", "text": "private-reasoning-content"}],
        "summary": [{"type": "summary_text", "text": "First check."}, {"type": "summary_text", "text": "Second check."}],
    }
    answer: Json = {
        "type": "message", "id": "msg_parts", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": "First paragraph.\n\n", "annotations": []},
                    {"type": "output_text", "text": "Second paragraph.", "annotations": []}],
    }

    def stream(_: Received) -> Generator[bytes]:
        yield event("response.reasoning_text.delta", item_id="rs_parts", output_index=0, content_index=0, delta="private-raw-delta")
        yield event("response.reasoning_text.done", item_id="rs_parts", output_index=0, content_index=0, text="private-raw-done")
        yield event("response.reasoning_summary_text.delta", item_id="rs_parts", output_index=0, summary_index=1, delta="Second")
        yield event("response.reasoning_summary_text.done", item_id="rs_parts", output_index=0, summary_index=1, text="Second check.")
        yield event("response.reasoning_summary_text.delta", item_id="rs_parts", output_index=0, summary_index=1, delta="duplicate")
        yield event("response.reasoning_summary_text.done", item_id="rs_parts", output_index=0, summary_index=0, text="First check.")
        yield event("response.output_text.done", item_id="msg_parts", output_index=1, content_index=1, text="Second paragraph.")
        yield event("response.output_text.done", item_id="msg_parts", output_index=1, content_index=0, text="First paragraph.\n\n")
        yield event("response.output_item.done", output_index=0, item=summary)
        yield event("response.reasoning_summary_text.delta", item_id="rs_parts", output_index=0, summary_index=0, delta="late delta")
        yield response(summary, answer).body

    with fake_endpoint() as endpoint:
        endpoint.stream = stream
        agent = make_agent(endpoint, panel, stop)
        agent.run("Read the workbook")
        panel.finish()
        assert [item.text for item in panel.state.messages] == [
            "Read the workbook", "First check.\n\nSecond check.", "First paragraph.\n\nSecond paragraph.",
        ]
        while not panel.events.empty():
            value = panel.events.get_nowait()
            assert "private" not in value.text and "duplicate" not in value.text and "late delta" not in value.text
        assert "opaque-private-payload" in json.dumps(agent.history), "Model history still needs the opaque reasoning item."
        assert not panel.state.activity


def test_a_tool_is_shown_running_before_it_returns_and_completes_the_same_row() -> None:
    release, entered, stop = threading.Event(), threading.Event(), threading.Event()
    panel = ObservedWindow(stop.set)

    class HeldClipboard(FakeMachine):
        def get_clipboard(self) -> str:
            entered.set()
            assert release.wait(timeout=5)
            return "Fixture workbook name"

    with fake_endpoint() as endpoint:
        endpoint.replies = [calls(("get_clipboard", {})), says("Found it.")]
        agent = make_agent(endpoint, panel, stop, machine=HeldClipboard())
        with running(agent, panel) as worker:
            try:
                assert entered.wait(timeout=3)
                panel.paint()
                activity = [item for item in panel.state.messages if isinstance(item, ToolActivity)]
                assert len(activity) == 1 and activity[0].outcome is ToolStatus.RUNNING
                assert worker.is_alive()
            finally:
                release.set()
            worker.join(timeout=3)
            assert not worker.is_alive()
            completed = [item for item in panel.state.messages if isinstance(item, ToolActivity)]
            assert len(completed) == 1 and completed[0].identity == activity[0].identity
            assert completed[0].outcome == Succeeded("Fixture workbook name")


def test_stop_closes_a_stalled_stream_and_preserves_earlier_tool_results() -> None:
    release, stop = threading.Event(), threading.Event()
    panel = ObservedWindow(stop.set)
    requests = 0

    def stream(_: Received) -> Generator[bytes]:
        nonlocal requests
        requests += 1
        if requests == 1:
            yield calls(("get_clipboard", {})).body
            return
        yield event("response.output_text.delta", item_id="msg_stalled", output_index=0, content_index=0, delta="The workbook")
        release.wait(timeout=5)
        yield response(message("Too late.")).body

    with fake_endpoint() as endpoint:
        endpoint.stream = stream
        agent = make_agent(endpoint, panel, stop)
        with running(agent, panel) as worker:
            try:
                while True:
                    value = panel.events.get(timeout=3)
                    if isinstance(value, Answer) and value.text == "The workbook":
                        break
                panel.stopping()
                stop.set()
                worker.join(timeout=2)
                assert not worker.is_alive(), "Stop waited for the server to send more data."
                assert not release.is_set()
                assert panel.state.status == "Stopped"
                interrupted = [item for item in panel.state.messages if isinstance(item, Answer) and item.text == "The workbook"]
                assert len(interrupted) == 1 and interrupted[0].state is StreamState.INTERRUPTED
            finally:
                release.set()
        endpoint.stream = None
        endpoint.replies = [says("It said clipped.")]
        follow_up = Agent(agent.settings, agent.machine, FakeWindow(), threading.Event(), journal=lambda _: None, history=agent.history)
        assert follow_up.run("What did it say?")
        assert b'"output":"clipped"' in endpoint.last.body
        assert b"Too late." not in endpoint.last.body and b"The workbook" not in endpoint.last.body


def test_ending_a_stream_early_keeps_visible_partial_text_and_reports_the_failure() -> None:
    stop = threading.Event()
    panel = ObservedWindow(stop.set)

    def stream(_: Received) -> Generator[bytes]:
        yield event("response.reasoning_summary_text.delta", item_id="rs_failed", output_index=0, summary_index=0, delta="Checking files.")
        yield event("response.output_text.delta", item_id="msg_failed", output_index=1, content_index=0, delta="I found")
        # EOF without the required response.completed event.

    with fake_endpoint() as endpoint:
        endpoint.stream = stream
        agent = make_agent(endpoint, panel, stop)
        assert not agent.run("Read the workbook")
        panel.finish()
        assert panel.state.status == "Needs attention"
        partials = [item for item in panel.state.messages if isinstance(item, (Answer, ReasoningSummary))]
        assert [item.text for item in partials] == ["Checking files.", "I found"]
        assert all(item.state is StreamState.INTERRUPTED for item in partials)
