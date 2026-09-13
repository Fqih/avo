"""Tests for FallbackRouterProvider."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from avo import ModelRequest, ModelResponse
from avo.config import build_provider_from_env
from avo.exceptions import ProviderError
from avo.providers.router import FallbackRouterProvider, RaceRouterProvider
from avo.providers.streaming import ModelChunk


class _MockProvider:
    def __init__(
        self,
        name: str,
        response: ModelResponse | None = None,
        error: Exception | None = None,
        chunks: list[str] | None = None,
        stream_error_before: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self.model = f"mock-{name}"
        self.response = response
        self.error = error
        self.chunks = chunks
        self.stream_error_before = stream_error_before
        self.delay = delay
        self.generate_called = 0
        self.closed = False

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.generate_called += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response or ModelResponse(content=f"from {self.name}")

    async def stream(self, request: ModelRequest) -> Any:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.stream_error_before:
            raise self.stream_error_before
        for chunk in self.chunks or []:
            yield ModelChunk(text=chunk)

    async def aclose(self) -> None:
        self.closed = True


def test_router_requires_routes() -> None:
    with pytest.raises(ValueError, match="requires at least one route"):
        FallbackRouterProvider([])


def test_router_resolves_primary_when_healthy() -> None:
    p1 = _MockProvider("ollama", response=ModelResponse(content="local fast response"))
    p2 = _MockProvider("openrouter", response=ModelResponse(content="cloud response"))

    router = FallbackRouterProvider([("ollama", p1), ("openrouter", p2)])  # type: ignore[list-item]
    req = ModelRequest(run_id="r1", step=1, messages=[])

    resp = asyncio.run(router.generate(req))
    assert resp.content == "local fast response"
    assert p1.generate_called == 1
    assert p2.generate_called == 0


def test_router_falls_back_when_primary_fails() -> None:
    fallback_events: list[tuple[str, str, Exception]] = []

    def on_fallback(from_name: str, to_name: str, exc: Exception) -> None:
        fallback_events.append((from_name, to_name, exc))

    p1 = _MockProvider("ollama", error=ConnectionRefusedError("Ollama daemon down"))
    p2 = _MockProvider("openrouter", response=ModelResponse(content="cloud fallback response"))

    router = FallbackRouterProvider(
        [("ollama", p1), ("openrouter", p2)],  # type: ignore[list-item]
        on_fallback=on_fallback,
    )
    req = ModelRequest(run_id="r2", step=1, messages=[])

    resp = asyncio.run(router.generate(req))
    assert resp.content == "cloud fallback response"
    assert p1.generate_called == 1
    assert p2.generate_called == 1
    assert len(fallback_events) == 1
    assert fallback_events[0][0] == "ollama"
    assert fallback_events[0][1] == "openrouter"
    assert isinstance(fallback_events[0][2], ConnectionRefusedError)


def test_router_raises_when_all_routes_fail() -> None:
    p1 = _MockProvider("ollama", error=ConnectionRefusedError("no daemon"))
    p2 = _MockProvider("openrouter", error=ProviderError("rate limited"))

    router = FallbackRouterProvider([("ollama", p1), ("openrouter", p2)])  # type: ignore[list-item]
    req = ModelRequest(run_id="r3", step=1, messages=[])

    with pytest.raises(ProviderError, match="all routed providers failed: \\[ollama\\]"):
        asyncio.run(router.generate(req))


def test_router_streaming_primary_success() -> None:
    p1 = _MockProvider("ollama", chunks=["hello ", "world"])
    p2 = _MockProvider("openrouter", chunks=["cloud"])

    router = FallbackRouterProvider([("ollama", p1), ("openrouter", p2)])  # type: ignore[list-item]
    req = ModelRequest(run_id="r4", step=1, messages=[])

    async def run_stream() -> list[str]:
        out = []
        async for chunk in router.stream(req):
            if chunk.text:
                out.append(chunk.text)
        return out

    assert asyncio.run(run_stream()) == ["hello ", "world"]


def test_router_streaming_fallback_when_primary_fails_immediately() -> None:
    fallback_log: list[str] = []

    def on_fallback(f: str, t: str, e: Exception) -> None:
        fallback_log.append(f"{f}->{t}")

    p1 = _MockProvider("ollama", stream_error_before=ConnectionResetError("dropped"))
    p2 = _MockProvider("openrouter", chunks=["fallback ", "stream"])

    router = FallbackRouterProvider(
        [("ollama", p1), ("openrouter", p2)],  # type: ignore[list-item]
        on_fallback=on_fallback,
    )
    req = ModelRequest(run_id="r5", step=1, messages=[])

    async def run_stream() -> list[str]:
        out = []
        async for chunk in router.stream(req):
            if chunk.text:
                out.append(chunk.text)
        return out

    assert asyncio.run(run_stream()) == ["fallback ", "stream"]
    assert fallback_log == ["ollama->openrouter"]


def test_router_aclose_calls_underlying_providers() -> None:
    p1 = _MockProvider("p1")
    p2 = _MockProvider("p2")
    router = FallbackRouterProvider([("p1", p1), ("p2", p2)])  # type: ignore[list-item]

    asyncio.run(router.aclose())
    assert p1.closed
    assert p2.closed


def test_router_in_build_provider_from_env() -> None:
    env = {
        "AVO_PROVIDER": "router",
        "AVO_ROUTER_CHAIN": "ollama",
        "AVO_OLLAMA_MODEL": "llama3.2",
        "AVO_ROUTER_COOLDOWN_SECONDS": "45.0",
    }
    provider = build_provider_from_env(env)
    assert isinstance(provider, FallbackRouterProvider)
    assert len(provider.routes) == 1
    assert provider.routes[0][0] == "ollama"
    assert provider.cooldown_seconds == 45.0


def test_router_circuit_breaker_skips_cooling_route() -> None:
    p1 = _MockProvider("ollama", error=ConnectionRefusedError("offline"))
    p2 = _MockProvider("openrouter", response=ModelResponse(content="from openrouter"))

    router = FallbackRouterProvider(
        [("ollama", p1), ("openrouter", p2)],  # type: ignore[list-item]
        cooldown_seconds=60.0,
    )
    req = ModelRequest(run_id="r-cb", step=1, messages=[])

    # First request: p1 fails, enters cooldown, falls back to p2
    resp1 = asyncio.run(router.generate(req))
    assert resp1.content == "from openrouter"
    assert p1.generate_called == 1
    assert p2.generate_called == 1

    # Second request: p1 is in cooldown, router skips p1 immediately to p2
    resp2 = asyncio.run(router.generate(req))
    assert resp2.content == "from openrouter"
    assert p1.generate_called == 1  # p1 was NOT called again
    assert p2.generate_called == 2

    # Verify health status
    status = router.get_health_status()
    assert status["ollama"]["in_cooldown"] is True
    assert status["ollama"]["healthy"] is False
    assert status["ollama"]["consecutive_failures"] == 1
    assert "offline" in status["ollama"]["last_error"]
    assert status["openrouter"]["healthy"] is True
    assert status["openrouter"]["in_cooldown"] is False


def test_router_reset_health() -> None:
    p1 = _MockProvider("ollama", error=ConnectionRefusedError("offline"))
    p2 = _MockProvider("openrouter", response=ModelResponse(content="ok"))

    router = FallbackRouterProvider(
        [("ollama", p1), ("openrouter", p2)],  # type: ignore[list-item]
        cooldown_seconds=60.0,
    )
    req = ModelRequest(run_id="r-reset", step=1, messages=[])
    asyncio.run(router.generate(req))

    assert router.get_health_status()["ollama"]["in_cooldown"] is True
    router.reset_health("ollama")
    assert router.get_health_status()["ollama"]["in_cooldown"] is False
    assert router.get_health_status()["ollama"]["healthy"] is True


def test_router_safety_fallback_when_all_in_cooldown() -> None:
    p1 = _MockProvider("ollama", error=ConnectionRefusedError("err1"))
    p2 = _MockProvider("openrouter", error=ProviderError("err2"))

    router = FallbackRouterProvider(
        [("ollama", p1), ("openrouter", p2)],  # type: ignore[list-item]
        cooldown_seconds=60.0,
    )
    req = ModelRequest(run_id="r-all-cool", step=1, messages=[])

    # First call puts both in cooldown
    with pytest.raises(ProviderError):
        asyncio.run(router.generate(req))

    assert router.get_health_status()["ollama"]["in_cooldown"] is True
    assert router.get_health_status()["openrouter"]["in_cooldown"] is True

    # Next call: even though all are in cooldown, safety net tries routes instead of failing blindly
    with pytest.raises(ProviderError):
        asyncio.run(router.generate(req))

    assert p1.generate_called == 2
    assert p2.generate_called == 2


def test_router_probe_all() -> None:
    p1 = _MockProvider("p1")
    p2 = _MockProvider("p2")
    router = FallbackRouterProvider([("p1", p1), ("p2", p2)])  # type: ignore[list-item]

    probes = asyncio.run(router.probe_all(timeout_seconds=1.0))
    assert probes == {"p1": True, "p2": True}


def test_race_router_requires_routes() -> None:
    with pytest.raises(ValueError, match="requires at least one route"):
        RaceRouterProvider([])


def test_race_router_returns_fastest() -> None:
    p_slow = _MockProvider(
        "slow-cloud",
        response=ModelResponse(content="slow response"),
        delay=0.1,
    )
    p_fast = _MockProvider(
        "fast-local",
        response=ModelResponse(content="fast response"),
        delay=0.01,
    )

    router = RaceRouterProvider(
        [("slow-cloud", p_slow), ("fast-local", p_fast)],  # type: ignore[list-item]
    )
    req = ModelRequest(run_id="r-race-1", step=1, messages=[])

    resp = asyncio.run(router.generate(req))
    assert resp.content == "fast response"
    status = router.get_health_status()
    assert status["fast-local"]["healthy"] is True


def test_race_router_ignores_failed_candidate() -> None:
    p_failing_fast = _MockProvider(
        "broken-fast",
        error=ConnectionRefusedError("fast connection refused"),
        delay=0.005,
    )
    p_ok_slower = _MockProvider(
        "ok-slower",
        response=ModelResponse(content="slower but valid"),
        delay=0.03,
    )

    router = RaceRouterProvider(
        [("broken-fast", p_failing_fast), ("ok-slower", p_ok_slower)],  # type: ignore[list-item]
    )
    req = ModelRequest(run_id="r-race-2", step=1, messages=[])

    resp = asyncio.run(router.generate(req))
    assert resp.content == "slower but valid"
    status = router.get_health_status()
    assert status["broken-fast"]["healthy"] is False
    assert status["broken-fast"]["in_cooldown"] is True
    assert status["ok-slower"]["healthy"] is True


def test_race_router_raises_when_all_fail() -> None:
    p1 = _MockProvider("p1", error=ConnectionRefusedError("err1"), delay=0.01)
    p2 = _MockProvider("p2", error=ProviderError("err2"), delay=0.01)

    router = RaceRouterProvider([("p1", p1), ("p2", p2)])  # type: ignore[list-item]
    req = ModelRequest(run_id="r-race-fail", step=1, messages=[])

    with pytest.raises(ProviderError, match="all raced providers failed:"):
        asyncio.run(router.generate(req))


def test_race_router_streaming_returns_fastest_chunk() -> None:
    p_slow = _MockProvider("slow", chunks=["slow chunk"], delay=0.1)
    p_fast = _MockProvider("fast", chunks=["fast 1", "fast 2"], delay=0.01)

    router = RaceRouterProvider([("slow", p_slow), ("fast", p_fast)])  # type: ignore[list-item]
    req = ModelRequest(run_id="r-race-stream", step=1, messages=[])

    async def run_stream() -> list[str]:
        out = []
        async for chunk in router.stream(req):
            if chunk.text:
                out.append(chunk.text)
        return out

    result = asyncio.run(run_stream())
    assert result == ["fast 1", "fast 2"]


def test_race_router_strategy_in_build_provider_from_env() -> None:
    env = {
        "AVO_PROVIDER": "router",
        "AVO_ROUTER_STRATEGY": "race",
        "AVO_ROUTER_CHAIN": "ollama",
        "AVO_OLLAMA_MODEL": "llama3.2",
    }
    provider = build_provider_from_env(env)
    assert isinstance(provider, RaceRouterProvider)
    assert provider.strategy == "race"
