"""Append-only event types and event-log invariant validation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, JsonValue, field_validator

from avo.exceptions import EventInvariantError, InvalidStateTransitionError
from avo.models import AvoModel, new_id, utc_now
from avo.state import RunState, StopReason, validate_terminal_outcome, validate_transition


class EventType(StrEnum):
    """Event kinds persisted by Avo."""

    RUN_CREATED = "run_created"
    STATE_CHANGED = "state_changed"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONDED = "model_responded"
    MODEL_FAILED = "model_failed"
    ROUTE_FAILOVER = "route_failover"
    TOOL_REQUESTED = "tool_requested"
    TOOL_APPROVAL_REQUESTED = "tool_approval_requested"
    TOOL_APPROVED = "tool_approved"
    TOOL_DENIED = "tool_denied"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    POLICY_TRIGGERED = "policy_triggered"
    CHECKPOINT_CREATED = "checkpoint_created"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_STOPPED = "run_stopped"
    RUN_CANCELLED = "run_cancelled"


TERMINAL_EVENT_TYPES = frozenset(
    {
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_STOPPED,
        EventType.RUN_CANCELLED,
    }
)


class AgentEvent(AvoModel):
    """One immutable, sequenced fact in a run's execution history."""

    event_id: str = Field(default_factory=new_id, min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(default=0, ge=0)
    event_type: EventType
    created_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    parent_event_id: str | None = None

    @field_validator("created_at", mode="after")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        """Normalize event timestamps to UTC and reject naive values."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)


def validate_event_append(existing: list[AgentEvent], candidate: AgentEvent) -> None:
    """Validate cross-event append-only invariants before persistence."""

    if not existing:
        if candidate.event_type is not EventType.RUN_CREATED:
            raise EventInvariantError("The first event for a run must be RUN_CREATED.")
        return

    if candidate.event_type is EventType.RUN_CREATED:
        raise EventInvariantError("A run can contain exactly one RUN_CREATED event.")
    if any(event.event_type in TERMINAL_EVENT_TYPES for event in existing):
        raise EventInvariantError("Cannot append an event after a terminal event.")

    if candidate.event_type is EventType.STATE_CHANGED:
        try:
            current = RunState(str(candidate.payload["from_state"]))
            target = RunState(str(candidate.payload["to_state"]))
            validate_transition(current, target)
        except (KeyError, ValueError, InvalidStateTransitionError) as exc:
            raise EventInvariantError(
                "STATE_CHANGED requires valid from_state and to_state values."
            ) from exc

    if candidate.event_type in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}:
        call_id = candidate.payload.get("tool_call_id")
        if not isinstance(call_id, str):
            raise EventInvariantError(
                f"{candidate.event_type.value} requires a string tool_call_id."
            )
        started = any(
            event.event_type is EventType.TOOL_STARTED
            and event.payload.get("tool_call_id") == call_id
            for event in existing
        )
        finished = any(
            event.event_type in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}
            and event.payload.get("tool_call_id") == call_id
            for event in existing
        )
        if not started:
            raise EventInvariantError(
                f"Tool call {call_id!r} cannot finish before TOOL_STARTED is persisted."
            )
        if finished:
            raise EventInvariantError(f"Tool call {call_id!r} already has a result event.")

    if candidate.event_type in TERMINAL_EVENT_TYPES:
        try:
            state = RunState(str(candidate.payload["state"]))
            reason = StopReason(str(candidate.payload["stop_reason"]))
            validate_terminal_outcome(state, reason)
        except (KeyError, ValueError, InvalidStateTransitionError) as exc:
            raise EventInvariantError(
                "A terminal event requires compatible state and stop_reason values."
            ) from exc
        if candidate.event_type is EventType.RUN_COMPLETED and not any(
            event.event_type is EventType.MODEL_RESPONDED for event in existing
        ):
            raise EventInvariantError("A run cannot complete before a model decision is recorded.")
