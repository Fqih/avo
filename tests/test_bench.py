"""Tests for ``avo.bench``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from avo.bench import (
    BenchError,
    BenchReport,
    RouteBenchResult,
    TurnRecord,
    _script_for_turns,
    benchmark_all_routes,
    benchmark_route,
    render_benchmark_table,
    run_benchmark,
)
from avo.bench import (
    main as bench_main,
)
from avo.models import ModelResponse
from avo.providers.fake import FakeProvider


def test_script_for_turns_rejects_zero() -> None:
    with pytest.raises(BenchError, match="turns must be > 0"):
        _script_for_turns(0)


def test_script_for_turns_returns_n_responses() -> None:
    responses = _script_for_turns(3)
    assert len(responses) == 3
    assert all(r.content for r in responses)


@pytest.mark.asyncio
async def test_run_benchmark_records_turns() -> None:
    provider = FakeProvider([ModelResponse(content="hi"), ModelResponse(content="hey")])
    report = await run_benchmark(provider=provider, task="say hi", turns=2)
    assert isinstance(report, BenchReport)
    assert report.turns == 2
    assert len(report.records) == 2
    assert all(isinstance(r, TurnRecord) for r in report.records)
    assert all(r.latency_ms >= 0 for r in report.records)


def test_turn_record_as_dict_has_expected_keys() -> None:
    record = TurnRecord(
        turn=1, latency_ms=1.5, input_tokens=10, output_tokens=5, steps=1, stop_reason="completed"
    )
    payload = record.as_dict()
    assert payload["turn"] == 1
    assert payload["latency_ms"] == 1.5
    assert payload["stop_reason"] == "completed"


def test_bench_report_to_json_is_valid_json() -> None:
    report = BenchReport(
        provider="fake",
        model="m",
        turns=1,
        task="t",
        records=[
            TurnRecord(
                turn=1,
                latency_ms=2.0,
                input_tokens=3,
                output_tokens=4,
                steps=1,
                stop_reason="completed",
            )
        ],
    )
    parsed = json.loads(report.to_json())
    assert parsed["provider"] == "fake"
    assert parsed["turns_completed"] == 1
    assert "summary" in parsed
    assert parsed["summary"]["tokens"]["input_total"] == 3


def test_bench_report_summary_empty() -> None:
    report = BenchReport(provider="p", model="m", turns=0, task="t")
    payload = report.to_json()
    parsed = json.loads(payload)
    assert parsed["summary"]["empty"] is True


def test_main_emits_json_to_stdout() -> None:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = bench_main(["--turns", "1"])
    assert code == 0
    parsed = json.loads(buffer.getvalue())
    assert parsed["turns_requested"] == 1


def test_main_writes_to_output_file(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    code = bench_main(["--turns", "1", "--output", str(target)])
    assert code == 0
    assert target.is_file()
    parsed = json.loads(target.read_text())
    assert parsed["turns_requested"] == 1


def test_main_rejects_zero_turns() -> None:
    with pytest.raises(BenchError):
        bench_main(["--turns", "0"])


def test_main_rejects_unknown_arg() -> None:
    with pytest.raises(BenchError, match="Unknown argument"):
        bench_main(["--bogus"])


def test_main_help_returns_zero() -> None:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = bench_main(["--help"])
    assert code == 0
    assert "Usage:" in buffer.getvalue()


@pytest.mark.asyncio
async def test_benchmark_route_success() -> None:
    provider = FakeProvider([ModelResponse(content="fast hello")])
    res = await benchmark_route("ollama", provider, prompt="hi")
    assert isinstance(res, RouteBenchResult)
    assert res.route == "ollama"
    assert res.success is True
    assert res.latency_ms >= 0
    assert res.output == "fast hello"
    assert res.error is None


@pytest.mark.asyncio
async def test_benchmark_route_failure() -> None:
    class BrokenProvider:
        async def generate(self, req: object) -> object:
            raise ConnectionRefusedError("Connection refused to port 11434")

    res = await benchmark_route("broken", BrokenProvider())  # type: ignore[arg-type]
    assert res.route == "broken"
    assert res.success is False
    assert "Connection refused" in (res.error or "")


@pytest.mark.asyncio
async def test_benchmark_all_routes_ranks_by_speed() -> None:
    import asyncio

    class SlowProvider:
        async def generate(self, req: object) -> object:
            await asyncio.sleep(0.05)
            return ModelResponse(content="slow")

    class FastProvider:
        async def generate(self, req: object) -> object:
            await asyncio.sleep(0.01)
            return ModelResponse(content="fast")

    routes = [("slow", SlowProvider()), ("fast", FastProvider())]  # type: ignore[list-item]
    results = await benchmark_all_routes(routes, prompt="test")
    assert len(results) == 2
    assert results[0].route == "fast"
    assert results[1].route == "slow"
    assert results[0].latency_ms < results[1].latency_ms


def test_render_benchmark_table() -> None:
    results = [
        RouteBenchResult(
            route="ollama",
            provider_name="ollama",
            model="llama3.2",
            success=True,
            latency_ms=12.5,
            ttft_ms=10.0,
            output="ok",
        ),
        RouteBenchResult(
            route="openrouter",
            provider_name="openrouter",
            model="meta-llama/llama-3.3-70b-instruct:free",
            success=True,
            latency_ms=85.2,
            ttft_ms=50.1,
            output="ok",
        ),
    ]
    table = render_benchmark_table(results)
    assert "Route Benchmark Ranking" in table
    assert "ollama" in table
    assert "openrouter" in table
    assert "Fastest route: ollama" in table
    assert "AVO_ROUTER_CHAIN=ollama,openrouter" in table


def test_bench_main_live_router(monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.providers.router import FallbackRouterProvider

    p1 = FakeProvider([ModelResponse(content="p1 reply")])
    p2 = FakeProvider([ModelResponse(content="p2 reply")])
    mock_router = FallbackRouterProvider([("p1", p1), ("p2", p2)])  # type: ignore[list-item]

    monkeypatch.setattr("avo.config.build_provider_from_env", lambda env: mock_router)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = bench_main(["--live", "--task", "ping test"])
    assert code == 0
    out = buffer.getvalue()
    assert "Route Benchmark Ranking" in out
    assert "p1" in out
    assert "p2" in out


def test_chat_repl_bench_slash_command(tmp_path: Path) -> None:
    from avo.chat import _run_slash, build_chat_context
    from avo.providers.router import FallbackRouterProvider

    p1 = FakeProvider([ModelResponse(content="r1 answer")])
    mock_router = FallbackRouterProvider([("ollama", p1)])  # type: ignore[list-item]

    db_path = tmp_path / "chat.db"
    environ = {
        "AVO_PROVIDER": "router",
        "AVO_ROUTER_CHAIN": "ollama",
        "AVO_OLLAMA_MODEL": "llama3.2",
    }
    ctx = build_chat_context(
        database_path=db_path,
        workspace_root=tmp_path,
        environ=environ,
    )
    ctx.runtime.provider = mock_router

    out = io.StringIO()
    err = io.StringIO()

    import asyncio

    exited = asyncio.run(_run_slash(ctx, ["/bench", "quick latency check"], out, err, environ))
    assert exited is False
    assert "Benchmarking with prompt" in out.getvalue()
    assert "Route Benchmark Ranking" in out.getvalue()
    assert "ollama" in out.getvalue()
