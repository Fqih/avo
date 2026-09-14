"""Public ``AgentRuntime`` — state-machine driver for bounded agent loops.

The state-machine handlers live in :mod:`avo.runtime_handlers`
and the persistence helpers (checkpoint, terminate, transition, ...)
live in :mod:`avo.runtime_persistence`. Both modules depend
on this file's public class so the dependency direction stays
one-way: ``runtime`` -> ``{handlers, persistence}``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue, ValidationError

from avo import runtime_handlers, runtime_persistence
from avo.circuit_breaker import CircuitBreaker
from avo.events import AgentEvent, EventType
from avo.exceptions import (
    CheckpointNotFoundError,
    RunAlreadyTerminalError,
    StorageError,
)
from avo.hooks import HookContext, HookEvent, HookRegistry
from avo.integrations.lethe import LetheMemoryAdapter
from avo.models import (
    Checkpoint,
    ModelResponse,
    RunRecord,
    RunResult,
    TokenUsage,
    ToolCall,
    ToolResult,
    utc_now,
)
from avo.observability import record_usage, span_for_turn
from avo.policies import LoopPolicy
from avo.progress import ProgressDetector
from avo.providers.base import ModelProvider
from avo.state import RunState, StopReason, is_terminal
from avo.storage.base import EventStore
from avo.storage.memory import InMemoryEventStore
from avo.tools import Tool, ToolRegistry

if TYPE_CHECKING:
    from avo.tracing import RunTrace

Clock = Callable[[], datetime]
ApprovalCallback = Callable[[ToolCall], bool | Awaitable[bool]]


async def _always_approve(call: ToolCall) -> bool:
    """Exercise the approval state in v0.1 without implementing approval policy."""

    del call
    return True


@dataclass
class _RunContext:
    run: RunRecord
    policy: LoopPolicy
    messages: list[dict[str, JsonValue]]
    next_step: int
    token_usage: TokenUsage
    token_accounting_available: bool
    consecutive_errors: int
    detector: ProgressDetector
    completed_tool_call_ids: set[str]
    user_state: dict[str, JsonValue]
    pending_response: ModelResponse | None = None
    # Snapshotted from the runtime at run entry (under the execution
    # lock) so installing a printer for a later turn cannot bind to an
    # in-flight run sharing this runtime.
    stream_callback: Callable[[str], None] | None = None
    stream_interrupt_callback: Callable[[], None] | None = None


class AgentRuntime:
    """Coordinate model decisions, tools, policies, events, and checkpoints.

    One runtime instance serializes its own runs because a provider may
    maintain cursor state. Different runtime instances can operate
    independently.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        tools: Iterable[Tool] = (),
        policy: LoopPolicy | None = None,
        event_store: EventStore | None = None,
        clock: Clock = utc_now,
        approval_callback: ApprovalCallback | None = None,
        memory: LetheMemoryAdapter | None = None,
        hooks: HookRegistry | None = None,
        stream_callback: Callable[[str], None] | None = None,
        stream_interrupt_callback: Callable[[], None] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = ToolRegistry(tools)
        self.policy = policy or LoopPolicy()
        self.event_store = event_store or InMemoryEventStore()
        self._clock = clock
        self._approval_callback = approval_callback or _always_approve
        # Purely observational display plumbing (see handle_model_pending);
        # public so callers can swap them per turn like ``provider``.
        # Each run/resume snapshots both at entry, so a swap only binds
        # from the next run — never to an in-flight one.
        # ``stream_interrupt_callback`` fires when a stream that already
        # forwarded text deltas dies mid-tokens, before the retry's deltas.
        self.stream_callback = stream_callback
        self.stream_interrupt_callback = stream_interrupt_callback
        self.memory = memory
        self.hooks = hooks if hooks is not None else HookRegistry()
        self._breaker: CircuitBreaker | None = (
            CircuitBreaker(self.policy.circuit_breaker)
            if self.policy.circuit_breaker is not None
            else None
        )
        self._execution_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def run(
        self,
        task: str,
        *,
        user_state: dict[str, JsonValue] | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """Create and execute a run until it reaches one explicit terminal state."""

        async with self._execution_lock:
            now = self._now()
            values: dict[str, object] = {
                "task": task,
                "user_state": user_state or {},
                "created_at": now,
                "updated_at": now,
            }
            if run_id is not None:
                values["run_id"] = run_id
            record = RunRecord.model_validate(values)
            messages: list[dict[str, JsonValue]] = [{"role": "user", "content": task}]
            if self.memory is not None:
                recalled = self.memory.recall_text(task)
                if recalled:
                    messages.append(
                        {
                            "role": "system",
                            "content": "Relevant memories:\n- " + "\n- ".join(recalled),
                        }
                    )
            context = _RunContext(
                run=record,
                policy=self.policy,
                messages=messages,
                next_step=1,
                token_usage=TokenUsage(),
                token_accounting_available=True,
                consecutive_errors=0,
                detector=ProgressDetector(),
                completed_tool_call_ids=set(),
                user_state=dict(user_state or {}),
                stream_callback=self.stream_callback,
                stream_interrupt_callback=self.stream_interrupt_callback,
            )
            created = AgentEvent(
                run_id=record.run_id,
                event_type=EventType.RUN_CREATED,
                created_at=now,
                payload={
                    "task": task,
                    "policy": cast(dict[str, JsonValue], self.policy.model_dump(mode="json")),
                },
            )
            persisted = await self.event_store.create_run(record, created)
            await self._event_persisted(persisted)

            try:
                await runtime_persistence.transition(self, context, RunState.MODEL_PENDING)
                await runtime_persistence.checkpoint(self, context)
                return await self._drive(context)
            except asyncio.CancelledError:
                if not is_terminal(context.run.state):
                    await runtime_persistence.terminate(
                        self,
                        context,
                        RunState.CANCELLED,
                        StopReason.USER_CANCELLED,
                        error="Run cancelled by the caller.",
                    )
                raise
            except StorageError:
                raise
            except Exception as exc:
                if not is_terminal(context.run.state):
                    await runtime_persistence.terminate(
                        self,
                        context,
                        RunState.FAILED,
                        StopReason.INTERNAL_ERROR,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                return self._result(context.run)

    async def resume(self, run_id: str) -> RunResult:
        """Resume a persisted non-terminal run from its latest safe checkpoint."""

        async with self._execution_lock:
            record = await self.event_store.get_run(run_id)
            if is_terminal(record.state):
                raise RunAlreadyTerminalError(
                    f"Run {run_id!r} is already terminal ({record.state.value}) and cannot resume."
                )
            checkpoint_obj = await self.event_store.get_latest_checkpoint(run_id)
            if checkpoint_obj is None:
                raise CheckpointNotFoundError(
                    f"Run {run_id!r} has no checkpoint. Resume requires at least one "
                    "successfully persisted checkpoint."
                )

            policy = LoopPolicy.model_validate(checkpoint_obj.policy)
            context = _RunContext(
                run=record,
                policy=policy,
                messages=[dict(message) for message in checkpoint_obj.messages],
                next_step=checkpoint_obj.next_step,
                token_usage=checkpoint_obj.token_usage.model_copy(),
                token_accounting_available=checkpoint_obj.token_accounting_available,
                consecutive_errors=checkpoint_obj.consecutive_errors,
                detector=ProgressDetector(
                    action_history=checkpoint_obj.repeated_action_history,
                    observation_history=checkpoint_obj.observation_fingerprints,
                    model_history=checkpoint_obj.model_response_fingerprints,
                    progress_markers=checkpoint_obj.progress_markers,
                ),
                completed_tool_call_ids=set(checkpoint_obj.completed_tool_call_ids),
                user_state=dict(checkpoint_obj.user_state),
                pending_response=checkpoint_obj.pending_response,
                stream_callback=self.stream_callback,
                stream_interrupt_callback=self.stream_interrupt_callback,
            )
            runtime_persistence.restore_provider(self, checkpoint_obj.provider_metadata)
            await runtime_persistence.reconcile_after_checkpoint(self, context, checkpoint_obj)

            resumed = await runtime_persistence.append(
                self,
                context,
                EventType.RUN_RESUMED,
                {
                    "checkpoint_id": checkpoint_obj.checkpoint_id,
                    "checkpoint_sequence": checkpoint_obj.last_event_sequence,
                    "state": context.run.state.value,
                },
            )
            del resumed
            try:
                return await self._drive(context)
            except asyncio.CancelledError:
                if not is_terminal(context.run.state):
                    await runtime_persistence.terminate(
                        self,
                        context,
                        RunState.CANCELLED,
                        StopReason.USER_CANCELLED,
                        error="Resumed run cancelled by the caller.",
                    )
                raise
            except StorageError:
                raise
            except Exception as exc:
                if not is_terminal(context.run.state):
                    await runtime_persistence.terminate(
                        self,
                        context,
                        RunState.FAILED,
                        StopReason.INTERNAL_ERROR,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                return self._result(context.run)

    async def inspect(self, run_id: str) -> RunTrace:
        """Build a chronological trace for a stored run."""

        from avo.tracing import TraceInspector

        return await TraceInspector(self.event_store).inspect(run_id)

    # ------------------------------------------------------------------
    # Internal driver and small helpers
    # ------------------------------------------------------------------

    async def _drive(self, context: _RunContext) -> RunResult:
        handlers = {
            RunState.CREATED: runtime_handlers.handle_created,
            RunState.MODEL_PENDING: runtime_handlers.handle_model_pending,
            RunState.DECISION_RECEIVED: runtime_handlers.handle_decision_received,
            RunState.TOOL_PENDING: runtime_handlers.handle_tool_pending,
            RunState.APPROVAL_PENDING: runtime_handlers.handle_approval_pending,
            RunState.TOOL_EXECUTING: runtime_handlers.handle_tool_executing,
            RunState.OBSERVATION_RECORDED: runtime_handlers.handle_observation_recorded,
            RunState.PAUSED: runtime_handlers.handle_paused,
        }
        provider_name = getattr(self.provider, "name", None) or "unknown"
        model_name = getattr(self.provider, "model", None)
        with span_for_turn(context.run.run_id, provider=provider_name, model=model_name) as span:
            while not is_terminal(context.run.state):
                handler = handlers.get(context.run.state)
                if handler is None:
                    raise RuntimeError(f"No state handler exists for {context.run.state.value!r}.")
                await handler(self, context)
            record_usage(
                span,
                input_tokens=context.token_usage.input_tokens,
                output_tokens=context.token_usage.output_tokens,
            )
        await self.hooks.fire(
            HookContext(event=HookEvent.STOP, run_id=context.run.run_id, run=context.run)
        )
        return self._result(context.run)

    def _active_record(
        self,
        context: _RunContext,
        *,
        state: RunState | None = None,
        steps: int | None = None,
        error: str | None = None,
    ) -> RunRecord:
        return RunRecord.model_validate(
            {
                **context.run.model_dump(),
                "state": state or context.run.state,
                "steps": context.run.steps if steps is None else steps,
                "token_usage": context.token_usage,
                "token_accounting_available": context.token_accounting_available,
                "user_state": context.user_state,
                "error": error,
                "updated_at": self._now(),
            }
        )

    def _operation_boundary_reason(self, context: _RunContext) -> StopReason | None:
        return context.policy.runtime_reason(self._elapsed(context))

    def _elapsed(self, context: _RunContext, *, now: datetime | None = None) -> float:
        current = now or self._now()
        return max(0.0, (current - context.run.created_at).total_seconds())

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Runtime clock must return a timezone-aware datetime.")
        return value

    @staticmethod
    def _assistant_message(response: ModelResponse) -> dict[str, JsonValue]:
        if response.tool_call is not None:
            return {
                "role": "assistant",
                "tool_call": cast(JsonValue, response.tool_call.model_dump(mode="json")),
            }
        return {"role": "assistant", "content": response.content}

    @staticmethod
    def _require_pending_response(context: _RunContext) -> ModelResponse:
        if context.pending_response is None:
            raise RuntimeError(
                f"State {context.run.state.value!r} requires a pending model response."
            )
        return context.pending_response

    @staticmethod
    def _require_tool_call(response: ModelResponse) -> ToolCall:
        if response.tool_call is None:
            raise RuntimeError("The current model decision does not contain a tool call.")
        return response.tool_call

    @staticmethod
    def _result(run: RunRecord) -> RunResult:
        if run.stop_reason is None:
            raise RuntimeError("Cannot build RunResult before a stop reason is persisted.")
        return RunResult(
            run_id=run.run_id,
            status=run.state,
            stop_reason=run.stop_reason,
            output=run.output,
            error=run.error,
            steps=run.steps,
            token_usage=run.token_usage,
            token_accounting_available=run.token_accounting_available,
        )

    async def _event_persisted(self, event: AgentEvent) -> None:
        """Hook called after each durable event; useful for failure-injection tests."""

        del event

    # ------------------------------------------------------------------
    # Persistence facade — delegates to runtime_persistence. Kept here
    # so existing callers (and runtime_handlers) can keep using
    # ``self._append``, ``self._checkpoint`` etc.
    # ------------------------------------------------------------------

    async def _append(
        self,
        context: _RunContext,
        event_type: EventType,
        payload: dict[str, JsonValue],
    ) -> AgentEvent:
        return await runtime_persistence.append(self, context, event_type, payload)

    async def _append_with_run(
        self,
        context: _RunContext,
        event_type: EventType,
        payload: dict[str, JsonValue],
    ) -> AgentEvent:
        return await runtime_persistence.append_with_run(self, context, event_type, payload)

    async def _transition(self, context: _RunContext, state: RunState) -> AgentEvent:
        return await runtime_persistence.transition(self, context, state)

    async def _checkpoint(self, context: _RunContext):  # type: ignore[no-untyped-def]
        return await runtime_persistence.checkpoint(self, context)

    def _record_tool_result(self, context: _RunContext, result: ToolResult) -> None:
        runtime_persistence.record_tool_result(self, context, result)

    def _provider_snapshot(self) -> dict[str, JsonValue]:
        return runtime_persistence.provider_snapshot(self)

    def _restore_provider(self, metadata: dict[str, JsonValue]) -> None:
        runtime_persistence.restore_provider(self, metadata)

    async def _reconcile_after_checkpoint(
        self,
        context: _RunContext,
        checkpoint_obj: Checkpoint,
    ) -> None:
        await runtime_persistence.reconcile_after_checkpoint(self, context, checkpoint_obj)

    async def _trigger_policy(
        self,
        context: _RunContext,
        reason: StopReason,
    ) -> None:
        await runtime_persistence.trigger_policy(self, context, reason)

    async def _terminate(
        self,
        context: _RunContext,
        state: RunState,
        reason: StopReason,
        *,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        await runtime_persistence.terminate(
            self, context, state, reason, output=output, error=error
        )


# Backwards-compat shim for ``from avo.runtime import _RunContext``.
__all__ = ["AgentRuntime", "ApprovalCallback", "Clock", "_RunContext"]


# Silence unused-import warnings: ``ValidationError`` is referenced by
# runtime_handlers.handle_model_pending via ``ModelResponse.model_validate``,
# but the type itself is imported here for future re-validation hooks.
_ = ValidationError
