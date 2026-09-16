"""Combo router provider: tiered model execution with quota & 429 failover."""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from avo import ModelRequest, ModelResponse
from avo.combo.detector import classify_failover_reason
from avo.combo.models import ComboProfile, ComboTier
from avo.exceptions import ProviderError
from avo.providers.base import ModelProvider
from avo.providers.streaming import ModelChunk, StreamingModelProvider, response_to_chunks

_LOG = logging.getLogger("avo.combo.provider")

ComboEventCallback = Callable[[dict[str, Any]], None | Awaitable[None]]
ComboNotifier = Callable[..., None]


@dataclass
class TierHealth:
    """Live health and circuit-breaker status for a single combo tier."""

    name: str
    is_healthy: bool = True
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: str | None = None
    last_latency_ms: float | None = None

    @property
    def is_cooling_down(self) -> bool:
        return time.monotonic() < self.cooldown_until


class ComboRouterProvider(StreamingModelProvider):
    """Orchestrates tiered execution across multiple providers with automatic failover."""

    name = "combo"

    def __init__(
        self,
        profile: ComboProfile,
        tiers: Sequence[tuple[ComboTier, ModelProvider]],
        *,
        event_callback: ComboEventCallback | None = None,
        notifier: ComboNotifier | None = None,
    ) -> None:
        if not tiers:
            raise ValueError(f"Combo {profile.name!r} requires at least one tier")
        self.profile = profile
        self._tiers = list(tiers)
        self._event_callback = event_callback
        self._notifier = notifier
        self.model = f"combo({profile.name})"
        self._health: dict[str, TierHealth] = {
            tier.name: TierHealth(name=tier.name) for tier, _ in self._tiers
        }

    @property
    def notifier(self) -> ComboNotifier | None:
        """Return the current notifier callback."""
        return self._notifier

    @notifier.setter
    def notifier(self, value: ComboNotifier | None) -> None:
        self._notifier = value

    @property
    def tiers(self) -> list[tuple[ComboTier, ModelProvider]]:
        """Return the configured (ComboTier, ModelProvider) list."""
        return list(self._tiers)

    def _select_candidate_tiers(self) -> list[tuple[ComboTier, ModelProvider]]:
        """Return available tiers, prioritizing non-cooling-down tiers."""
        active = [
            (tier, prov)
            for tier, prov in self._tiers
            if not self._health[tier.name].is_cooling_down
        ]
        if active:
            return active

        _LOG.warning(
            "all combo tiers in cooldown for %r; attempting primary chain", self.profile.name
        )
        return list(self._tiers)

    def _record_success(self, name: str, start_time: float) -> None:
        latency = (time.monotonic() - start_time) * 1000.0
        h = self._health[name]
        h.is_healthy = True
        h.consecutive_failures = 0
        h.cooldown_until = 0.0
        h.last_error = None
        h.last_latency_ms = latency

    def _record_failure(self, tier: ComboTier, exc: Exception) -> None:
        h = self._health[tier.name]
        h.is_healthy = False
        h.consecutive_failures += 1
        if tier.cooldown_seconds > 0:
            h.cooldown_until = time.monotonic() + tier.cooldown_seconds
        h.last_error = str(exc)

    def get_health_status(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of current health and cooldown status for all tiers."""
        now = time.monotonic()
        status: dict[str, dict[str, Any]] = {}
        for tier, _ in self._tiers:
            h = self._health[tier.name]
            is_cooling = now < h.cooldown_until
            status[tier.name] = {
                "provider": tier.provider,
                "model": tier.model,
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
        """Reset cooldown and failure counters for one or all tiers."""
        if name is not None:
            if name in self._health:
                self._health[name] = TierHealth(name=name)
        else:
            for t_name in self._health:
                self._health[t_name] = TierHealth(name=t_name)

    async def _emit_failover(
        self,
        from_tier: ComboTier,
        to_tier: ComboTier,
        reason: str,
        exc: Exception,
    ) -> None:
        payload: dict[str, Any] = {
            "combo": self.profile.name,
            "from_tier": from_tier.name,
            "from_provider": from_tier.provider,
            "from_model": from_tier.model,
            "to_tier": to_tier.name,
            "to_provider": to_tier.provider,
            "to_model": to_tier.model,
            "reason": reason,
            "error_snippet": str(exc)[:200],
        }

        if self._event_callback is not None:
            try:
                res = self._event_callback(payload)
                if inspect.isawaitable(res):
                    await res
            except Exception as cb_exc:  # pragma: no cover
                _LOG.debug("failover event callback error: %s", cb_exc)

        if self._notifier is not None:
            notice = (
                f"⤾ Fallback: switched from '{from_tier.name}' ({from_tier.provider}) "
                f"to '{to_tier.name}' ({to_tier.provider}) [{reason}]"
            )
            try:
                self._notifier(notice)
            except TypeError:
                try:
                    self._notifier(from_tier.name, to_tier.name, reason)
                except Exception as notif_exc:  # pragma: no cover
                    _LOG.debug("failover notifier error: %s", notif_exc)
            except Exception as notif_exc:  # pragma: no cover
                _LOG.debug("failover notifier error: %s", notif_exc)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Execute request across candidate tiers until one succeeds."""
        candidates = self._select_candidate_tiers()
        errors: list[str] = []

        for i, (tier, provider) in enumerate(candidates):
            t0 = time.monotonic()
            try:
                response = await provider.generate(request)
                self._record_success(tier.name, t0)
                if i > 0:
                    _LOG.info(
                        "combo %r succeeded on fallback tier %r", self.profile.name, tier.name
                    )
                return response
            except Exception as exc:
                reason = classify_failover_reason(exc)
                if reason is None:
                    # Non-failover error (e.g. 400 Bad Request, auth, etc.) — fail-closed
                    raise

                self._record_failure(tier, exc)
                errors.append(f"[{tier.name}:{tier.provider}] {exc!s}")
                _LOG.warning(
                    "combo tier %r failed (%s: %s); evaluating fallback",
                    tier.name,
                    reason,
                    exc,
                )

                if i + 1 < len(candidates):
                    next_tier = candidates[i + 1][0]
                    await self._emit_failover(tier, next_tier, reason, exc)

        err_str = "; ".join(errors)
        raise ProviderError(f"all combo tiers failed for {self.profile.name!r}: {err_str}")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Stream chunks from the highest-priority functional tier."""
        candidates = self._select_candidate_tiers()
        errors: list[str] = []

        for i, (tier, provider) in enumerate(candidates):
            yielded_any = False
            t0 = time.monotonic()
            try:
                if isinstance(provider, StreamingModelProvider):
                    async for chunk in provider.stream(request):
                        yielded_any = True
                        yield chunk
                    self._record_success(tier.name, t0)
                    return

                resp = await provider.generate(request)
                self._record_success(tier.name, t0)
                for chunk in response_to_chunks(resp):
                    yield chunk
                return
            except Exception as exc:
                if yielded_any:
                    # Partial tokens already yielded — cannot transparently rewind
                    raise ProviderError(
                        f"streaming error on tier {tier.name!r} after partial output: {exc!s}"
                    ) from exc

                reason = classify_failover_reason(exc)
                if reason is None:
                    raise

                self._record_failure(tier, exc)
                errors.append(f"[{tier.name}:{tier.provider}] {exc!s}")
                _LOG.warning(
                    "combo tier %r streaming failed (%s: %s); evaluating fallback",
                    tier.name,
                    reason,
                    exc,
                )

                if i + 1 < len(candidates):
                    next_tier = candidates[i + 1][0]
                    await self._emit_failover(tier, next_tier, reason, exc)

        err_str = "; ".join(errors)
        raise ProviderError(f"all combo tiers failed for {self.profile.name!r}: {err_str}")

    async def aclose(self) -> None:
        """Close client sessions for all underlying providers."""
        for _, provider in self._tiers:
            aclose_fn = getattr(provider, "aclose", None)
            if callable(aclose_fn):
                try:
                    await aclose_fn()
                except Exception as exc:  # pragma: no cover
                    _LOG.debug("error closing tier provider %r: %s", provider, exc)
