"""State-machine handlers for :mod:`avo.runtime`.

Each handler corresponds to one ``RunState`` value and is called from
:class:`AgentRuntime._drive` while the run is non-terminal. Handlers
read the run context, mutate it, and either transition forward (via
``runtime._transition`` / ``runtime._trigger_policy`` /
``runtime._terminate``) or wait on the next state by returning.

Handlers live in their own module so the public ``AgentRuntime``
class can stay below the file-size limit while the state machine
remains a single coherent flow. The free functions take
``(runtime, context, ...)`` so they can reach the provider, the
tool registry, the approval callback, and the persistence helpers
defined on ``AgentRuntime``.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue, ValidationError

from avo.circuit_breaker import BreakerOpen
from avo.events import EventType
from avo.exceptions import (
    FakeProviderExhaustedError,
    ProviderError,
    ToolAlreadyCompletedError,
    UnsafeResumeError,
)
from avo.hooks import HookAction, HookContext, HookEvent
from avo.models import ModelRequest, ModelResponse, ToolResult, utc_now
from avo.providers.streaming import StreamAssembler, StreamingModelProvider
from avo.state import RunState, StopReason
from avo.tools import tool_call_fingerprint

if TYPE_CHECKING:
    from avo.runtime import AgentRuntime, _RunContext


async def handle_created(runtime: AgentRuntime, context: _RunContext) -> None:
    await runtime._transition(context, RunState.MODEL_PENDING)


async def handle_model_pending(runtime: AgentRuntime, context: _RunContext) -> None:
    boundary_reason = runtime._operation_boundary_reason(context)
    if boundary_reason is not None:
        await runtime._trigger_policy(context, boundary_reason)
        return
    if context.next_step > context.policy.max_steps:
        await runtime._trigger_policy(context, StopReason.MAX_STEPS)
        return

    step = context.next_step
    context.next_step += 1
    request = ModelRequest(
        run_id=context.run.run_id,
        step=step,
        messages=context.messages,
        tools=runtime.tools.metadata,
    )
    context.run = runtime._active_record(context, steps=step)
    await runtime._append_with_run(
        context,
        EventType.MODEL_REQUESTED,
        {
            "step": step,
            "request": cast(JsonValue, request.model_dump(mode="json")),
        },
    )

    started = time.perf_counter()
    breaker = runtime._breaker
    if breaker is not None:
        try:
            breaker.allow()
        except BreakerOpen as exc:
            await handle_provider_error(
                runtime,
                context,
                step,
                started,
                ProviderError(str(exc), retryable=False),
            )
            return

    provider = runtime.provider
    callback = runtime.stream_callback
    try:
        if callback is not None and isinstance(provider, StreamingModelProvider):
            generated = await _stream_with_callback(
                runtime,
                context,
                request,
                provider,
                callback,
            )
        else:
            provider_call = runtime.provider.generate(request)
            if context.policy.provider_timeout_seconds is None:
                generated = await provider_call
            else:
                generated = await asyncio.wait_for(
                    provider_call,
                    timeout=context.policy.provider_timeout_seconds,
                )
        from avo.models import ModelResponse

        response = ModelResponse.model_validate(generated)
        if breaker is not None:
            breaker.record_success()
    except TimeoutError:
        if breaker is not None:
            breaker.record_failure()
        timeout = context.policy.provider_timeout_seconds
        await handle_provider_error(
            runtime,
            context,
            step,
            started,
            ProviderError(
                f"Provider call exceeded the configured timeout of {timeout} seconds.",
                retryable=True,
            ),
        )
        return
    except ValidationError as exc:
        if breaker is not None:
            breaker.record_failure()
        await runtime._append(
            context,
            EventType.MODEL_FAILED,
            {
                "step": step,
                "error": str(exc),
                "error_type": "invalid_model_response",
                "duration_ms": (time.perf_counter() - started) * 1000,
            },
        )
        await runtime._terminate(
            context,
            RunState.FAILED,
            StopReason.INVALID_MODEL_RESPONSE,
            error=f"Provider returned an invalid model response: {exc}",
        )
        return
    except Exception as exc:
        if breaker is not None:
            breaker.record_failure()
        await handle_provider_error(runtime, context, step, started, exc)
        return

    if response.usage is None:
        context.token_accounting_available = False
    else:
        context.token_usage = context.token_usage.plus(response.usage)
    context.consecutive_errors = 0
    context.pending_response = response
    context.detector.record_model_response(response)
    if response.tool_call is not None:
        context.detector.record_action(response.tool_call)
    context.messages.append(runtime._assistant_message(response))
    context.run = runtime._active_record(context, error=None)

    await runtime._append(
        context,
        EventType.MODEL_RESPONDED,
        {
            "step": step,
            "response": cast(JsonValue, response.model_dump(mode="json")),
            "duration_ms": (time.perf_counter() - started) * 1000,
            "token_accounting_available": context.token_accounting_available,
        },
    )
    await runtime._transition(context, RunState.DECISION_RECEIVED)

    if context.policy.checkpoint_every_step or response.tool_call is not None:
        await runtime._checkpoint(context)

    token_reason = context.policy.token_budget_reason(
        context.token_usage,
        accounting_available=context.token_accounting_available,
    )
    if token_reason is not None:
        await runtime._trigger_policy(context, token_reason)


async def _stream_with_callback(
    runtime: AgentRuntime,
    context: _RunContext,
    request: ModelRequest,
    provider: StreamingModelProvider,
    callback: Callable[[str], None],
) -> ModelResponse:
    """Consume ``provider.stream`` while forwarding text deltas to ``callback``.

    The assembled response is byte-identical to ``generate`` for the same
    provider output (task 3a protocol). Mirrors the generate path's
    timeout and breaker contract: exceptions re-raise unchanged so the
    caller's except blocks classify them. If the stream dies after deltas
    were already displayed, ``stream_interrupt_callback`` fires once
    before the re-raise so the display can flag the upcoming retry.
    """
    forwarded_text = False

    async def _consume() -> ModelResponse:
        nonlocal forwarded_text
        assembler = StreamAssembler()
        async for chunk in provider.stream(request):
            if chunk.text:
                forwarded_text = True
                callback(chunk.text)
            assembler.feed(chunk)
        return assembler.build()

    timeout = context.policy.provider_timeout_seconds
    try:
        if timeout is None:
            return await _consume()
        return await asyncio.wait_for(_consume(), timeout=timeout)
    except Exception:
        if forwarded_text and runtime.stream_interrupt_callback is not None:
            runtime.stream_interrupt_callback()
        raise


async def handle_provider_error(
    runtime: AgentRuntime,
    context: _RunContext,
    step: int,
    started: float,
    exc: Exception,
) -> None:
    context.consecutive_errors += 1
    context.run = runtime._active_record(
        context,
        error=f"{type(exc).__name__}: {exc}",
    )
    await runtime._append_with_run(
        context,
        EventType.MODEL_FAILED,
        {
            "step": step,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "consecutive_errors": context.consecutive_errors,
        },
    )
    await runtime._checkpoint(context)

    if isinstance(exc, FakeProviderExhaustedError) or (
        isinstance(exc, ProviderError) and not exc.retryable
    ):
        await runtime._terminate(
            context,
            RunState.FAILED,
            StopReason.PROVIDER_ERROR,
            error=f"Provider failed without a retry path: {exc}",
        )
    elif context.consecutive_errors >= context.policy.consecutive_error_limit:
        await runtime.hooks.fire(
            HookContext(
                event=HookEvent.NOTIFICATION,
                run_id=context.run.run_id,
                notification=(
                    f"consecutive_errors={context.consecutive_errors} hit limit "
                    f"{context.policy.consecutive_error_limit}"
                ),
                extra={"step": step, "consecutive_errors": context.consecutive_errors},
            )
        )
        await runtime._trigger_policy(context, StopReason.CONSECUTIVE_ERRORS)


async def handle_decision_received(runtime: AgentRuntime, context: _RunContext) -> None:
    response = runtime._require_pending_response(context)
    if response.is_final:
        if runtime.memory is not None and response.content is not None:
            runtime.memory.remember_output(response.content, session_id=context.run.run_id)
        await runtime._terminate(
            context,
            RunState.COMPLETED,
            StopReason.COMPLETED,
            output=response.content,
        )
        return

    call = runtime._require_tool_call(response)
    await runtime._append(
        context,
        EventType.TOOL_REQUESTED,
        {
            "tool_call_id": call.tool_call_id,
            "idempotency_key": tool_call_fingerprint(call),
            "name": call.name,
            "arguments": call.arguments,
        },
    )
    if context.detector.repeated_action(context.policy.repeated_action_limit):
        await runtime._trigger_policy(context, StopReason.REPEATED_ACTION)
        return
    await runtime._transition(context, RunState.TOOL_PENDING)


async def handle_tool_pending(runtime: AgentRuntime, context: _RunContext) -> None:
    call = runtime._require_tool_call(runtime._require_pending_response(context))
    await runtime._append(
        context,
        EventType.TOOL_APPROVAL_REQUESTED,
        {
            "tool_call_id": call.tool_call_id,
            "idempotency_key": tool_call_fingerprint(call),
            "name": call.name,
            "mode": "v0.1_callback",
        },
    )
    await runtime._transition(context, RunState.APPROVAL_PENDING)


async def handle_approval_pending(runtime: AgentRuntime, context: _RunContext) -> None:
    call = runtime._require_tool_call(runtime._require_pending_response(context))
    decision = await runtime.hooks.fire(
        HookContext(event=HookEvent.PRE_TOOL_USE, run_id=context.run.run_id, tool_call=call)
    )
    if decision.action is HookAction.BLOCK:
        await runtime._append(
            context,
            EventType.TOOL_DENIED,
            {
                "tool_call_id": call.tool_call_id,
                "name": call.name,
                "reason": decision.reason,
                "blocked_by": "hook",
            },
        )
        await runtime._trigger_policy(context, StopReason.POLICY_DENIED)
        return
    approved_value = runtime._approval_callback(call)
    approved = await approved_value if inspect.isawaitable(approved_value) else approved_value
    if not approved:
        await runtime._append(
            context,
            EventType.TOOL_DENIED,
            {"tool_call_id": call.tool_call_id, "name": call.name},
        )
        await runtime._trigger_policy(context, StopReason.POLICY_DENIED)
        return
    await runtime._append(
        context,
        EventType.TOOL_APPROVED,
        {
            "tool_call_id": call.tool_call_id,
            "idempotency_key": tool_call_fingerprint(call),
            "name": call.name,
            "mode": "v0.1_callback",
        },
    )
    await runtime._transition(context, RunState.TOOL_EXECUTING)


async def handle_tool_executing(runtime: AgentRuntime, context: _RunContext) -> None:
    boundary_reason = runtime._operation_boundary_reason(context)
    if boundary_reason is not None:
        await runtime._trigger_policy(context, boundary_reason)
        return
    response = runtime._require_pending_response(context)
    call = runtime._require_tool_call(response)
    if call.tool_call_id in context.completed_tool_call_ids:
        raise ToolAlreadyCompletedError(
            f"Refusing to execute completed tool call {call.tool_call_id!r}."
        )

    await runtime._append(
        context,
        EventType.TOOL_STARTED,
        {
            "tool_call_id": call.tool_call_id,
            "idempotency_key": tool_call_fingerprint(call),
            "name": call.name,
            "arguments": call.arguments,
        },
    )
    started_at = utc_now()
    started_clock = time.perf_counter()
    try:
        invocation = runtime.tools.invoke(
            call,
            completed_tool_call_ids=context.completed_tool_call_ids,
        )
        if context.policy.tool_timeout_seconds is None:
            result = await invocation
        else:
            result = await asyncio.wait_for(
                invocation,
                timeout=context.policy.tool_timeout_seconds,
            )
    except TimeoutError:
        finished_at = utc_now()
        result = ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=call.name,
            success=False,
            error=(
                f"Tool {call.name!r} exceeded the configured timeout of "
                f"{context.policy.tool_timeout_seconds} seconds."
            ),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0.0, (time.perf_counter() - started_clock) * 1000),
        )
    result_type = EventType.TOOL_COMPLETED if result.success else EventType.TOOL_FAILED
    await runtime._append(
        context,
        result_type,
        {
            "tool_call_id": call.tool_call_id,
            "name": call.name,
            "result": cast(JsonValue, result.model_dump(mode="json")),
            "duration_ms": result.duration_ms,
            "error": result.error,
        },
    )

    runtime._record_tool_result(context, result)
    context.pending_response = None
    if result.success:
        context.consecutive_errors = 0
        context.run = runtime._active_record(context, error=None)
    else:
        context.consecutive_errors += 1
        context.run = runtime._active_record(context, error=result.error)
    await runtime._transition(context, RunState.OBSERVATION_RECORDED)

    # PostToolUse is informational only — fire after the tool result is
    # persisted so observers see the final outcome.
    await runtime.hooks.fire(
        HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id=context.run.run_id,
            tool_call=call,
            tool_result=result,
        )
    )

    # A successful or durably recorded tool result always forces a checkpoint.
    await runtime._checkpoint(context)

    boundary_reason = runtime._operation_boundary_reason(context)
    if boundary_reason is not None:
        await runtime._trigger_policy(context, boundary_reason)
    elif context.consecutive_errors >= context.policy.consecutive_error_limit:
        await runtime._trigger_policy(context, StopReason.CONSECUTIVE_ERRORS)
    elif context.detector.no_progress(context.policy.no_progress_window):
        await runtime._trigger_policy(context, StopReason.NO_PROGRESS)


async def handle_observation_recorded(runtime: AgentRuntime, context: _RunContext) -> None:
    await runtime._transition(context, RunState.MODEL_PENDING)


async def handle_paused(runtime: AgentRuntime, context: _RunContext) -> None:
    raise UnsafeResumeError(
        f"Run {context.run.run_id!r} is paused without a recorded continuation state."
    )


__all__ = [
    "handle_approval_pending",
    "handle_created",
    "handle_decision_received",
    "handle_model_pending",
    "handle_observation_recorded",
    "handle_paused",
    "handle_provider_error",
    "handle_tool_executing",
    "handle_tool_pending",
]
