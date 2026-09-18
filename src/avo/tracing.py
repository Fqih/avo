"""Chronological trace inspection and plain-text rendering."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, JsonValue

from avo.events import AgentEvent, EventType
from avo.models import AvoModel, RunRecord, TokenUsage
from avo.state import RunState, StopReason
from avo.storage.base import EventStore


class TraceEntry(AvoModel):
    """A normalized, chronological view of one persisted event."""

    sequence: int = Field(ge=1)
    created_at: datetime
    event_type: EventType
    summary: str
    from_state: RunState | None = None
    to_state: RunState | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    error: str | None = None
    policy: str | None = None
    payload: dict[str, JsonValue]


class RunTrace(AvoModel):
    """Inspectable trace summary with text and structured representations."""

    run_id: str
    entries: list[TraceEntry]
    final_state: RunState
    stop_reason: StopReason | None
    steps: int = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    token_usage: TokenUsage
    token_accounting_available: bool

    def to_text(self) -> str:
        """Render a compact human-readable trace."""

        reason = self.stop_reason.value if self.stop_reason is not None else "-"
        accounting = "available" if self.token_accounting_available else "unavailable"
        lines = [
            f"Run: {self.run_id}",
            f"State: {self.final_state.value}",
            f"Stop reason: {reason}",
            f"Steps: {self.steps}",
            (
                "Tokens: "
                f"{self.token_usage.total_tokens} "
                f"(input={self.token_usage.input_tokens}, "
                f"output={self.token_usage.output_tokens}, {accounting})"
            ),
        ]
        if self.duration_seconds is not None:
            lines.append(f"Duration: {self.duration_seconds:.3f}s")
        lines.extend(["", "Events:"])
        for entry in self.entries:
            suffixes: list[str] = []
            if entry.duration_ms is not None:
                suffixes.append(f"{entry.duration_ms:.2f}ms")
            if entry.policy is not None:
                suffixes.append(f"policy={entry.policy}")
            if entry.error is not None:
                suffixes.append(f"error={entry.error}")
            suffix = f" ({'; '.join(suffixes)})" if suffixes else ""
            lines.append(
                f"{entry.sequence:>4}  {entry.event_type.value:<27} {entry.summary}{suffix}"
            )
        return "\n".join(lines)


class TraceInspector:
    """Build RunTrace values from an EventStore."""

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store

    async def inspect(self, run_id: str) -> RunTrace:
        """Load metadata and chronological events for one run."""

        run = await self._event_store.get_run(run_id)
        events = await self._event_store.get_events(run_id)
        return self.from_events(run, events)

    @staticmethod
    def from_events(run: RunRecord, events: list[AgentEvent]) -> RunTrace:
        """Normalize already-loaded events into a trace."""

        return RunTrace(
            run_id=run.run_id,
            entries=[TraceInspector._entry(event) for event in events],
            final_state=run.state,
            stop_reason=run.stop_reason,
            steps=run.steps,
            duration_seconds=run.duration_seconds,
            token_usage=run.token_usage,
            token_accounting_available=run.token_accounting_available,
        )

    @staticmethod
    def _entry(event: AgentEvent) -> TraceEntry:
        payload = event.payload
        from_state: RunState | None = None
        to_state: RunState | None = None
        if event.event_type is EventType.STATE_CHANGED:
            from_state = RunState(str(payload["from_state"]))
            to_state = RunState(str(payload["to_state"]))

        duration_value = payload.get("duration_ms")
        duration = float(duration_value) if isinstance(duration_value, int | float) else None
        error_value = payload.get("error")
        error = str(error_value) if error_value is not None else None
        policy_value = payload.get("policy")
        policy = (
            str(policy_value)
            if event.event_type is EventType.POLICY_TRIGGERED and policy_value is not None
            else None
        )
        return TraceEntry(
            sequence=event.sequence,
            created_at=event.created_at,
            event_type=event.event_type,
            summary=TraceInspector._summary(event),
            from_state=from_state,
            to_state=to_state,
            duration_ms=duration,
            error=error,
            policy=policy,
            payload=payload,
        )

    @staticmethod
    def _summary(event: AgentEvent) -> str:
        payload = event.payload
        event_type = event.event_type
        if event_type is EventType.RUN_CREATED:
            return "run created"
        if event_type is EventType.STATE_CHANGED:
            return f"{payload.get('from_state')} -> {payload.get('to_state')}"
        if event_type is EventType.MODEL_REQUESTED:
            return f"provider call for step {payload.get('step')}"
        if event_type is EventType.MODEL_RESPONDED:
            response = payload.get("response")
            if isinstance(response, dict) and response.get("tool_call") is not None:
                return f"provider requested tool at step {payload.get('step')}"
            return f"provider returned final decision at step {payload.get('step')}"
        if event_type is EventType.MODEL_FAILED:
            return f"provider failed at step {payload.get('step')}"
        if event_type is EventType.SAVER_APPLIED:
            saved = payload.get("saved_percent", 0)
            if not isinstance(saved, int | float):
                saved = 0
            sign = "\u2212" if saved > 0 else ("+" if saved < 0 else "")
            return (
                f"saver: {payload.get('preset')} {sign}{abs(saved)}% "
                f"({payload.get('tokens_before')}→{payload.get('tokens_after')})"
            )
        if event_type is EventType.TOOL_REQUESTED:
            return f"tool requested: {payload.get('name')}"
        if event_type is EventType.TOOL_APPROVAL_REQUESTED:
            return f"approval requested: {payload.get('name')}"
        if event_type is EventType.TOOL_APPROVED:
            return f"tool approved: {payload.get('name')}"
        if event_type is EventType.TOOL_DENIED:
            return f"tool denied: {payload.get('name')}"
        if event_type is EventType.TOOL_STARTED:
            return f"tool started: {payload.get('name')}"
        if event_type is EventType.TOOL_COMPLETED:
            return f"tool completed: {payload.get('name')}"
        if event_type is EventType.TOOL_FAILED:
            return f"tool failed: {payload.get('name')}"
        if event_type is EventType.POLICY_TRIGGERED:
            return f"policy stopped run: {payload.get('policy')}"
        if event_type is EventType.CHECKPOINT_CREATED:
            return f"checkpoint at {payload.get('state')}"
        if event_type is EventType.RUN_RESUMED:
            return f"resumed from checkpoint {payload.get('checkpoint_id')}"
        if event_type in {
            EventType.RUN_COMPLETED,
            EventType.RUN_FAILED,
            EventType.RUN_STOPPED,
            EventType.RUN_CANCELLED,
        }:
            return f"terminal: {payload.get('state')} / {payload.get('stop_reason')}"
        return event_type.value
