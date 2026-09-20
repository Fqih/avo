"""Tests for LoopRunner execution, backoff, and budget enforcement."""

from __future__ import annotations

from decimal import Decimal

import pytest

from avo.budget import BudgetConfig
from avo.loop.runner import LoopRunner, LoopState
from avo.loop.schedule import parse_schedule
from avo.models import ModelRequest, ModelResponse, TokenUsage
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime


def _runtime(responses: list[ModelResponse]) -> AgentRuntime:
    return AgentRuntime(provider=FakeProvider(responses))


@pytest.mark.asyncio
async def test_loop_runner_executes_tick_and_records_metrics() -> None:
    resp = ModelResponse(content="tick 1 ok", usage=TokenUsage(input_tokens=10, output_tokens=5))
    runtime = _runtime([resp])
    schedule = parse_schedule("10s")
    runner = LoopRunner(runtime, schedule=schedule, prompt="run check")

    tick = await runner.step()

    assert tick.tick_number == 1
    assert tick.status == "success"
    assert tick.output == "tick 1 ok"
    assert tick.tokens_used == 15
    assert tick.error is None
    assert len(runner.ticks) == 1


@pytest.mark.asyncio
async def test_loop_runner_stops_on_hard_budget_limit() -> None:
    runtime = _runtime(
        [
            ModelResponse(
                content="expensive",
                usage=TokenUsage(input_tokens=10000, output_tokens=10000),
            )
        ]
    )
    schedule = parse_schedule("10s")
    # Set budget limit very low ($0.01) with explicit rates
    budget = BudgetConfig(hard_limit_usd=Decimal("0.0001"))
    runner = LoopRunner(
        runtime,
        schedule=schedule,
        prompt="expensive check",
        budget_config=budget,
        rates=(Decimal("0.01"), Decimal("0.02")),
    )

    tick1 = await runner.step()
    assert tick1.status == "success"

    # Second step should be blocked by budget
    tick2 = await runner.step()
    assert tick2.status == "budget_exceeded"
    assert runner.state == LoopState.STOPPED


@pytest.mark.asyncio
async def test_loop_runner_stops_after_max_consecutive_failures() -> None:
    class FailingProvider(FakeProvider):
        async def generate(self, request: ModelRequest) -> ModelResponse:
            del request
            raise RuntimeError("upstream outage")

    runtime = AgentRuntime(provider=FailingProvider([]))
    schedule = parse_schedule("10s")
    runner = LoopRunner(runtime, schedule=schedule, prompt="check", max_consecutive_failures=2)

    tick1 = await runner.step()
    assert tick1.status == "failed"
    assert runner.state == LoopState.RUNNING

    tick2 = await runner.step()
    assert tick2.status == "failed"
    assert runner.state == LoopState.FAILED
