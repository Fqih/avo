"""Autonomous loop runner with schedule, budget, and failure backoff."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from avo.budget import BudgetChecker, BudgetConfig
from avo.exceptions import AvoError
from avo.models import TokenUsage

if TYPE_CHECKING:
    from avo.runtime import AgentRuntime

    from .schedule import LoopSchedule


class LoopRunnerError(AvoError):
    """Raised when loop execution invariants are violated."""


class LoopState(StrEnum):
    """State of the autonomous loop runner."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class LoopTick:
    """Outcome record of one schedule tick execution."""

    tick_number: int
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    status: str
    run_id: str | None = None
    output: str | None = None
    error: str | None = None
    tokens_used: int = 0
    cost_usd: Decimal | None = None


class LoopRunner:
    """Executes a prompt on schedule with budget and failure boundaries."""

    def __init__(
        self,
        runtime: AgentRuntime,
        schedule: LoopSchedule,
        prompt: str,
        *,
        budget_config: BudgetConfig | None = None,
        rates: tuple[Decimal, Decimal] | None = None,
        max_consecutive_failures: int = 3,
    ) -> None:
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be positive")
        self.runtime = runtime
        self.schedule = schedule
        self.prompt = prompt
        self.rates = rates
        self.max_consecutive_failures = max_consecutive_failures

        self._budget_checker = BudgetChecker(budget_config or BudgetConfig())
        self._state: LoopState = LoopState.IDLE
        self._ticks: list[LoopTick] = []
        self._consecutive_failures = 0
        self._cumulative_usage = TokenUsage()
        self._active_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> LoopState:
        return self._state

    @property
    def ticks(self) -> tuple[LoopTick, ...]:
        return tuple(self._ticks)

    @property
    def cumulative_usage(self) -> TokenUsage:
        return self._cumulative_usage

    async def step(self) -> LoopTick:
        """Run one iteration of the loop prompt."""
        if self._state not in {LoopState.IDLE, LoopState.RUNNING}:
            raise LoopRunnerError(f"cannot step when runner is in state {self._state.value}")

        self._state = LoopState.RUNNING
        tick_number = len(self._ticks) + 1
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        # 1. Check budget limits prior to execution
        decision = self._budget_checker.check(self._cumulative_usage, rates=self.rates)
        if not decision.allowed:
            self._state = LoopState.STOPPED
            tick = LoopTick(
                tick_number=tick_number,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                duration_ms=0.0,
                status="budget_exceeded",
                error="budget hard limit exceeded",
                cost_usd=decision.spent_usd,
            )
            self._ticks.append(tick)
            return tick

        # 2. Execute runtime turn
        try:
            result = await self.runtime.run(self.prompt)
            duration_ms = (time.monotonic() - start_mono) * 1000.0
            finished_at = datetime.now(UTC)

            # Update token usage accounting
            in_tok = self._cumulative_usage.input_tokens + result.token_usage.input_tokens
            out_tok = self._cumulative_usage.output_tokens + result.token_usage.output_tokens
            self._cumulative_usage = TokenUsage(
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
            tick_cost = self._budget_checker.check(result.token_usage, rates=self.rates).spent_usd

            if result.status.value == "completed":
                self._consecutive_failures = 0
                tick = LoopTick(
                    tick_number=tick_number,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status="success",
                    run_id=result.run_id,
                    output=result.output,
                    tokens_used=result.token_usage.total_tokens,
                    cost_usd=tick_cost,
                )
            else:
                self._consecutive_failures += 1
                error_msg = result.error or result.stop_reason.value
                tick = LoopTick(
                    tick_number=tick_number,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status="failed",
                    run_id=result.run_id,
                    error=error_msg,
                    tokens_used=result.token_usage.total_tokens,
                    cost_usd=tick_cost,
                )

        except asyncio.CancelledError:
            self._state = LoopState.STOPPED
            raise
        except Exception as exc:
            duration_ms = (time.monotonic() - start_mono) * 1000.0
            finished_at = datetime.now(UTC)
            self._consecutive_failures += 1
            tick = LoopTick(
                tick_number=tick_number,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

        self._ticks.append(tick)

        # 3. Check failure threshold
        if self._consecutive_failures >= self.max_consecutive_failures:
            self._state = LoopState.FAILED

        return tick

    def stop(self) -> None:
        """Stop the loop runner."""
        self._state = LoopState.STOPPED
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()

    def pause(self) -> None:
        """Pause scheduling without discarding progress."""
        if self._state == LoopState.RUNNING:
            self._state = LoopState.PAUSED

    def resume(self) -> None:
        """Resume a paused loop."""
        if self._state == LoopState.PAUSED:
            self._state = LoopState.RUNNING

    async def run_forever(self) -> None:
        """Run the scheduling loop until stopped or failed."""
        self._state = LoopState.RUNNING
        while self._state == LoopState.RUNNING:
            await self.step()
            if self._state != LoopState.RUNNING:
                break
            delay = self.schedule.compute_next_delay()
            await asyncio.sleep(delay)
