"""SAVER_APPLIED event legality, persistence, and trace rendering."""

from __future__ import annotations

import pytest

from avo.events import TERMINAL_EVENT_TYPES, AgentEvent, EventType, validate_event_append
from avo.models import RunRecord
from avo.storage.memory import InMemoryEventStore
from avo.tracing import TraceInspector


def _saver_event(sequence: int = 1) -> AgentEvent:
    return AgentEvent(
        run_id="run-1",
        sequence=sequence,
        event_type=EventType.SAVER_APPLIED,
        payload={
            "preset": "compact",
            "stages_applied": ["json_minify", "dedupe_tool_results"],
            "tokens_before": 100,
            "tokens_after": 40,
            "saved_percent": 60.0,
            "message_count": 4,
            "addendum": False,
        },
    )


def test_saver_applied_is_additive_not_terminal() -> None:
    assert EventType.SAVER_APPLIED == "saver_applied"
    assert EventType.SAVER_APPLIED not in TERMINAL_EVENT_TYPES


def test_append_mid_run_passes_invariants() -> None:
    created = AgentEvent(run_id="run-1", event_type=EventType.RUN_CREATED)
    validate_event_append([created], _saver_event())
    validate_event_append([created, _saver_event()], _saver_event(sequence=2))


def test_summary_renders_savings() -> None:
    summary = TraceInspector._summary(_saver_event())
    assert summary == "saver: compact \u221260.0% (100→40)"


def test_summary_renders_addendum_only() -> None:
    event = _saver_event()
    event = event.model_copy(
        update={"payload": {**event.payload, "preset": "terse", "saved_percent": -12.5}}
    )
    # negative savings (addendum cost) renders as a plus, never a double negative
    assert TraceInspector._summary(event) == "saver: terse +12.5% (100→40)"


@pytest.mark.asyncio
async def test_store_roundtrip_keeps_originals_elsewhere() -> None:
    store = InMemoryEventStore()
    created = AgentEvent(run_id="run-1", event_type=EventType.RUN_CREATED)
    await store.create_run(RunRecord(run_id="run-1", task="saver test"), created)
    await store.append_event(_saver_event())
    events = await store.get_events("run-1")
    assert [event.event_type for event in events] == [
        EventType.RUN_CREATED,
        EventType.SAVER_APPLIED,
    ]
    assert events[1].payload["stages_applied"] == ["json_minify", "dedupe_tool_results"]
