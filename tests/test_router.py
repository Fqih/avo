"""Tests for FallbackRouterProvider."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from avo import ModelRequest, ModelResponse
from avo.config import build_provider_from_env
from avo.exceptions import ProviderError
from avo.providers.router import FallbackRouterProvider
from avo.providers.streaming import ModelChunk


class _MockProvider:
    def __init__(
        self,
        name: str,
        response: ModelResponse | None = None,
        error: Exception | None = None,
        chunks: list[str] | None = None,
        stream_error_before: Exception | None = None,
    ) -> None:
        self.name = name
        self.model = f"mock-{name}"
        self.response = response
        self.error = error
        self.chunks = chunks
        self.stream_error_before = stream_error_before
        self.generate_called = 0
        self.closed = False

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.generate_called += 1
        if self.error:
            raise self.error
        return self.response or ModelResponse(content=f"from {self.name}")

    async def stream(self, request: ModelRequest) -> Any:
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
    }
    provider = build_provider_from_env(env)
    assert isinstance(provider, FallbackRouterProvider)
    assert len(provider.routes) == 1
    assert provider.routes[0][0] == "ollama"
