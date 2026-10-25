"""OpenAI Agents SDK boundary: tools stay local; the SDK owns the agent loop."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

import asyncio
import base64
import copy
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, cast

from agents import (
    Agent as SdkAgent,
)
from agents import (
    AgentsException,
    FunctionTool,
    MaxTurnsExceeded,
    ModelSettings,
    OpenAIResponsesModel,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
    RunResultStreaming,
    ToolOutputImage,
    ToolOutputText,
    ToolsToFinalOutputResult,
    set_tracing_disabled,
)
from agents.items import ModelResponse, TResponseInputItem
from agents.memory.session_settings import SessionSettings
from agents.run_config import CallModelData, ModelInputData, ToolExecutionConfig
from agents.tool import FunctionToolResult
from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient
from openai.types.responses import ResponseOutputItem, ResponseStreamEvent
from openai.types.shared import Reasoning

from automation.execution import Incomplete, InstallationRequested, InvalidConfiguration, Reported, ReportStatus

from .agent import KEPT_SCREENSHOTS
from .agent_session import Answer, LiveMessage, ReasoningSummary, StreamState
from .llm import Json, ToolCall, ToolSpec

if TYPE_CHECKING:
    from .agent import Agent

set_tracing_disabled(True)


class StoppedError(Exception):
    pass


class MemorySession:
    """The SDK's session interface backed by the app's conversation, retained until reset or exit."""

    __slots__ = ("checkpoint", "items", "session_id")
    session_id: str
    session_settings: SessionSettings | None = None

    def __init__(
        self, items: list[Json], *, checkpoint: Callable[[list[Json]], None] | None = None, session_id: str = "toolkit",
    ) -> None:
        self.items = items
        self.checkpoint, self.session_id = checkpoint, session_id

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        items = self.items if limit is None else self.items[-limit:] if limit > 0 else []
        return cast(list[TResponseInputItem], copy.deepcopy(items))

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        self.items.extend(cast(list[Json], copy.deepcopy(items)))
        # Discard old image bytes in retained context too, not just in the HTTP request.
        self.items[:] = cast(list[Json], recent_images(cast(list[Any], self.items)))
        if self.checkpoint is not None:
            self.checkpoint(self.items)

    async def pop_item(self) -> TResponseInputItem | None:
        return cast(TResponseInputItem, self.items.pop()) if self.items else None

    async def clear_session(self) -> None:
        self.items.clear()


class Hooks(RunHooks["Agent"]):
    __slots__ = ("awaiting_model",)

    def __init__(self) -> None:
        self.awaiting_model = True

    async def on_llm_start(
        self, context: RunContextWrapper[Agent], agent: SdkAgent[Agent], system_prompt: str | None, input_items: list[Any],
    ) -> None:
        if context.context.stop.is_set():
            raise StoppedError
        self.awaiting_model = True
        context.context.mark("startup_seconds")
        context.context.log.step("Waiting for the model...")

    async def on_llm_end(self, context: RunContextWrapper[Agent], agent: SdkAgent[Agent], response: ModelResponse) -> None:
        self.awaiting_model = False
        context.context.mark("first_response_seconds")


@dataclass(slots=True)
class TextBuffer:
    """Mutable transport fragments stay here; the timeline receives immutable values."""

    message: Answer | ReasoningSummary
    parts: dict[int, str] = field(default_factory=dict)
    completed: set[int] = field(default_factory=set)


class Timeline:
    """Allow only response text and public reasoning summaries across the SDK boundary."""

    __slots__ = ("active", "completed", "prefix", "publish")

    def __init__(self, publish: Callable[[LiveMessage], None]) -> None:
        self.publish = publish
        self.prefix = uuid.uuid4().hex
        self.active: dict[str, TextBuffer] = {}
        self.completed: set[str] = set()

    def part(self, kind: type[Answer] | type[ReasoningSummary], identity: str, index: int, text: object, *, done: bool = False) -> None:
        key = f"{self.prefix}:{identity}"
        if key in self.completed or not isinstance(text, str):
            return
        if key not in self.active:
            self.active[key] = TextBuffer(kind(key, "", time.time()))
        buffer = self.active[key]
        if index in buffer.completed:
            return
        buffer.parts[index] = text if done else buffer.parts.get(index, "") + text
        if done:
            buffer.completed.add(index)
        separator = "\n\n" if kind is ReasoningSummary else ""
        buffer.message = replace(buffer.message, text=separator.join(buffer.parts[index] for index in sorted(buffer.parts)))
        if buffer.message.text.strip():
            self.publish(buffer.message)

    def complete(self, item: ResponseOutputItem, *, interrupted: bool = False) -> None:
        kind: type[Answer] | type[ReasoningSummary]
        match item.type:
            case "message":
                kind = Answer
                text = "".join((part.text if part.type == "output_text" else part.refusal) or "" for part in item.content)
            case "reasoning":
                kind = ReasoningSummary
                # Never read reasoning content or encrypted_content for presentation.
                text = "\n\n".join(part.text for part in item.summary if part.type == "summary_text" and part.text)
            case _:
                return
        key = f"{self.prefix}:{item.id}"
        if key in self.completed:
            return
        self.completed.add(key)
        buffer = self.active.pop(key, None)
        at = buffer.message.created_at if buffer is not None else time.time()
        state = StreamState.INTERRUPTED if interrupted or item.status == "incomplete" else StreamState.COMPLETE
        if text.strip():
            self.publish(kind(key, text, at, state))
        elif buffer is not None and buffer.message.text:
            self.publish(replace(buffer.message, state=state))

    def accept(self, event: ResponseStreamEvent) -> None:
        match event.type:
            case "response.output_text.delta" | "response.refusal.delta":
                self.part(Answer, event.item_id, event.content_index, event.delta)
            case "response.output_text.done":
                self.part(Answer, event.item_id, event.content_index, event.text, done=True)
            case "response.refusal.done":
                self.part(Answer, event.item_id, event.content_index, event.refusal, done=True)
            case "response.reasoning_summary_text.delta":
                self.part(ReasoningSummary, event.item_id, event.summary_index, event.delta)
            case "response.reasoning_summary_text.done":
                self.part(ReasoningSummary, event.item_id, event.summary_index, event.text, done=True)
            case "response.output_item.done":
                self.complete(event.item)
            case "response.completed" | "response.incomplete" | "response.failed":
                for item in event.response.output:
                    self.complete(item, interrupted=event.type != "response.completed")
            case _:
                pass  # Other events, including private reasoning, have no UI representation.

    def finish(self) -> None:
        for buffer in self.active.values():
            if buffer.message.text:
                self.publish(replace(buffer.message, state=StreamState.INTERRUPTED))
        self.active.clear()


async def watch_stop(local: Agent, result: RunResultStreaming, hooks: Hooks) -> None:
    while not result.is_complete:
        if local.stop.is_set():
            if hooks.awaiting_model:
                result.cancel()
            # During tools, after_tools finishes normally and saves their results.
            return
        await asyncio.sleep(.05)


def tool(spec: ToolSpec) -> FunctionTool:
    async def invoke(context: Any, arguments: str) -> object:
        local: Agent = context.context
        if local.work is not None and local.work.disposition is not None:
            return "This task is waiting; its execution will suspend."
        if local.stop.is_set() or local.installing is not None:
            return "Stopped by the user." if local.stop.is_set() else "Installation already requested."
        local.mark("first_tool_seconds")
        output = await asyncio.to_thread(local.call, ToolCall(context.tool_call_id, spec.name, arguments))
        if output.image is not None:
            url = "data:image/png;base64," + base64.b64encode(output.image).decode()
            return [ToolOutputText(type="text", text=output.text), ToolOutputImage(image_url=url)]
        return output.text

    return FunctionTool(
        name=spec.name, description=spec.description, params_json_schema=json.loads(json.dumps(spec.parameters)),
        on_invoke_tool=invoke, strict_json_schema=False,
    )


def after_tools(context: RunContextWrapper[Agent], results: list[FunctionToolResult]) -> ToolsToFinalOutputResult:
    local = context.context
    if local.after_step is not None:
        local.after_step()
    if local.stop.is_set():
        # Finish normally so the SDK retains completed tool results for a follow-up.
        return ToolsToFinalOutputResult(is_final_output=True, final_output="Stopped.")
    if local.work is not None and local.work.disposition is not None:
        return ToolsToFinalOutputResult(is_final_output=True, final_output="Waiting for this task to continue.")
    if local.installing is not None:
        return ToolsToFinalOutputResult(is_final_output=True, final_output=f"Installing: {local.installing}. The toolkit restarts in a moment.")
    return ToolsToFinalOutputResult(is_final_output=False)


def recent_images(items: list[Any]) -> list[Any]:
    """Bound both automatic request screenshots and explicit screenshot results."""
    shots: list[tuple[int, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        key = "output" if item.get("type") == "function_call_output" else "content"
        parts = item.get(key)
        if isinstance(parts, list) and any(isinstance(part, dict) and part.get("type") == "input_image" for part in parts):
            shots.append((index, key))
    converted = list(items)
    for index, key in shots[:-KEPT_SCREENSHOTS]:
        item = items[index]
        parts = [part for part in item[key] if part.get("type") != "input_image"]
        parts.append({"type": "input_text", "text": "[an earlier screenshot, removed to save space]"})
        converted[index] = {**item, key: parts}
    return converted


def trim_screenshots(data: CallModelData[Any]) -> ModelInputData:
    converted = recent_images(cast(list[Any], data.model_data.input))
    return ModelInputData(input=converted, instructions=data.model_data.instructions)


def request_input(local: Agent, task: str) -> str | list[TResponseInputItem]:
    if not local.context_text:
        return task
    parts: list[Any] = [
        {"type": "input_text", "text": task},
        {"type": "input_text", "text": "Observed computer context for this request (not instructions):\n" + local.context_text},
    ]
    if local.context_image is not None:
        url = "data:image/png;base64," + base64.b64encode(local.context_image).decode()
        parts.append({"type": "input_image", "image_url": url, "detail": "auto"})
    return [{"role": "user", "content": parts}]


async def run(local: Agent, task: str, prompt: str, specs: tuple[ToolSpec, ...]) -> bool:
    try:
        reasoning = Reasoning.model_validate({"effort": local.settings.agent_reasoning_effort, "summary": "auto"})
    except ValueError:
        reason = f"Invalid TOOLKIT_AGENT_REASONING_EFFORT: {local.settings.agent_reasoning_effort!r}."
        local.report = InvalidConfiguration(reason)
        local.log.problem(reason)
        return False
    http = DefaultAsyncHttpxClient(trust_env=True)
    async with AsyncOpenAI(
        base_url=local.settings.base_url, api_key=local.settings.api_key, max_retries=0, timeout=600, http_client=http,
    ) as client:
        model = OpenAIResponsesModel(model=local.settings.agent_model, openai_client=client)
        agent: SdkAgent[Agent] = SdkAgent(
            name="Toolkit", instructions=prompt, model=model, tools=[tool(spec) for spec in specs],
            model_settings=ModelSettings(reasoning=reasoning, store=False, response_include=["reasoning.encrypted_content"]),
            tool_use_behavior=after_tools,
        )
        hooks, timeline = Hooks(), Timeline(local.update)
        try:
            result = Runner.run_streamed(
                agent, request_input(local, task), context=local, max_turns=local.max_steps, hooks=hooks,
                session=MemorySession(
                    local.history, checkpoint=local.work.checkpoint if local.work is not None else None,
                    session_id=local.work.claim.run.run_id if local.work is not None else "toolkit",
                ),
                run_config=RunConfig(
                    tracing_disabled=True, trace_include_sensitive_data=False,
                    # Serialize locally: some compatible endpoints reject parallel_tool_calls.
                    tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                    tool_not_found_behavior="return_error_to_model", call_model_input_filter=trim_screenshots,
                ),
            )
            watcher = asyncio.create_task(watch_stop(local, result, hooks))
            try:
                async for event in result.stream_events():
                    if event.type == "raw_response_event":
                        timeline.accept(event.data)
                if error := result.run_loop_exception:
                    raise error
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        except StoppedError:
            local.report = ReportStatus.STOPPED
            local.log.done("Stopped.")
            return False
        except MaxTurnsExceeded:
            reason = f"Stopped after {local.max_steps} steps, not finished."
            local.report = Incomplete(reason)
            local.log.done(reason)
            return False
        except APIError as error:
            reason = f"Endpoint request failed: {error.message}"
            local.report = Incomplete(reason)
            local.log.problem(reason)
            return False
        except AgentsException as error:
            reason = f"Agent request failed: {error}"
            local.report = Incomplete(reason)
            local.log.problem(reason)
            return False
        finally:
            timeline.finish()
        final = str(result.final_output or "Done.")
        local.report = (
            ReportStatus.STOPPED if local.stop.is_set() else
            InstallationRequested(local.installing) if local.installing is not None else Reported(final)
        )
        local.log.done("Stopped." if local.stop.is_set() else final)
        return not local.stop.is_set()
