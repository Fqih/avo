"""End-to-end state-machine and reliability policy behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from avo import (
    AgentRuntime,
    EventType,
    LoopPolicy,
    ModelResponse,
    RunState,
    StopReason,
    TokenUsage,
    ToolCall,
)
from avo.events import TERMINAL_EVENT_TYPES
from avo.models import ModelRequest
from avo.providers import FakeProvider
from avo.storage import InMemoryEventStore
from tests.helpers import value_tool


@pytest.mark.asyncio
async def test_successful_final_response_has_one_explicit_terminal_event() -> None:
    store = InMemoryEventStore()
    runtime = AgentRuntime(
        provider=FakeProvider(
            [
                ModelResponse(
                    content="finished",
                    usage=TokenUsage(input_tokens=4, output_tokens=2),
                )
            ]
        ),
        event_store=store,
    )

    result = await runtime.run("finish directly")
    events = await store.get_events(result.run_id)

    assert result.status is RunState.COMPLETED
    assert result.stop_reason is StopReason.COMPLETED
    assert result.output == "finished"
    assert result.token_usage.total_tokens == 6
    assert sum(event.event_type in TERMINAL_EVENT_TYPES for event in events) == 1
    assert events[-1].event_type is EventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_run_places_optional_system_prompt_before_user_message() -> None:
    provider = FakeProvider([ModelResponse(content="hello")])
    runtime = AgentRuntime(provider=provider)

    result = await runtime.run("say hello", system_prompt="You are Avo.")

    assert result.status is RunState.COMPLETED
    assert provider.requests[0].messages == [
        {"role": "system", "content": "You are Avo."},
        {"role": "user", "content": "say hello"},
    ]


@pytest.mark.asyncio
async def test_successful_tool_flow_appends_observation_to_model_context() -> None:
    provider = FakeProvider(
        [
            ModelResponse(
                tool_call=ToolCall(
                    tool_call_id="call-1",
                    name="value",
                    arguments={"value": 7},
                )
            ),
            ModelResponse(content="tool observed"),
        ]
    )
    store = InMemoryEventStore()
    runtime = AgentRuntime(
        provider=provider,
        tools=[value_tool()],
        event_store=store,
    )

    result = await runtime.run("use a tool")
    events = await store.get_events(result.run_id)

    assert result.status is RunState.COMPLETED
    assert len(provider.requests) == 2
    tool_message = provider.requests[1].messages[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call-1"
    assert EventType.TOOL_APPROVAL_REQUESTED in {event.event_type for event in events}
    assert EventType.TOOL_APPROVED in {event.event_type for event in events}
    assert EventType.TOOL_COMPLETED in {event.event_type for event in events}


@pytest.mark.asyncio
async def test_provider_error_can_recover_before_error_limit() -> None:
    provider = FakeProvider([RuntimeError("temporary"), ModelResponse(content="recovered")])
    runtime = AgentRuntime(
        provider=provider,
        policy=LoopPolicy(consecutive_error_limit=2),
    )

    result = await runtime.run("retry provider")

    assert result.status is RunState.COMPLETED
    assert result.output == "recovered"
    assert result.steps == 2


@pytest.mark.asyncio
async def test_consecutive_provider_errors_trigger_policy() -> None:
    store = InMemoryEventStore()
    runtime = AgentRuntime(
        provider=FakeProvider([RuntimeError("one"), RuntimeError("two")]),
        policy=LoopPolicy(consecutive_error_limit=2),
        event_store=store,
    )

    result = await runtime.run("provider keeps failing")
    events = await store.get_events(result.run_id)

    assert result.status is RunState.STOPPED
    assert result.stop_reason is StopReason.CONSECUTIVE_ERRORS
    assert [event.event_type for event in events].count(EventType.MODEL_FAILED) == 2
    assert any(
        event.event_type is EventType.POLICY_TRIGGERED
        and event.payload["policy"] == "consecutive_errors"
        for event in events
    )


@pytest.mark.asyncio
async def test_exhausted_fake_provider_is_explicit_provider_failure() -> None:
    runtime = AgentRuntime(provider=FakeProvider([]))

    result = await runtime.run("no script")

    assert result.status is RunState.FAILED
    assert result.stop_reason is StopReason.PROVIDER_ERROR
    assert "exhausted" in (result.error or "")


@pytest.mark.asyncio
async def test_invalid_model_response_is_explicit_failure() -> None:
    store = InMemoryEventStore()
    runtime = AgentRuntime(
        provider=FakeProvider([{"content": "x", "tool_call": {"name": "y"}}]),
        event_store=store,
    )

    result = await runtime.run("malformed response")

    assert result.status is RunState.FAILED
    assert result.stop_reason is StopReason.INVALID_MODEL_RESPONSE
    assert EventType.MODEL_FAILED in {
        event.event_type for event in await store.get_events(result.run_id)
    }


@pytest.mark.asyncio
async def test_tool_failure_is_observed_and_model_can_recover() -> None:
    provider = FakeProvider(
        [
            ModelResponse(
                tool_call=ToolCall(
                    tool_call_id="bad-call",
                    name="value",
                    arguments={"value": "not-an-integer"},
                )
            ),
            ModelResponse(content="handled tool error"),
        ]
    )
    store = InMemoryEventStore()
    runtime = AgentRuntime(
        provider=provider,
        tools=[value_tool()],
        event_store=store,
    )

    result = await runtime.run("malformed arguments")

    assert result.status is RunState.COMPLETED
    assert provider.requests[1].messages[-1]["content"]["success"] is False
    assert EventType.TOOL_FAILED in {
        event.event_type for event in await store.get_events(result.run_id)
    }


@pytest.mark.asyncio
async def test_tool_error_limit_stops_run() -> None:
    runtime = AgentRuntime(
        provider=FakeProvider(
            [
                ModelResponse(
                    tool_call=ToolCall(
                        tool_call_id="call-1",
                        name="value",
                        arguments={"value": 1},
                    )
                )
            ]
        ),
        tools=[value_tool(raises=RuntimeError("broken"))],
        policy=LoopPolicy(consecutive_error_limit=1),
    )

    result = await runtime.run("broken tool")

    assert result.status is RunState.STOPPED
    assert result.stop_reason is StopReason.CONSECUTIVE_ERRORS


@pytest.mark.asyncio
async def test_max_steps_stops_before_an_extra_provider_call() -> None:
    provider = FakeProvider(
        [
            ModelResponse(
                tool_call=ToolCall(
                    tool_call_id="call-1",
                    name="value",
                    arguments={"value": 1},
                )
            ),
            ModelResponse(content="must not be reached"),
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        tools=[value_tool()],
        policy=LoopPolicy(max_steps=1),
    )

    result = await runtime.run("one step only")

    assert result.stop_reason is StopReason.MAX_STEPS
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_repeated_action_stops_before_repeating_side_effect_at_limit() -> None:
    counter = [0]
    responses = [
        ModelResponse(
            tool_call=ToolCall(
                tool_call_id=f"call-{index}",
                name="value",
                arguments={"value": 1},
            )
        )
        for index in range(1, 4)
    ]
    runtime = AgentRuntime(
        provider=FakeProvider(responses),
        tools=[value_tool(counter=counter)],
        policy=LoopPolicy(repeated_action_limit=3, no_progress_window=10),
    )

    result = await runtime.run("repeat action")

    assert result.stop_reason is StopReason.REPEATED_ACTION
    assert counter[0] == 2


@pytest.mark.asyncio
async def test_identical_observations_trigger_no_progress() -> None:
    responses: list[ModelResponse] = []
    for index in range(2):
        responses.append(
            ModelResponse(
                tool_call=ToolCall(
                    tool_call_id=f"call-{index}",
                    name="value",
                    arguments={"value": index},
                )
            )
        )
    runtime = AgentRuntime(
        provider=FakeProvider(responses),
        tools=[value_tool(output={"unchanged": True})],
        policy=LoopPolicy(repeated_action_limit=10, no_progress_window=2),
    )

    result = await runtime.run("no observation progress")

    assert result.stop_reason is StopReason.NO_PROGRESS


@pytest.mark.asyncio
async def test_token_budget_stops_before_requested_tool_executes() -> None:
    counter = [0]
    runtime = AgentRuntime(
        provider=FakeProvider(
            [
                ModelResponse(
                    tool_call=ToolCall(name="value", arguments={"value": 1}),
                    usage=TokenUsage(input_tokens=6, output_tokens=1),
                )
            ]
        ),
        tools=[value_tool(counter=counter)],
        policy=LoopPolicy(max_total_tokens=5),
    )

    result = await runtime.run("small token budget")

    assert result.stop_reason is StopReason.TOKEN_BUDGET_EXCEEDED
    assert counter[0] == 0


@pytest.mark.asyncio
async def test_missing_token_usage_is_visible_in_result_and_trace() -> None:
    class ProviderWithoutUsage:
        async def generate(self, request: ModelRequest) -> ModelResponse:
            del request
            return ModelResponse(content="done")

    runtime = AgentRuntime(
        provider=ProviderWithoutUsage(),
        policy=LoopPolicy(max_total_tokens=1),
    )

    result = await runtime.run("usage unavailable")
    trace = await runtime.inspect(result.run_id)

    assert result.status is RunState.COMPLETED
    assert result.token_accounting_available is False
    assert trace.token_accounting_available is False
    assert "unavailable" in trace.to_text()


@pytest.mark.asyncio
async def test_runtime_limit_is_checked_before_tool_boundary() -> None:
    class MutableClock:
        def __init__(self) -> None:
            self.current = datetime(2026, 1, 1, tzinfo=UTC)

        def __call__(self) -> datetime:
            return self.current

    clock = MutableClock()

    class SlowDecisionProvider:
        async def generate(self, request: ModelRequest) -> ModelResponse:
            del request
            clock.current += timedelta(seconds=3)
            return ModelResponse(
                tool_call=ToolCall(name="value", arguments={"value": 1}),
                usage=TokenUsage(),
            )

    counter = [0]
    runtime = AgentRuntime(
        provider=SlowDecisionProvider(),
        tools=[value_tool(counter=counter)],
        policy=LoopPolicy(max_runtime_seconds=2),
        clock=clock,
    )

    result = await runtime.run("runtime expires between operations")

    assert result.stop_reason is StopReason.MAX_RUNTIME
    assert counter[0] == 0


@pytest.mark.asyncio
async def test_denied_approval_stops_with_policy_denied() -> None:
    runtime = AgentRuntime(
        provider=FakeProvider(
            [ModelResponse(tool_call=ToolCall(name="value", arguments={"value": 1}))]
        ),
        tools=[value_tool()],
        approval_callback=lambda call: False,
    )

    result = await runtime.run("deny tool")

    assert result.status is RunState.STOPPED
    assert result.stop_reason is StopReason.POLICY_DENIED


@pytest.mark.asyncio
async def test_trace_contains_transitions_durations_and_terminal_reason() -> None:
    runtime = AgentRuntime(provider=FakeProvider([ModelResponse(content="done")]))
    result = await runtime.run("trace me")
    trace = await runtime.inspect(result.run_id)
    dumped = trace.model_dump(mode="json")

    assert trace.entries == sorted(trace.entries, key=lambda entry: entry.sequence)
    assert any(entry.from_state is RunState.CREATED for entry in trace.entries)
    assert any(entry.duration_ms is not None for entry in trace.entries)
    assert dumped["stop_reason"] == "completed"
    assert "run_completed" in trace.to_text()


@pytest.mark.asyncio
async def test_caller_cancellation_is_persisted_before_propagation() -> None:
    started = asyncio.Event()

    class BlockingProvider:
        async def generate(self, request: ModelRequest) -> ModelResponse:
            del request
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    store = InMemoryEventStore()
    runtime = AgentRuntime(provider=BlockingProvider(), event_store=store)
    task = asyncio.create_task(runtime.run("cancel me", run_id="cancelled-run"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = await store.get_run("cancelled-run")
    assert run.state is RunState.CANCELLED
    assert run.stop_reason is StopReason.USER_CANCELLED
    assert (await store.get_events(run.run_id))[-1].event_type is EventType.RUN_CANCELLED


@pytest.mark.asyncio
async def test_runtime_aclose_calls_provider_aclose() -> None:
    """AgentRuntime.aclose() must forward to provider.aclose() when available."""
    closed: list[bool] = []

    class _CloseableProvider(FakeProvider):
        async def aclose(self) -> None:
            closed.append(True)

    rt = AgentRuntime(provider=_CloseableProvider([ModelResponse(content="hi")]))
    await rt.aclose()
    assert closed == [True]

    # Second call is a no-op (provider already closed; FakeProvider.aclose is idempotent)
    await rt.aclose()


@pytest.mark.asyncio
async def test_runtime_async_context_manager_closes_provider() -> None:
    """``async with AgentRuntime(...)`` must call aclose() on exit."""
    closed: list[bool] = []

    class _CloseableProvider(FakeProvider):
        async def aclose(self) -> None:
            closed.append(True)

    async with AgentRuntime(provider=_CloseableProvider([ModelResponse(content="hi")])) as rt:
        result = await rt.run("hi")
        assert result.status is RunState.COMPLETED

    assert closed == [True]


@pytest.mark.asyncio
async def test_runtime_aclose_is_noop_for_provider_without_aclose() -> None:
    """AgentRuntime.aclose() must not raise when provider has no aclose()."""
    rt = AgentRuntime(provider=FakeProvider([ModelResponse(content="hi")]))
    # FakeProvider has no aclose; this must not raise
    await rt.aclose()

