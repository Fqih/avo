"""``avo bench`` — lightweight cross-provider benchmark harness.

MVP scope:

- Use :class:`avo.providers.fake.FakeProvider` as the deterministic
  baseline. The provider's scripted responses let the harness run
  reproducibly without any HTTP traffic or API keys.
- Capture per-turn metrics: latency, token usage (input + output),
  step count, stop reason.
- Emit a JSON report consumable by ``avo diff`` and external
  dashboard tooling.

Roadmap (not in this MVP):

- Live provider adapters (ollama, anthropic, openai, minimax).
- Task-matrix sweep (``--tasks-file``).
- Cost-normalized scoring.
- Regression detection against a stored baseline.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avo.exceptions import AvoError
from avo.models import ModelResponse, TokenUsage
from avo.providers.base import ModelProvider
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime


class BenchError(AvoError):
    """User-facing failure from ``avo bench``."""


@dataclass
class TurnRecord:
    """One turn's measurement under :func:`run_benchmark`."""

    turn: int
    latency_ms: float
    input_tokens: int
    output_tokens: int
    steps: int
    stop_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "steps": self.steps,
            "stop_reason": self.stop_reason,
        }


@dataclass
class RouteBenchResult:
    """Benchmark result for a single provider route."""

    route: str
    provider_name: str
    model: str
    success: bool
    latency_ms: float
    ttft_ms: float | None = None
    output: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "provider": self.provider_name,
            "model": self.model,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "output": self.output,
            "error": self.error,
        }


def render_benchmark_table(results: Sequence[RouteBenchResult]) -> str:
    """Render an ASCII/Unicode comparison table ranked by speed."""
    if not results:
        return "No benchmark results to display.\n"

    lines = [
        "╭─ Route Benchmark Ranking ─────────────────────────────────────────────────────────╮",
        (
            f"│ {'Rank':<5} {'Route':<14} {'Provider':<12} {'Model':<20} "
            f"{'TTFT':<9} {'Total':<9} {'Status':<7} │"
        ),
        "├───────────────────────────────────────────────────────────────────────────────────┤",
    ]

    for idx, r in enumerate(results, start=1):
        status_str = "OK" if r.success else "FAIL"
        ttft_str = f"{r.ttft_ms:.1f}ms" if r.ttft_ms is not None else "-"
        total_str = f"{r.latency_ms:.1f}ms"
        model_display = r.model[:18] + ".." if len(r.model) > 20 else r.model
        route_display = r.route[:12] + ".." if len(r.route) > 14 else r.route
        prov_display = r.provider_name[:10] + ".." if len(r.provider_name) > 12 else r.provider_name

        lines.append(
            f"│ {idx:<5} {route_display:<14} {prov_display:<12} {model_display:<20} "
            f"{ttft_str:<9} {total_str:<9} {status_str:<7} │"
        )

    lines.append(
        "╰───────────────────────────────────────────────────────────────────────────────────╯"
    )

    successful = [r for r in results if r.success]
    if len(successful) > 1:
        fastest = successful[0]
        recommended_chain = ",".join(r.route for r in successful)
        lines.append(f"\nFastest route: {fastest.route} ({fastest.latency_ms:.1f}ms)")
        lines.append(f"Recommended chain: AVO_ROUTER_CHAIN={recommended_chain}")

    return "\n".join(lines) + "\n"


async def benchmark_route(
    route_name: str,
    provider: ModelProvider,
    *,
    prompt: str = "Explain recursion in 10 words.",
    timeout_seconds: float = 15.0,
) -> RouteBenchResult:
    """Execute a single-turn latency test against one provider route."""
    from avo.models import ModelRequest
    from avo.providers.streaming import StreamingModelProvider

    prov_name = getattr(provider, "name", route_name)
    prov_model = getattr(provider, "model", "default")
    req = ModelRequest(
        run_id=f"bench-{route_name}-{int(time.time() * 1000)}",
        step=1,
        messages=[{"role": "user", "content": prompt}],
    )

    t0 = time.perf_counter()
    ttft: float | None = None
    output_text = ""
    try:
        if isinstance(provider, StreamingModelProvider):

            async def _stream_read() -> None:
                nonlocal ttft, output_text
                async for chunk in provider.stream(req):
                    if ttft is None and (chunk.text or chunk.thought):
                        ttft = (time.perf_counter() - t0) * 1000
                    output_text += chunk.text

            await asyncio.wait_for(_stream_read(), timeout=timeout_seconds)
        else:
            resp = await asyncio.wait_for(provider.generate(req), timeout=timeout_seconds)
            output_text = resp.content or ""

        total_ms = (time.perf_counter() - t0) * 1000
        if ttft is None:
            ttft = total_ms

        return RouteBenchResult(
            route=route_name,
            provider_name=prov_name,
            model=prov_model,
            success=True,
            latency_ms=round(total_ms, 1),
            ttft_ms=round(ttft, 1) if ttft is not None else None,
            output=output_text.strip(),
        )
    except Exception as exc:
        total_ms = (time.perf_counter() - t0) * 1000
        return RouteBenchResult(
            route=route_name,
            provider_name=prov_name,
            model=prov_model,
            success=False,
            latency_ms=round(total_ms, 1),
            error=str(exc),
        )


async def benchmark_all_routes(
    routes: Sequence[tuple[str, ModelProvider]],
    *,
    prompt: str = "Explain recursion in 10 words.",
    timeout_seconds: float = 15.0,
    concurrent: bool = True,
) -> list[RouteBenchResult]:
    """Benchmark multiple provider routes and return ranked by latency."""
    if concurrent:
        tasks = [
            benchmark_route(name, prov, prompt=prompt, timeout_seconds=timeout_seconds)
            for name, prov in routes
        ]
        results = await asyncio.gather(*tasks)
    else:
        results = []
        for name, prov in routes:
            res = await benchmark_route(
                name,
                prov,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
            )
            results.append(res)

    return sorted(
        results,
        key=lambda r: (0 if r.success else 1, r.latency_ms),
    )


@dataclass
class BenchReport:
    """Aggregate benchmark report; serialises to JSON."""

    provider: str
    model: str
    turns: int
    task: str
    records: list[TurnRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "turns_requested": self.turns,
            "task": self.task,
            "turns_completed": len(self.records),
            "summary": _summary(self.records),
            "turns": [record.as_dict() for record in self.records],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, sort_keys=True)


def _summary(records: Sequence[TurnRecord]) -> dict[str, Any]:
    if not records:
        return {"empty": True}
    latencies = [r.latency_ms for r in records]
    inputs = [r.input_tokens for r in records]
    outputs = [r.output_tokens for r in records]
    return {
        "latency_ms": {
            "min": min(latencies),
            "max": max(latencies),
            "mean": statistics.fmean(latencies),
            "stdev": statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        },
        "tokens": {
            "input_total": sum(inputs),
            "output_total": sum(outputs),
            "input_mean": statistics.fmean(inputs),
            "output_mean": statistics.fmean(outputs),
        },
        "steps_total": sum(r.steps for r in records),
    }


def _script_for_turns(turns: int) -> list[ModelResponse]:
    """Return a deterministic response script that completes ``turns`` runs."""

    if turns <= 0:
        raise BenchError(f"turns must be > 0; got {turns}.")
    return [ModelResponse(content=f"reply-{n}") for n in range(turns)]


async def _one_turn(
    runtime: AgentRuntime,
    *,
    task: str,
    run_id: str,
) -> TurnRecord:
    started = time.perf_counter()
    result = await runtime.run(task, run_id=run_id)
    elapsed = (time.perf_counter() - started) * 1000
    return TurnRecord(
        turn=1,
        latency_ms=elapsed,
        input_tokens=result.token_usage.input_tokens,
        output_tokens=result.token_usage.output_tokens,
        steps=result.steps,
        stop_reason=result.stop_reason.value,
    )


async def run_benchmark(
    *,
    provider: ModelProvider,
    task: str = "Hello, world.",
    turns: int = 1,
) -> BenchReport:
    """Run ``turns`` single-turn agent runs and aggregate metrics."""

    provider_name = getattr(provider, "name", "fake")
    model_name = getattr(provider, "model", "fake")
    report = BenchReport(provider=provider_name, model=model_name, turns=turns, task=task)
    for index in range(turns):
        runtime = AgentRuntime(provider=provider)
        record = await _one_turn(runtime, task=task, run_id=f"bench-{index}")
        report.records.append(record)
    return report


def _scripted_provider(turns: int) -> FakeProvider:
    responses = _script_for_turns(turns)
    return FakeProvider(responses)


def main(argv: Sequence[str] | None = None) -> int:
    """``avo bench`` entry point. Parses argv and emits report to stdout."""
    import os

    args = list(argv if argv is not None else sys.argv[1:])
    turns = 1
    task = "Hello, world."
    output: Path | None = None
    live = False
    requested_routes: list[str] | None = None

    while args:
        head = args[0]
        if head in {"--turns", "-n"} and len(args) > 1:
            turns = int(args[1])
            args = args[2:]
            continue
        if head == "--task" and len(args) > 1:
            task = args[1]
            args = args[2:]
            continue
        if head in {"--output", "-o"} and len(args) > 1:
            output = Path(args[1])
            args = args[2:]
            continue
        if head == "--live":
            live = True
            args = args[1:]
            continue
        if head == "--routes" and len(args) > 1:
            live = True
            requested_routes = [r.strip() for r in args[1].split(",") if r.strip()]
            args = args[2:]
            continue
        if head in {"--help", "-h"}:
            print(
                "Usage: avo bench [--live] [--routes R1,R2] [--turns N] "
                "[--task TEXT] [--output FILE]\n\n"
                "Run a benchmark against providers or deterministic FakeProvider.\n"
                "  --live              Benchmark active configured provider from environment\n"
                "  --routes R1,R2      Benchmark and rank specific multi-provider routes\n"
                "  --turns N           Number of benchmark turns to run (default: 1)\n"
                "  --task TEXT         Prompt/task text to benchmark\n"
                "  --output FILE       Save JSON report to file\n"
            )
            return 0
        raise BenchError(f"Unknown argument: {head!r}")

    if turns <= 0:
        raise BenchError("--turns must be > 0")

    if live:
        from avo.config import build_provider_from_env
        from avo.providers.router import BaseRouterProvider

        environ = dict(os.environ)
        if requested_routes:
            environ["AVO_PROVIDER"] = "router"
            environ["AVO_ROUTER_CHAIN"] = ",".join(requested_routes)

        prov = build_provider_from_env(environ)
        if isinstance(prov, BaseRouterProvider):
            results = asyncio.run(benchmark_all_routes(prov.routes, prompt=task))
            table_text = render_benchmark_table(results)
            sys.stdout.write(table_text)
            if output is not None:
                json_payload = json.dumps([r.as_dict() for r in results], indent=2)
                output.write_text(json_payload + "\n", encoding="utf-8")
            return 0

        # Single live provider
        report = asyncio.run(run_benchmark(provider=prov, task=task, turns=turns))
    else:
        report = asyncio.run(
            run_benchmark(provider=_scripted_provider(turns), task=task, turns=turns)
        )

    text = report.to_json()
    if output is None:
        sys.stdout.write(text + "\n")
    else:
        output.write_text(text + "\n", encoding="utf-8")
    return 0


__all__ = [
    "BenchError",
    "BenchReport",
    "RouteBenchResult",
    "TurnRecord",
    "benchmark_all_routes",
    "benchmark_route",
    "main",
    "render_benchmark_table",
    "run_benchmark",
]


def _ensure_token_usage_typed() -> TokenUsage:
    """Type-hint anchor for the ``TokenUsage`` import above."""

    return TokenUsage()
