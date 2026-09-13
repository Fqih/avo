"""Fallback and load-balancing multi-provider router.

Enables resilience and high performance across multiple model providers (e.g.
prioritizing a free local Ollama instance and falling back to OpenRouter, Groq,
or Anthropic; or racing them concurrently for fastest time-to-first-token).
Includes circuit-breaker cooldowns, active health probing, and latency metrics.
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


class BaseRouterProvider:
    """Base router managing route catalog, health tracking, and circuit breaker."""

    name = "router"
    strategy: str = "base"

    def __init__(
        self,
        routes: Sequence[tuple[str, ModelProvider]],
        *,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if not routes:
            raise ValueError(f"{self.__class__.__name__} requires at least one route")
        self._routes = list(routes)
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
        """Return routes to attempt, prioritizing active non-cooldown routes."""
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


class FallbackRouterProvider(BaseRouterProvider):
    """An async provider that queries a chain of providers in priority order.

    If a provider fails (network disconnect, rate limit, HTTP 5xx, timeout, etc.),
    it is temporarily placed in circuit-breaker cooldown and the router falls back
    to the next functional provider in the chain until a response succeeds or all
    providers are exhausted.
    """

    strategy: str = "fallback"

    def __init__(
        self,
        routes: Sequence[tuple[str, ModelProvider]],
        *,
        on_fallback: FallbackNotifier | None = None,
        cooldown_seconds: float = 30.0,
    ) -> None:
        super().__init__(routes, cooldown_seconds=cooldown_seconds)
        self._on_fallback = on_fallback

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
                    raise ProviderError(
                        f"streaming error on {name} after partial output: {exc!s}"
                    ) from exc
                if self._on_fallback is not None and i + 1 < len(candidates):
                    next_name = candidates[i + 1][0]
                    self._on_fallback(name, next_name, exc)

        raise ProviderError(f"all routed providers failed: {'; '.join(errors)}")


class RaceRouterProvider(BaseRouterProvider):
    """An async provider that queries multiple providers concurrently in parallel.

    Returns the fastest successful response or first chunk from whichever provider
    replies first, then cancels the remaining requests to maximize speed.
    Supports speculative delay racing (hedged requests) via ``speculative_delay_seconds``.
    """

    strategy: str = "race"

    def __init__(
        self,
        routes: Sequence[tuple[str, ModelProvider]],
        *,
        cooldown_seconds: float = 30.0,
        speculative_delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(routes, cooldown_seconds=cooldown_seconds)
        self._speculative_delay_seconds = max(0.0, speculative_delay_seconds)

    @property
    def speculative_delay_seconds(self) -> float:
        return self._speculative_delay_seconds

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Query candidate routes in parallel and return the first successful response."""
        candidates = self._select_candidate_routes()
        if not candidates:
            raise ProviderError("no routes configured for race router")

        if len(candidates) == 1:
            name, provider = candidates[0]
            t0 = time.monotonic()
            try:
                resp = await provider.generate(request)
                self._record_success(name, t0)
                return resp
            except Exception as exc:
                self._record_failure(name, exc)
                raise ProviderError(f"race provider {name!r} failed: {exc}") from exc

        async def _delayed_generate(
            r_name: str,
            r_prov: ModelProvider,
            delay: float,
        ) -> tuple[str, float, ModelResponse]:
            if delay > 0:
                await asyncio.sleep(delay)
            t_start = time.monotonic()
            resp = await r_prov.generate(request)
            return (r_name, t_start, resp)

        tasks: set[asyncio.Task[tuple[str, float, ModelResponse]]] = set()
        for idx, (name, provider) in enumerate(candidates):
            delay = self._speculative_delay_seconds * idx
            task = asyncio.create_task(
                _delayed_generate(name, provider, delay),
                name=f"race-{name}",
            )
            tasks.add(task)

        errors: list[str] = []
        pending: set[asyncio.Task[tuple[str, float, ModelResponse]]] = set(tasks)

        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for completed_task in done:
                    try:
                        name, start_time, resp = completed_task.result()
                        self._record_success(name, start_time)
                        _LOG.info(
                            "race won by provider %r in %.1fms",
                            name,
                            (time.monotonic() - start_time) * 1000,
                        )
                        for other_task in pending:
                            other_task.cancel()
                        return resp
                    except asyncio.CancelledError:
                        continue
                    except Exception as exc:
                        task_name = completed_task.get_name()
                        clean_name = task_name.replace("race-", "")
                        self._record_failure(clean_name, exc)
                        errors.append(f"[{clean_name}] {exc!s}")
                        _LOG.warning("race candidate %r failed: %s", clean_name, exc)
        finally:
            for t in pending:
                t.cancel()

        raise ProviderError(f"all raced providers failed: {'; '.join(errors)}")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Stream chunks from whichever provider emits the first token fastest."""
        candidates = self._select_candidate_routes()
        if not candidates:
            raise ProviderError("no routes configured for race router")

        if len(candidates) == 1:
            name, provider = candidates[0]
            t0 = time.monotonic()
            try:
                if isinstance(provider, StreamingModelProvider):
                    async for chunk in provider.stream(request):
                        yield chunk
                else:
                    resp = await provider.generate(request)
                    yield ModelChunk(text=resp.content or "")
                self._record_success(name, t0)
                return
            except Exception as exc:
                self._record_failure(name, exc)
                raise ProviderError(f"race provider {name!r} failed: {exc}") from exc

        queue: asyncio.Queue[tuple[str, ModelChunk | None, Any, Exception | None]] = asyncio.Queue()

        async def _worker(r_name: str, r_prov: ModelProvider, delay: float) -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                if isinstance(r_prov, StreamingModelProvider):
                    it = r_prov.stream(request).__aiter__()
                    first = await it.__anext__()
                    await queue.put((r_name, first, it, None))
                else:
                    r_resp = await r_prov.generate(request)
                    await queue.put((r_name, ModelChunk(text=r_resp.content or ""), None, None))
            except StopAsyncIteration:
                await queue.put((r_name, ModelChunk(text=""), None, None))
            except asyncio.CancelledError:
                raise
            except Exception as worker_exc:
                await queue.put((r_name, None, None, worker_exc))

        tasks = [
            asyncio.create_task(
                _worker(name, prov, self._speculative_delay_seconds * idx),
                name=f"race-stream-{name}",
            )
            for idx, (name, prov) in enumerate(candidates)
        ]

        winner_name: str | None = None
        winner_iter: Any = None
        first_chunk: ModelChunk | None = None
        errors: list[str] = []
        t0 = time.monotonic()

        try:
            completed_workers = 0
            while completed_workers < len(candidates):
                res_name, res_chunk, res_iter, res_err = await queue.get()
                completed_workers += 1
                if res_err is not None:
                    self._record_failure(res_name, res_err)
                    errors.append(f"[{res_name}] {res_err!s}")
                    _LOG.warning("streaming race candidate %r failed: %s", res_name, res_err)
                else:
                    winner_name = res_name
                    first_chunk = res_chunk
                    winner_iter = res_iter
                    self._record_success(res_name, t0)
                    _LOG.info("streaming race won by provider %r", res_name)
                    break

            if winner_name is None:
                raise ProviderError(f"all raced streaming providers failed: {'; '.join(errors)}")

            for t in tasks:
                if not t.done():
                    t.cancel()

            if first_chunk and first_chunk.text:
                yield first_chunk

            if winner_iter is not None:
                try:
                    while True:
                        next_chunk = await winner_iter.__anext__()
                        yield next_chunk
                except StopAsyncIteration:
                    pass
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()


__all__ = [
    "BaseRouterProvider",
    "FallbackNotifier",
    "FallbackRouterProvider",
    "RaceRouterProvider",
    "RouteHealth",
]
