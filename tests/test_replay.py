"""Deterministic replay ledger and provider tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from avo import AgentRuntime, EventType, ModelResponse, RunState, StopReason
from avo.events import AgentEvent
from avo.models import ModelRequest, RunRecord, ToolCall
from avo.providers import FakeProvider
from avo.replay import (
    DeterministicReplayProvider,
    ReplayError,
    ReplayTranscript,
    replay_run,
)
from avo.storage import InMemoryEventStore


def _request_from_event(event: AgentEvent) -> ModelRequest:
    return ModelRequest.model_validate(event.payload["request"])


@pytest.mark.asyncio
async def test_transcript_replays_recorded_model_decision() -> None:
    store = InMemoryEventStore()
    runtime = AgentRuntime(
        provider=FakeProvider([ModelResponse(content="done")]), event_store=store
    )
    result = await runtime.run("finish")
    run = await store.get_run(result.run_id)
    events = await store.get_events(result.run_id)

    transcript = ReplayTranscript.from_events(run, events)
    provider = DeterministicReplayProvider(transcript)
    request = _request_from_event(
        next(e for e in events if e.event_type is EventType.MODEL_REQUESTED)
    )

    response = await provider.generate(request)

    assert response.content == "done"
    assert provider.remaining == 0
    assert transcript.fingerprint
    report = await replay_run(store, result.run_id)
    assert report.verified is True
    assert report.matched_events == len(events)


@pytest.mark.asyncio
async def test_replay_provider_rejects_request_divergence() -> None:
    store = InMemoryEventStore()
    runtime = AgentRuntime(
        provider=FakeProvider([ModelResponse(content="done")]), event_store=store
    )
    result = await runtime.run("finish")
    events = await store.get_events(result.run_id)
    transcript = ReplayTranscript.from_events(await store.get_run(result.run_id), events)
    provider = DeterministicReplayProvider(transcript)
    request = _request_from_event(
        next(e for e in events if e.event_type is EventType.MODEL_REQUESTED)
    )

    with pytest.raises(ReplayError, match="divergence"):
        changed = request.model_copy(update={"messages": [{"role": "user", "content": "changed"}]})
        await provider.generate(changed)


@pytest.mark.asyncio
async def test_replay_does_not_invoke_tools_again() -> None:
    calls = 0

    async def counted(arguments: object) -> int:
        nonlocal calls
        calls += 1
        return 7

    # The normal runtime executes the tool once and records its durable result.
    from pydantic import BaseModel

    class Args(BaseModel):
        value: int

    from avo.tools import FunctionTool

    tool = FunctionTool(name="counted", description="count", arguments_model=Args, function=counted)
    provider = FakeProvider(
        [
            ModelResponse(
                tool_call=ToolCall(tool_call_id="call-1", name="counted", arguments={"value": 1})
            ),
            ModelResponse(content="finished"),
        ]
    )
    store = InMemoryEventStore()
    runtime = AgentRuntime(provider=provider, tools=[tool], event_store=store)
    result = await runtime.run("use counted")
    assert calls == 1

    report = await replay_run(store, result.run_id)
    assert report.verified is True
    assert calls == 1


def test_transcript_rejects_unresolved_tool_start() -> None:
    now = datetime.now(UTC)
    run = RunRecord(
        run_id="run-1",
        task="task",
        state=RunState.COMPLETED,
        stop_reason=StopReason.COMPLETED,
    )
    events = [
        AgentEvent(run_id="run-1", sequence=1, event_type=EventType.RUN_CREATED, created_at=now),
        AgentEvent(
            run_id="run-1",
            sequence=2,
            event_type=EventType.MODEL_REQUESTED,
            created_at=now,
            payload={
                "step": 1,
                "request": ModelRequest(run_id="run-1", step=1, messages=[]).model_dump(
                    mode="json"
                ),
            },
        ),
        AgentEvent(
            run_id="run-1",
            sequence=3,
            event_type=EventType.MODEL_RESPONDED,
            created_at=now,
            payload={"step": 1, "response": ModelResponse(content="done").model_dump(mode="json")},
        ),
        AgentEvent(
            run_id="run-1",
            sequence=4,
            event_type=EventType.TOOL_STARTED,
            created_at=now,
            payload={"tool_call_id": "call-1", "name": "value", "arguments": {}},
        ),
        AgentEvent(
            run_id="run-1",
            sequence=5,
            event_type=EventType.RUN_COMPLETED,
            created_at=now,
            payload={"state": "completed", "stop_reason": "completed"},
        ),
    ]

    with pytest.raises(ReplayError, match="without a durable result"):
        ReplayTranscript.from_events(run, events)
