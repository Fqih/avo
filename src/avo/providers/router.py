"""Fallback and load-balancing multi-provider router.

Enables resilience across multiple model providers (e.g. prioritizing a free
local Ollama instance and falling back to OpenRouter, Groq, or Anthropic when
the local provider is unreachable, overloaded, or rate-limited).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Sequence

from avo import ModelRequest, ModelResponse
from avo.exceptions import ProviderError
from avo.providers.base import ModelProvider
from avo.providers.streaming import ModelChunk, StreamingModelProvider

_LOG = logging.getLogger("avo.providers.router")

FallbackNotifier = Callable[[str, str, Exception], None]


class FallbackRouterProvider:
    """An async provider that queries a chain of providers in priority order.

    If the primary provider fails (network disconnect, rate limit, HTTP 5xx,
    timeout, etc.), it automatically falls back to the next provider in the
    chain until a response succeeds or all providers are exhausted.
    """

    name = "router"

    def __init__(
        self,
        routes: Sequence[tuple[str, ModelProvider]],
        *,
        on_fallback: FallbackNotifier | None = None,
    ) -> None:
        if not routes:
            raise ValueError("FallbackRouterProvider requires at least one route")
        self._routes = list(routes)
        self._on_fallback = on_fallback
        self.model = f"router({','.join(name for name, _ in self._routes)})"

    @property
    def routes(self) -> list[tuple[str, ModelProvider]]:
        """Return the ordered list of (name, provider) routes."""

        return list(self._routes)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Attempt generate on each route sequentially until one succeeds."""

        errors: list[str] = []
        for i, (name, provider) in enumerate(self._routes):
            try:
                response = await provider.generate(request)
                if i > 0:
                    _LOG.info("route %r successfully resolved after fallback", name)
                return response
            except Exception as exc:
                errors.append(f"[{name}] {exc!s}")
                _LOG.warning("provider %r failed (%s); checking next route", name, exc)
                if self._on_fallback is not None and i + 1 < len(self._routes):
                    next_name = self._routes[i + 1][0]
                    self._on_fallback(name, next_name, exc)

        raise ProviderError(f"all routed providers failed: {'; '.join(errors)}")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Stream chunks from the highest-priority functional provider."""

        errors: list[str] = []
        for i, (name, provider) in enumerate(self._routes):
            yielded_any = False
            try:
                if isinstance(provider, StreamingModelProvider):
                    async for chunk in provider.stream(request):
                        yielded_any = True
                        yield chunk
                    return
                # Provider does not implement streaming; fallback to generate
                resp = await provider.generate(request)
                yield ModelChunk(text=resp.content or "")
                return
            except Exception as exc:
                errors.append(f"[{name}] {exc!s}")
                _LOG.warning("streaming from provider %r failed (%s)", name, exc)
                if yielded_any:
                    # Chunks were already delivered to the caller; cannot restart stream
                    raise ProviderError(
                        f"streaming error on {name} after partial output: {exc!s}"
                    ) from exc
                if self._on_fallback is not None and i + 1 < len(self._routes):
                    next_name = self._routes[i + 1][0]
                    self._on_fallback(name, next_name, exc)

        raise ProviderError(f"all routed providers failed: {'; '.join(errors)}")

    async def aclose(self) -> None:
        """Close client sessions for all underlying providers."""

        for _, provider in self._routes:
            aclose_fn = getattr(provider, "aclose", None)
            if callable(aclose_fn):
                try:
                    await aclose_fn()
                except Exception as exc:  # pragma: no cover
                    _LOG.debug("error closing provider %r: %s", provider, exc)


__all__ = ["FallbackNotifier", "FallbackRouterProvider"]
