"""OpenAI Agents SDK boundary: tools stay local; the SDK owns the agent loop."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

from __future__ import annotations

import asyncio
import base64
import json
from typing import TYPE_CHECKING, Any, cast

from agents import (
    Agent as SdkAgent,
)
from agents import (
    AgentsException,
    FunctionTool,
    MaxTurnsExceeded,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
    ToolOutputImage,
    ToolOutputText,
    ToolsToFinalOutputResult,
    set_tracing_disabled,
)
from agents.items import ModelResponse
from agents.run_config import CallModelData, ModelInputData, ToolExecutionConfig
from agents.tool import FunctionToolResult
from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient

from .agent import KEPT_SCREENSHOTS
from .llm import ToolCall, ToolSpec

if TYPE_CHECKING:
    from .agent import Agent

set_tracing_disabled(True)


class StoppedError(Exception):
    pass


class Hooks(RunHooks["Agent"]):
    __slots__ = ()

    async def on_llm_start(
        self, context: RunContextWrapper[Agent], agent: SdkAgent[Agent], system_prompt: str | None, input_items: list[Any],
    ) -> None:
        if context.context.stop.is_set():
            raise StoppedError

    async def on_llm_end(self, context: RunContextWrapper[Agent], agent: SdkAgent[Agent], response: ModelResponse) -> None:
        for item in response.output:
            if item.type == "message":
                for part in item.content:
                    if part.type == "output_text" and part.text:
                        context.context.log.say(part.text)


def tool(spec: ToolSpec) -> FunctionTool:
    async def invoke(context: Any, arguments: str) -> object:
        local: Agent = context.context
        if local.stop.is_set() or local.installing is not None:
            return "Stopped by the user." if local.stop.is_set() else "Installation already requested."
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
        raise StoppedError
    if local.installing is not None:
        return ToolsToFinalOutputResult(is_final_output=True, final_output=f"Installing: {local.installing}. The toolkit restarts in a moment.")
    return ToolsToFinalOutputResult(is_final_output=False)


def trim_screenshots(data: CallModelData[Any]) -> ModelInputData:
    """Chat Completions accepts images in user messages, not tool results.

    Keep every tool/call association intact and retain only the latest two shots.
    Build a fresh request without changing the SDK's stored history.
    """
    items = cast(list[Any], data.model_data.input)
    shots = [
        i for i, item in enumerate(items)
        if isinstance(item, dict) and item.get("type") == "function_call_output"
        and isinstance(item.get("output"), list)
        and any(part.get("type") == "input_image" for part in item["output"])
    ]
    kept = set(shots[-KEPT_SCREENSHOTS:])
    converted: list[Any] = []
    pending: list[Any] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            converted.extend(pending)
            pending.clear()
        if i in shots:
            parts = item["output"]
            text = "\n".join(part.get("text", "") for part in parts if part.get("type") == "input_text")
            converted.append({**item, "output": text})
            content = [part for part in parts if part.get("type") == "input_image"] if i in kept else (
                "[an earlier screenshot, removed to save space]"
            )
            pending.append({"role": "user", "content": content})
        else:
            converted.append(item)
    converted.extend(pending)
    return ModelInputData(input=converted, instructions=data.model_data.instructions)


async def run(local: Agent, task: str, prompt: str, specs: tuple[ToolSpec, ...]) -> bool:
    http = DefaultAsyncHttpxClient(trust_env=True)
    async with AsyncOpenAI(
        base_url=local.settings.base_url, api_key=local.settings.api_key, max_retries=0, timeout=120, http_client=http,
    ) as client:
        model = OpenAIChatCompletionsModel(model=local.settings.agent_model, openai_client=client)
        agent: SdkAgent[Agent] = SdkAgent(
            name="Toolkit", instructions=prompt, model=model, tools=[tool(spec) for spec in specs],
            model_settings=ModelSettings(parallel_tool_calls=False), tool_use_behavior=after_tools,
        )
        try:
            result = await Runner.run(
                agent, task, context=local, max_turns=local.max_steps, hooks=Hooks(),
                run_config=RunConfig(
                    tracing_disabled=True, trace_include_sensitive_data=False,
                    tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                    tool_not_found_behavior="return_error_to_model", call_model_input_filter=trim_screenshots,
                ),
            )
        except StoppedError:
            local.log.done("Stopped.")
            return False
        except MaxTurnsExceeded:
            local.log.done(f"Stopped after {local.max_steps} steps, not finished.")
            return False
        except APIError as error:
            local.log.problem(f"Endpoint request failed: {error.message}")
            return False
        except AgentsException as error:
            local.log.problem(f"Agent request failed: {error}")
            return False
        local.log.done(str(result.final_output or "Done."))
        return True
