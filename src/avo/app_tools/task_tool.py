"""``task`` tool: dispatch an isolated sub-agent run.

The parent runtime builds a fresh :class:`AgentRuntime` for every call
and runs it to a terminal state inside this tool's coroutine. The child
has its own event store (in-memory) so its events do not bleed into
the parent's audit trail, and its tool list is filtered by
``agent_type`` so an ``explore`` agent cannot mutate the workspace.

Tool selection is delegated to the caller via ``tool_selector`` so the
parent runtime owns the registry and the sub-agent inherits only the
tools the operator wants to expose. By default the selector keeps the
read-only tools (``read_file``) and drops everything else for
``explore`` runs; ``general`` runs receive the full tool list.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, JsonValue

from avo import FunctionTool as PublicFunctionTool
from avo.capabilities import filter_tools
from avo.models import ToolCall
from avo.policies import LoopPolicy
from avo.runtime import AgentRuntime
from avo.storage.memory import InMemoryEventStore
from avo.tools import Tool


class AgentType(StrEnum):
    """The preset tool subsets a sub-agent run may use."""

    EXPLORE = "explore"
    GENERAL = "general"


class TaskArguments(BaseModel):
    """Arguments for the ``task`` tool."""

    agent_type: AgentType = Field(default=AgentType.EXPLORE)
    prompt: str = Field(min_length=1)
    max_steps: int | None = Field(default=None, gt=0)


# Tool selector signature: given the agent type, return the tool list.
ToolSelector = Callable[[AgentType], list[Tool]]


def _default_tool_selector(
    all_tools: list[Tool],
) -> ToolSelector:
    """Build a selector that filters ``all_tools`` by ``AgentType`` preset."""

    by_name: dict[str, Tool] = {tool.metadata.name: tool for tool in all_tools}

    def select(agent_type: AgentType) -> list[Tool]:
        if agent_type is AgentType.GENERAL:
            return list(by_name.values())
        # EXPLORE (and any future read-only preset): keep only read-only tools.
        return filter_tools(list(by_name.values()), read_only=True)

    return select


async def _run_task(
    arguments: TaskArguments,
    *,
    parent_runtime: AgentRuntime,
    selector: ToolSelector,
    policy_overrides: LoopPolicy | None,
) -> dict[str, JsonValue]:
    """Build a child runtime, run it to terminal, and return the summary."""

    child_policy = policy_overrides or LoopPolicy(
        max_steps=arguments.max_steps or parent_runtime.policy.max_steps
    )
    child = AgentRuntime(
        provider=parent_runtime.provider,
        event_store=InMemoryEventStore(),
        tools=selector(arguments.agent_type),
        policy=child_policy,
    )
    result = await child.run(arguments.prompt)
    payload: dict[str, JsonValue] = {
        "agent_type": arguments.agent_type.value,
        "run_id": result.run_id,
        "output": result.output,
        "stop_reason": result.stop_reason.value,
        "steps": result.steps,
        "token_usage": {
            "input_tokens": result.token_usage.input_tokens,
            "output_tokens": result.token_usage.output_tokens,
        },
    }
    return payload


def task_tool(
    *,
    parent_runtime: AgentRuntime,
    tools: list[Tool] | None = None,
    policy_overrides: LoopPolicy | None = None,
) -> PublicFunctionTool[TaskArguments]:
    """Return a :class:`FunctionTool` that dispatches sub-agent runs."""

    if tools is None:
        # Use the parent's registered tools as the candidate set.
        tools = list(parent_runtime.tools._tools.values())
    selector = _default_tool_selector(tools)

    async def function(arguments: TaskArguments) -> JsonValue:
        return await _run_task(
            arguments,
            parent_runtime=parent_runtime,
            selector=selector,
            policy_overrides=policy_overrides,
        )

    return PublicFunctionTool(
        name="task",
        description=(
            "Dispatch an isolated sub-agent run. agent_type='explore' restricts "
            "the child to read-only tools (read_file) so it cannot mutate the "
            "workspace; agent_type='general' exposes the full parent tool set. "
            "Returns a summary dict (run_id, output, stop_reason, steps, token_usage)."
        ),
        arguments_model=TaskArguments,
        function=function,
    )


__all__ = ["AgentType", "TaskArguments", "task_tool"]


# Keep unused-import placeholders so static checkers stay quiet without
# importing names we do not actually use.
_ = (Awaitable, Callable, Any, ToolCall)
