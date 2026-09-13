"""Fallback and load-balancing multi-provider router.

Enables resilience across multiple model providers (e.g. prioritizing a free
local Ollama instance and falling back to OpenRouter, Groq, or Anthropic when
the local provider is unreachable, overloaded, or rate-limited). Includes
circuit-breaker cooldowns, active health probing, and latency monitoring.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from avo import ModelRequest, ModelResponse
from avo.exceptions import ProviderError
from avo.providers.base import ModelProvider
from avo.providers.streaming import ModelChunk, StreamingModelProvider

_LOG = logging.getLogger("avo.providers.router")

FallbackNotifier = Callable[[str, str, Exception], None]


@dataclass
class RouteHealth:
    """Live health and circuit-breaker status for a single routed provider."""

    name: str
    is_healthy: bool = True
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: str | None = None
    last_latency_ms: float | None = None

    @property
    def is_cooling_down(self) -> bool:
        """Return True if this route is currently cooling down from a recent failure."""
        return time.monotonic() < self.cooldown_until

    def remaining_cooldown(self) -> float:
        """Return remaining cooldown seconds (0.0 if not cooling down)."""
        return max(0.0, self.cooldown_until - time.monotonic())


class FallbackRouterProvider:
    """An async provider that queries a chain of providers in priority order.

    If a provider fails (network disconnect, rate limit, HTTP 5xx, timeout, etc.),
    it is temporarily placed in circuit-breaker cooldown and the router falls back
    to the next functional provider in the chain until a response succeeds or all
    providers are exhausted.
    """

    name = "router"

    def __init__(
        self,
        routes: Sequence[tuple[str, ModelProvider]],
        *,
        on_fallback: FallbackNotifier | None = None,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if not routes:
            raise ValueError("FallbackRouterProvider requires at least one route")
        self._routes = list(routes)
        self._on_fallback = on_fallback
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._health: dict[str, RouteHealth] = {
            name: RouteHealth(name=name) for name, _ in self._routes
        }
        self.model = f"router({','.join(name for name, _ in self._routes)})"

    @property
    def routes(self) -> list[tuple[str, ModelProvider]]:
        """Return the ordered list of (name, provider) routes."""
        return list(self._routes)

    @property
    def cooldown_seconds(self) -> float:
        """Return the configured circuit-breaker cooldown duration in seconds."""
        return self._cooldown_seconds

    def get_health_status(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of current health and cooldown status for all routes."""
        now = time.monotonic()
        status: dict[str, dict[str, Any]] = {}
        for name, _ in self._routes:
            h = self._health[name]
            is_cooling = now < h.cooldown_until
            status[name] = {
                "healthy": h.is_healthy and not is_cooling,
                "in_cooldown": is_cooling,
                "cooldown_remaining_seconds": round(max(0.0, h.cooldown_until - now), 1),
                "consecutive_failures": h.consecutive_failures,
                "last_error": h.last_error,
                "last_latency_ms": round(h.last_latency_ms, 2)
                if h.last_latency_ms is not None
                else None,
            }
        return status

    def reset_health(self, name: str | None = None) -> None:
        """Reset cooldown and failure counters for one or all routes."""
        if name is not None:
            if name in self._health:
                self._health[name] = RouteHealth(name=name)
        else:
            for r_name in self._health:
                self._health[r_name] = RouteHealth(name=r_name)

    def _select_candidate_routes(self) -> list[tuple[str, ModelProvider]]:
        """Return ordered routes to attempt, prioritizing active non-cooldown routes."""
        if self._cooldown_seconds <= 0:
            return list(self._routes)

        active = [r for r in self._routes if not self._health[r[0]].is_cooling_down]
        if active:
            return active

        # All routes are in cooldown; try all routes as safety net
        _LOG.info("all router routes are in cooldown; attempting emergency retry on primary chain")
        return list(self._routes)

    def _record_success(self, name: str, start_time: float) -> None:
        """Update route health metrics on successful execution."""
        latency = (time.monotonic() - start_time) * 1000.0
        h = self._health[name]
        h.is_healthy = True
        h.consecutive_failures = 0
        h.cooldown_until = 0.0
        h.last_error = None
        h.last_latency_ms = latency

    def _record_failure(self, name: str, exc: Exception) -> None:
        """Update route health metrics on provider failure and enter cooldown."""
        h = self._health[name]
        h.is_healthy = False
        h.consecutive_failures += 1
        if self._cooldown_seconds > 0:
            h.cooldown_until = time.monotonic() + self._cooldown_seconds
        h.last_error = str(exc)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Attempt generate on candidate routes sequentially until one succeeds."""
        candidates = self._select_candidate_routes()
        errors: list[str] = []

        for i, (name, provider) in enumerate(candidates):
            t0 = time.monotonic()
            try:
                response = await provider.generate(request)
                self._record_success(name, t0)
                if i > 0 or name != self._routes[0][0]:
                    _LOG.info("route %r successfully resolved after fallback", name)
                return response
            except Exception as exc:
                self._record_failure(name, exc)
                errors.append(f"[{name}] {exc!s}")
                _LOG.warning("provider %r failed (%s); checking next route", name, exc)
                if self._on_fallback is not None and i + 1 < len(candidates):
                    next_name = candidates[i + 1][0]
                    self._on_fallback(name, next_name, exc)

        raise ProviderError(f"all routed providers failed: {'; '.join(errors)}")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Stream chunks from the highest-priority functional provider."""
        candidates = self._select_candidate_routes()
        errors: list[str] = []

        for i, (name, provider) in enumerate(candidates):
            yielded_any = False
            t0 = time.monotonic()
            try:
                if isinstance(provider, StreamingModelProvider):
                    async for chunk in provider.stream(request):
                        yielded_any = True
                        yield chunk
                    self._record_success(name, t0)
                    return
                # Provider does not implement streaming; fallback to generate
                resp = await provider.generate(request)
                self._record_success(name, t0)
                yield ModelChunk(text=resp.content or "")
                return
            except Exception as exc:
                self._record_failure(name, exc)
                errors.append(f"[{name}] {exc!s}")
                _LOG.warning("streaming from provider %r failed (%s)", name, exc)
                if yielded_any:
                    # Chunks were already delivered to caller; cannot safely restart stream
                    raise ProviderError(
                        f"streaming error on {name} after partial output: {exc!s}"
                    ) from exc
                if self._on_fallback is not None and i + 1 < len(candidates):
                    next_name = candidates[i + 1][0]
                    self._on_fallback(name, next_name, exc)

        raise ProviderError(f"all routed providers failed: {'; '.join(errors)}")

    async def probe_route(self, name: str, *, timeout_seconds: float = 3.0) -> bool:
        """Probe an individual route for health."""
        provider = next((p for n, p in self._routes if n == name), None)
        if provider is None:
            return False

        probe_fn = getattr(provider, "probe_health", None) or getattr(provider, "ping", None)
        if callable(probe_fn):
            try:
                res = await asyncio.wait_for(probe_fn(), timeout=timeout_seconds)
                is_ok = bool(res)
                if is_ok:
                    self.reset_health(name)
                return is_ok
            except Exception:
                return False

        h = self._health.get(name)
        return bool(h and not h.is_cooling_down)

    async def probe_all(self, *, timeout_seconds: float = 3.0) -> dict[str, bool]:
        """Probe all routes concurrently and return health status."""
        results: dict[str, bool] = {}
        tasks = [
            self.probe_route(name, timeout_seconds=timeout_seconds) for name, _ in self._routes
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for (name, _), outcome in zip(self._routes, outcomes, strict=True):
            results[name] = outcome is True
        return results

    async def aclose(self) -> None:
        """Close client sessions for all underlying providers."""
        for _, provider in self._routes:
            aclose_fn = getattr(provider, "aclose", None)
            if callable(aclose_fn):
                try:
                    await aclose_fn()
                except Exception as exc:  # pragma: no cover
                    _LOG.debug("error closing provider %r: %s", provider, exc)


__all__ = ["FallbackNotifier", "FallbackRouterProvider", "RouteHealth"]
