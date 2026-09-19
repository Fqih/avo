"""Tests for bounded, isolated child-agent delegation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import JsonValue

from avo import delegation as delegation_module
from avo.agent_profiles import AgentCapability, AgentProfileRegistry
from avo.config_resolver import resolve_security_config
from avo.delegation import DelegationCoordinator, child_tools
from avo.models import ModelRequest, ModelResponse, ToolCall
from avo.policies import LoopPolicy
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime
from avo.storage.memory import InMemoryEventStore
from avo.tools import FunctionTool
from tests.helpers import ValueArguments, value_tool


def _parent_runtime(tools: list[object]) -> AgentRuntime:
    return AgentRuntime(
        provider=FakeProvider([]),
        tools=tools,  # type: ignore[arg-type]
        policy=LoopPolicy(max_steps=4),
    )


def test_read_only_profiles_receive_only_inspection_tools() -> None:
    registry = AgentProfileRegistry(Path("/tmp"))
    tools = [
        value_tool(name="read_file"),
        value_tool(name="grep"),
        value_tool(name="write_file"),
        value_tool(name="run_terminal"),
        value_tool(name="git_diff"),
    ]

    selected = child_tools(registry.get("explore"), tools)  # type: ignore[arg-type]

    assert {tool.metadata.name for tool in selected} == {"read_file", "grep", "git_diff"}


def test_inherited_profile_keeps_parent_tool_set() -> None:
    registry = AgentProfileRegistry(Path("/tmp"))
    tools = [value_tool(name="write_file"), value_tool(name="read_file")]

    selected = child_tools(registry.get("coder"), tools)  # type: ignore[arg-type]

    assert [tool.metadata.name for tool in selected] == ["write_file", "read_file"]


@pytest.mark.asyncio
async def test_coordinator_returns_stable_order_and_isolated_stores() -> None:
    registry = AgentProfileRegistry(Path("/tmp"))
    parent = _parent_runtime([])
    stores: list[InMemoryEventStore] = []

    def make_store() -> InMemoryEventStore:
        store = InMemoryEventStore()
        stores.append(store)
        return store

    def make_provider() -> FakeProvider:
        return FakeProvider([ModelResponse(content="child result")])

    coordinator = DelegationCoordinator(
        parent,
        provider_factory=make_provider,
        event_store_factory=make_store,
    )
    request = registry.parse_prompt("@reviewer first | @explore second")
    assert request is not None

    results = await coordinator.run("parent-run", request.parts)

    assert [result.agent_name for result in results] == ["reviewer", "explore"]
    assert [result.child_run_id for result in results] == [
        "parent-run.reviewer.1",
        "parent-run.explore.2",
    ]
    assert all(result.status == "completed" for result in results)
    assert len(stores) == 2
    for store in stores:
        assert len(await store.list_runs()) == 1


@pytest.mark.asyncio
async def test_coordinator_bounds_parallelism_and_keeps_sibling_failures() -> None:
    registry = AgentProfileRegistry(Path("/tmp"))
    parent = _parent_runtime([])
    active = 0
    peak = 0

    class Provider:
        async def generate(self, request: ModelRequest) -> ModelResponse:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                prompt = str(request.messages[-1]["content"])
                if "fail" in prompt:
                    raise RuntimeError("child failed")
                return ModelResponse(content=f"done: {prompt}")
            finally:
                active -= 1

    coordinator = DelegationCoordinator(
        parent,
        provider_factory=Provider,
        max_concurrency=2,
    )
    request = registry.parse_prompt("@explore one | @explore two | @explore fail | @explore four")
    assert request is not None

    results = await coordinator.run("parent-run", request.parts)

    assert peak == 2
    assert [result.status for result in results] == [
        "completed",
        "completed",
        "stopped",
        "completed",
    ]
    assert results[2].error


@pytest.mark.asyncio
async def test_coder_child_inherits_parent_approval_callback() -> None:
    registry = AgentProfileRegistry(Path("/tmp"))
    approved: list[str] = []

    async def record_tool(arguments: ValueArguments) -> dict[str, JsonValue]:
        return {"value": arguments.value}

    tool = FunctionTool(
        name="write_file",
        description="Write a value.",
        arguments_model=ValueArguments,
        function=record_tool,
    )
    parent = AgentRuntime(
        provider=FakeProvider([]),
        tools=[tool],
        approval_callback=lambda call: approved.append(call.name) or True,
    )

    def make_provider() -> FakeProvider:
        return FakeProvider(
            [
                ModelResponse(
                    tool_call=ToolCall(
                        name="write_file",
                        arguments={"value": 1},
                    )
                ),
                ModelResponse(content="written"),
            ]
        )

    coordinator = DelegationCoordinator(parent, provider_factory=make_provider)
    request = registry.parse_prompt("@coder make the change")
    assert request is not None

    result = await coordinator.run("parent-run", request.parts)

    assert result[0].status == "completed"
    assert approved == ["write_file"]


@pytest.mark.asyncio
async def test_child_runtime_receives_derived_security_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = AgentProfileRegistry(tmp_path)
    parent_policy = resolve_security_config(
        explicit={
            "sandbox_required": False,
            "sandbox_network": True,
            "plugin_editable": True,
            "plugin_activation": True,
        },
        user_root=tmp_path / "config",
    )
    parent = AgentRuntime(
        provider=FakeProvider([]),
        policy=LoopPolicy(max_steps=2),
        security_config=parent_policy,
    )
    captured: list[object] = []
    original_init = delegation_module.AgentRuntime.__init__

    def capture_init(self: AgentRuntime, *args: object, **kwargs: object) -> None:
        captured.append(kwargs.get("security_config"))
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(delegation_module.AgentRuntime, "__init__", capture_init)

    coordinator = DelegationCoordinator(
        parent,
        provider_factory=lambda: FakeProvider([ModelResponse(content="read")]),
    )
    request = registry.parse_prompt("@explore inspect")
    assert request is not None

    result = await coordinator.run("parent-run", request.parts)

    assert result[0].status == "completed"
    child_policy = captured[0]
    assert child_policy is not None
    assert child_policy.sandbox_required.value is True  # type: ignore[union-attr]
    assert child_policy.sandbox_network.value is False  # type: ignore[union-attr]
    assert child_policy.plugin_activation.value is False  # type: ignore[union-attr]
    assert AgentCapability.READ_ONLY.value == request.parts[0].agent.capability


@pytest.mark.asyncio
async def test_coordinator_pipeline_executes_sequentially_and_passes_output() -> None:
    registry = AgentProfileRegistry(Path("/tmp"))
    parent = _parent_runtime([])

    received_prompts: list[str] = []

    class _TrackingProvider(FakeProvider):
        def __init__(self, responses: list[ModelResponse]) -> None:
            super().__init__(responses)

        async def generate(self, request: ModelRequest) -> ModelResponse:
            received_prompts.append(str(request.messages[0]["content"]))
            return await super().generate(request)

    responses_list = [
        [ModelResponse(content="plan output")],
        [ModelResponse(content="code output")],
    ]
    call_idx = 0

    def make_provider() -> _TrackingProvider:
        nonlocal call_idx
        p = _TrackingProvider(responses_list[call_idx])
        call_idx += 1
        return p

    coordinator = DelegationCoordinator(parent, provider_factory=make_provider)
    request = registry.parse_prompt("@explore plan task -> @coder implement task")
    assert request is not None
    assert request.is_pipeline is True

    results = await coordinator.pipeline("pipeline-run", request.parts)

    assert len(results) == 2
    assert results[0].agent_name == "explore"
    assert results[0].output == "plan output"
    assert results[1].agent_name == "coder"
    assert results[1].output == "code output"

    # Verify stage 2 received output from stage 1
    assert "plan output" in received_prompts[1]
    assert "--- Output from prior stage (@explore) ---" in received_prompts[1]
