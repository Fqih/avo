"""State-machine and append-only event invariants."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from avo.events import TERMINAL_EVENT_TYPES, AgentEvent, EventType
from avo.exceptions import (
    EventInvariantError,
    InvalidStateTransitionError,
    StorageError,
)
from avo.models import ModelResponse, RunRecord
from avo.state import RunState, StopReason, validate_terminal_outcome, validate_transition
from avo.storage import InMemoryEventStore
from tests.helpers import seed_run


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.CREATED, RunState.MODEL_PENDING),
        (RunState.MODEL_PENDING, RunState.DECISION_RECEIVED),
        (RunState.DECISION_RECEIVED, RunState.TOOL_PENDING),
        (RunState.TOOL_PENDING, RunState.APPROVAL_PENDING),
        (RunState.APPROVAL_PENDING, RunState.TOOL_EXECUTING),
        (RunState.TOOL_EXECUTING, RunState.OBSERVATION_RECORDED),
        (RunState.OBSERVATION_RECORDED, RunState.MODEL_PENDING),
        (RunState.DECISION_RECEIVED, RunState.COMPLETED),
    ],
)
def test_valid_state_transitions(current: RunState, target: RunState) -> None:
    validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.CREATED, RunState.COMPLETED),
        (RunState.MODEL_PENDING, RunState.TOOL_EXECUTING),
        (RunState.TOOL_PENDING, RunState.COMPLETED),
        (RunState.COMPLETED, RunState.MODEL_PENDING),
        (RunState.FAILED, RunState.CREATED),
        (RunState.STOPPED, RunState.CANCELLED),
        (RunState.CANCELLED, RunState.PAUSED),
    ],
)
def test_invalid_state_transitions(current: RunState, target: RunState) -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(current, target)


def test_terminal_state_and_reason_must_agree() -> None:
    validate_terminal_outcome(RunState.COMPLETED, StopReason.COMPLETED)

    with pytest.raises(InvalidStateTransitionError):
        validate_terminal_outcome(RunState.COMPLETED, StopReason.MAX_STEPS)
    with pytest.raises(InvalidStateTransitionError):
        validate_terminal_outcome(RunState.MODEL_PENDING, StopReason.PROVIDER_ERROR)


def test_run_record_rejects_terminal_state_without_reason() -> None:
    with pytest.raises(ValidationError):
        RunRecord(task="task", state=RunState.FAILED)


def test_model_response_requires_exactly_one_decision() -> None:
    with pytest.raises(ValidationError):
        ModelResponse()
    with pytest.raises(ValidationError):
        ModelResponse(content="done", tool_call={"name": "x", "arguments": {}})


def test_event_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        AgentEvent(
            run_id="run",
            event_type=EventType.RUN_CREATED,
            created_at=datetime(2026, 1, 1),
        )


@pytest.mark.asyncio
async def test_store_allocates_strict_gap_free_sequences() -> None:
    store = InMemoryEventStore()
    await seed_run(store)
    for step in range(1, 5):
        await store.append_event(
            AgentEvent(
                run_id="run-1",
                event_type=EventType.MODEL_REQUESTED,
                payload={"step": step},
            )
        )

    events = await store.get_events("run-1")
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_run_cannot_have_two_creation_events() -> None:
    store = InMemoryEventStore()
    await seed_run(store)

    with pytest.raises(EventInvariantError, match="exactly one"):
        await store.append_event(AgentEvent(run_id="run-1", event_type=EventType.RUN_CREATED))


@pytest.mark.asyncio
async def test_tool_cannot_finish_before_it_starts_or_finish_twice() -> None:
    store = InMemoryEventStore()
    await seed_run(store)
    completed = AgentEvent(
        run_id="run-1",
        event_type=EventType.TOOL_COMPLETED,
        payload={"tool_call_id": "call-1"},
    )
    with pytest.raises(EventInvariantError, match="before TOOL_STARTED"):
        await store.append_event(completed)

    await store.append_event(
        AgentEvent(
            run_id="run-1",
            event_type=EventType.TOOL_STARTED,
            payload={"tool_call_id": "call-1"},
        )
    )
    await store.append_event(completed)
    with pytest.raises(EventInvariantError, match="already has a result"):
        await store.append_event(completed.model_copy(update={"event_id": "another"}))


@pytest.mark.asyncio
async def test_invalid_state_event_is_rejected() -> None:
    store = InMemoryEventStore()
    run = await seed_run(store)
    invalid = run.model_copy(update={"state": RunState.TOOL_EXECUTING})

    with pytest.raises((EventInvariantError, StorageError)):
        await store.append_event_and_update_run(
            AgentEvent(
                run_id=run.run_id,
                event_type=EventType.STATE_CHANGED,
                payload={
                    "from_state": RunState.CREATED.value,
                    "to_state": RunState.TOOL_EXECUTING.value,
                },
            ),
            invalid,
        )


@pytest.mark.asyncio
async def test_run_cannot_complete_without_model_decision_event() -> None:
    store = InMemoryEventStore()
    run = await seed_run(store)
    model_pending = RunRecord.model_validate({**run.model_dump(), "state": RunState.MODEL_PENDING})
    await store.append_event_and_update_run(
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.STATE_CHANGED,
            payload={"from_state": "created", "to_state": "model_pending"},
        ),
        model_pending,
    )
    decision = RunRecord.model_validate(
        {**model_pending.model_dump(), "state": RunState.DECISION_RECEIVED}
    )
    await store.append_event_and_update_run(
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.STATE_CHANGED,
            payload={"from_state": "model_pending", "to_state": "decision_received"},
        ),
        decision,
    )
    completed = RunRecord.model_validate(
        {
            **decision.model_dump(),
            "state": RunState.COMPLETED,
            "stop_reason": StopReason.COMPLETED,
        }
    )

    with pytest.raises(EventInvariantError, match="model decision"):
        await store.finalize_run(
            completed,
            AgentEvent(
                run_id=run.run_id,
                event_type=EventType.STATE_CHANGED,
                payload={"from_state": "decision_received", "to_state": "completed"},
            ),
            AgentEvent(
                run_id=run.run_id,
                event_type=EventType.RUN_COMPLETED,
                payload={"state": "completed", "stop_reason": "completed"},
            ),
        )


@pytest.mark.asyncio
async def test_no_event_can_follow_terminal_event() -> None:
    store = InMemoryEventStore()
    run = await seed_run(store)
    failed = RunRecord.model_validate(
        {
            **run.model_dump(),
            "state": RunState.FAILED,
            "stop_reason": StopReason.INTERNAL_ERROR,
        }
    )
    await store.finalize_run(
        failed,
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.STATE_CHANGED,
            payload={"from_state": "created", "to_state": "failed"},
        ),
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.RUN_FAILED,
            payload={"state": "failed", "stop_reason": "internal_error"},
        ),
    )

    with pytest.raises(EventInvariantError, match="terminal"):
        await store.append_event(
            AgentEvent(run_id=run.run_id, event_type=EventType.MODEL_REQUESTED)
        )
    events = await store.get_events(run.run_id)
    assert sum(event.event_type in TERMINAL_EVENT_TYPES for event in events) == 1


@pytest.mark.asyncio
async def test_route_failover_event_can_be_appended_during_active_run() -> None:
    store = InMemoryEventStore()
    run = await seed_run(store)
    event = AgentEvent(
        run_id=run.run_id,
        event_type=EventType.ROUTE_FAILOVER,
        payload={
            "combo": "coder",
            "from_tier": "subscription",
            "from_provider": "claude",
            "from_model": "claude-sonnet-5",
            "to_tier": "cheap",
            "to_provider": "openrouter",
            "to_model": "meta-llama/llama-3.3-70b-instruct",
            "reason": "rate_limited_429",
        },
    )
    appended = await store.append_event(event)
    assert appended.event_type is EventType.ROUTE_FAILOVER
    events = await store.get_events(run.run_id)
    assert any(e.event_type is EventType.ROUTE_FAILOVER for e in events)
