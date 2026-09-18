"""Deterministic, side-effect-free replay of persisted model decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from avo.events import TERMINAL_EVENT_TYPES, AgentEvent, EventType
from avo.exceptions import AvoError
from avo.models import ModelRequest, ModelResponse, RunRecord, ToolResult
from avo.providers.base import ModelProvider
from avo.state import is_terminal
from avo.storage.base import EventStore
from avo.tools import canonical_fingerprint


class ReplayError(AvoError):
    """Raised when an event ledger cannot be replayed deterministically."""


def _request_payload(request: ModelRequest) -> dict[str, JsonValue]:
    """Return request data with identity fields removed from the comparison."""

    payload = cast(dict[str, JsonValue], request.model_dump(mode="json"))
    payload.pop("request_id", None)
    payload.pop("run_id", None)
    return payload


def _request_fingerprint(request: ModelRequest) -> str:
    return canonical_fingerprint(cast(JsonValue, _request_payload(request)))


@dataclass(frozen=True)
class ReplayStep:
    """One recorded model request and its exact response."""

    request_fingerprint: str
    response: ModelResponse


@dataclass(frozen=True)
class ReplayTranscript:
    """Immutable replay material extracted from one completed run."""

    run_id: str
    steps: tuple[ReplayStep, ...]
    tool_results: tuple[ToolResult, ...]
    fingerprint: str

    @classmethod
    def from_events(cls, run: RunRecord, events: list[AgentEvent]) -> ReplayTranscript:
        """Validate and extract the deterministic portions of a run ledger."""

        if not is_terminal(run.state):
            raise ReplayError("Replay requires a terminal run.")
        if not events or events[0].event_type is not EventType.RUN_CREATED:
            raise ReplayError("Replay ledger must start with RUN_CREATED.")
        if events[-1].event_type not in TERMINAL_EVENT_TYPES:
            raise ReplayError("Replay ledger has no terminal event.")
        if any(event.run_id != run.run_id for event in events):
            raise ReplayError("Replay ledger contains an event for another run.")

        pending: ModelRequest | None = None
        steps: list[ReplayStep] = []
        started: set[str] = set()
        finished: set[str] = set()
        tool_results: list[ToolResult] = []

        for event in sorted(events, key=lambda item: item.sequence):
            if event.event_type is EventType.MODEL_REQUESTED:
                if pending is not None:
                    raise ReplayError("Replay ledger contains consecutive model requests.")
                try:
                    pending = ModelRequest.model_validate(event.payload["request"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ReplayError("MODEL_REQUESTED has an invalid request payload.") from exc
            elif event.event_type is EventType.MODEL_RESPONDED:
                if pending is None:
                    raise ReplayError("MODEL_RESPONDED has no preceding model request.")
                try:
                    response = ModelResponse.model_validate(event.payload["response"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ReplayError("MODEL_RESPONDED has an invalid response payload.") from exc
                steps.append(ReplayStep(_request_fingerprint(pending), response))
                pending = None
            elif event.event_type is EventType.TOOL_STARTED:
                call_id = event.payload.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise ReplayError("TOOL_STARTED has no valid tool_call_id.")
                if call_id in started:
                    raise ReplayError(f"Tool call {call_id!r} started more than once.")
                started.add(call_id)
            elif event.event_type in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}:
                call_id = event.payload.get("tool_call_id")
                if not isinstance(call_id, str) or call_id not in started:
                    raise ReplayError("Tool result has no preceding TOOL_STARTED event.")
                if call_id in finished:
                    raise ReplayError(f"Tool call {call_id!r} has multiple results.")
                try:
                    result = ToolResult.model_validate(event.payload["result"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ReplayError("Tool result event has an invalid result payload.") from exc
                if result.tool_call_id != call_id:
                    raise ReplayError("Tool result call ID does not match its event.")
                finished.add(call_id)
                tool_results.append(result)

        if pending is not None:
            raise ReplayError("Replay ledger contains a model request without a response.")
        unresolved = started - finished
        if unresolved:
            ids = ", ".join(sorted(unresolved))
            raise ReplayError(f"Tool calls started without a durable result: {ids}")
        if not steps:
            raise ReplayError("Replay ledger contains no model decision.")

        material: dict[str, JsonValue] = {
            "run_id": run.run_id,
            "steps": [
                {
                    "request": step.request_fingerprint,
                    "response": step.response.model_dump(mode="json"),
                }
                for step in steps
            ],
            "tool_results": [result.model_dump(mode="json") for result in tool_results],
        }
        return cls(
            run_id=run.run_id,
            steps=tuple(steps),
            tool_results=tuple(tool_results),
            fingerprint=canonical_fingerprint(cast(JsonValue, material)),
        )


class DeterministicReplayProvider(ModelProvider):
    """Provider that returns recorded responses and rejects request drift."""

    def __init__(self, transcript: ReplayTranscript) -> None:
        self._transcript = transcript
        self._cursor = 0

    @property
    def remaining(self) -> int:
        """Return the number of recorded decisions not consumed yet."""

        return len(self._transcript.steps) - self._cursor

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Return the next response only when the request fingerprint matches."""

        if self._cursor >= len(self._transcript.steps):
            raise ReplayError("Replay provider has no recorded response remaining.")
        step = self._transcript.steps[self._cursor]
        actual = _request_fingerprint(request)
        if actual != step.request_fingerprint:
            raise ReplayError(
                "Replay request divergence at step "
                f"{request.step}: expected {step.request_fingerprint}, got {actual}."
            )
        self._cursor += 1
        return step.response.model_copy(deep=True)


@dataclass(frozen=True)
class ReplayReport:
    """Machine-readable result of validating one run for replay."""

    run_id: str
    verified: bool
    matched_events: int
    divergences: tuple[str, ...] = ()
    fingerprint: str | None = None

    def to_text(self) -> str:
        status = "verified" if self.verified else "failed"
        lines = [
            f"Replay {status}: {self.run_id}",
            f"Matched events: {self.matched_events}",
        ]
        if self.fingerprint:
            lines.append(f"Fingerprint: {self.fingerprint}")
        lines.extend(f"Divergence: {item}" for item in self.divergences)
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "verified": self.verified,
                "matched_events": self.matched_events,
                "divergences": list(self.divergences),
                "fingerprint": self.fingerprint,
            },
            sort_keys=True,
        )


async def replay_run(store: EventStore, run_id: str) -> ReplayReport:
    """Validate a run's durable replay material without invoking tools/providers."""

    run = await store.get_run(run_id)
    events = await store.get_events(run_id)
    try:
        transcript = ReplayTranscript.from_events(run, events)
    except ReplayError as exc:
        return ReplayReport(
            run_id=run_id,
            verified=False,
            matched_events=0,
            divergences=(str(exc),),
        )
    return ReplayReport(
        run_id=run_id,
        verified=True,
        matched_events=len(events),
        fingerprint=transcript.fingerprint,
    )


__all__ = [
    "DeterministicReplayProvider",
    "ReplayError",
    "ReplayReport",
    "ReplayStep",
    "ReplayTranscript",
    "replay_run",
]
