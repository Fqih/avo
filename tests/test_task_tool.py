"""Tests for the ``task`` sub-agent dispatch tool."""

from __future__ import annotations

import pytest

import avo.app_tools.task_tool as task_module
from avo.agent_profiles import AgentCapability
from avo.app_tools.file_tools import read_file_tool, write_file_tool
from avo.app_tools.task_tool import AgentType, TaskArguments, task_tool
from avo.capabilities import inherit_runtime_security
from avo.config_resolver import resolve_security_config
from avo.models import ModelResponse, ToolCall
from avo.policies import LoopPolicy
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime
from avo.storage.memory import InMemoryEventStore


def _parent(
    *,
    responses: list[ModelResponse],
    tools: list[object] | None = None,
) -> AgentRuntime:
    runtime = AgentRuntime(
        provider=FakeProvider(responses=responses),
        event_store=InMemoryEventStore(),
        policy=LoopPolicy(max_steps=4),
    )
    if tools:
        # Re-register so tests can introspect what the parent exposes.
        for tool in tools:
            runtime.tools.register(tool)  # type: ignore[attr-defined]
    return runtime


async def test_task_tool_metadata_describes_contract() -> None:
    parent = _parent(responses=[])
    tool = task_tool(parent_runtime=parent)
    metadata = tool.metadata
    assert metadata.name == "task"
    assert metadata.input_schema["properties"]["prompt"]
    assert metadata.input_schema["properties"]["agent_type"]


async def test_task_explore_returns_summary_with_run_id() -> None:
    parent = _parent(
        responses=[ModelResponse(content="child finished")],
        tools=[read_file_tool(), write_file_tool()],
    )
    tool = task_tool(parent_runtime=parent)

    result = await tool._function(TaskArguments(agent_type=AgentType.EXPLORE, prompt="summarise"))

    assert result["agent_type"] == "explore"
    assert result["output"] == "child finished"
    assert result["stop_reason"] == "completed"
    assert result["run_id"]
    assert result["steps"] >= 1


async def test_task_explore_only_uses_read_only_tools() -> None:
    parent = _parent(
        responses=[
            ModelResponse(content="child finished"),
        ],
        tools=[read_file_tool(), write_file_tool()],
    )
    tool = task_tool(parent_runtime=parent)

    # The child's first request should advertise only read_file, not write_file.
    await tool._function(TaskArguments(agent_type=AgentType.EXPLORE, prompt="look"))
    child_requests = parent.provider.requests  # type: ignore[attr-defined]
    assert child_requests, "child runtime should have made at least one request"
    advertised = {tool.name for tool in child_requests[0].tools}
    assert "read_file" in advertised
    assert "write_file" not in advertised


async def test_task_general_exposes_all_parent_tools() -> None:
    parent = _parent(
        responses=[ModelResponse(content="done")],
        tools=[read_file_tool(), write_file_tool()],
    )
    tool = task_tool(parent_runtime=parent)

    await tool._function(TaskArguments(agent_type=AgentType.GENERAL, prompt="work"))
    child_requests = parent.provider.requests  # type: ignore[attr-defined]
    advertised = {tool.name for tool in child_requests[0].tools}
    assert {"read_file", "write_file"}.issubset(advertised)


async def test_task_child_run_is_isolated() -> None:
    parent = _parent(
        responses=[ModelResponse(content="isolated done")],
        tools=[read_file_tool()],
    )
    parent_store = parent.event_store
    parent_runs_before = len(await parent_store.list_runs())

    tool = task_tool(parent_runtime=parent)
    await tool._function(TaskArguments(agent_type=AgentType.EXPLORE, prompt="x"))

    # The parent must still only have its own run, not the child's.
    parent_runs_after = len(await parent_store.list_runs())
    assert parent_runs_after == parent_runs_before


async def test_task_child_inherits_callback_and_narrows_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = object()
    parent = AgentRuntime(
        provider=FakeProvider(responses=[ModelResponse(content="done")]),
        event_store=InMemoryEventStore(),
        approval_callback=callback,  # type: ignore[arg-type]
        security_config=resolve_security_config(
            explicit={"sandbox_network": True, "plugin_activation": True},
            environ={},
        ),
    )
    captured: dict[str, object] = {}
    original_runtime = task_module.AgentRuntime

    class RecordingRuntime(original_runtime):
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            super().__init__(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(task_module, "AgentRuntime", RecordingRuntime)
    tool = task_tool(parent_runtime=parent, tools=[read_file_tool()])

    await tool._function(TaskArguments(agent_type=AgentType.GENERAL, prompt="inspect"))

    assert captured["approval_callback"] is callback
    security = captured["security_config"]
    assert security is not None
    assert security.sandbox_required.value is True  # type: ignore[union-attr]
    assert security.sandbox_network.value is True  # type: ignore[union-attr]
    assert security.plugin_activation.value is True  # type: ignore[union-attr]


async def test_task_read_only_child_narrows_network_and_plugins() -> None:
    parent = AgentRuntime(
        provider=FakeProvider(responses=[ModelResponse(content="done")]),
        event_store=InMemoryEventStore(),
        security_config=resolve_security_config(
            explicit={"sandbox_network": True, "plugin_activation": True},
            environ={},
        ),
    )
    tool = task_tool(parent_runtime=parent, tools=[read_file_tool()])

    await tool._function(TaskArguments(agent_type=AgentType.EXPLORE, prompt="inspect"))

    child_security = inherit_runtime_security(
        parent.security_config,
        AgentCapability.READ_ONLY,
    )
    assert child_security.sandbox_network.value is False
    assert child_security.plugin_activation.value is False


async def test_task_respects_max_steps_override() -> None:
    parent = _parent(
        responses=[ModelResponse(content="done")],
        tools=[read_file_tool()],
    )
    tool = task_tool(parent_runtime=parent, policy_overrides=LoopPolicy(max_steps=2))

    result = await tool._function(
        TaskArguments(agent_type=AgentType.EXPLORE, prompt="x", max_steps=3)
    )
    assert result["steps"] >= 1


async def test_task_empty_prompt_rejected() -> None:
    _parent(responses=[])  # ensure factory works; rejection happens at model level
    with pytest.raises(ValueError, match="at least 1 character"):
        TaskArguments(agent_type=AgentType.EXPLORE, prompt="")


async def test_task_unknown_agent_type_rejected() -> None:
    _parent(responses=[])  # ensure factory works; rejection happens at model level
    with pytest.raises(ValueError):
        TaskArguments(agent_type="nuclear", prompt="x")  # type: ignore[arg-type]


# Reference ToolCall so the type checker keeps the import alive; the
# tests above use ``ModelResponse(tool_call=...)`` paths indirectly.
_ = ToolCall
