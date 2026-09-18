"""Tests for ComboRouterProvider (Task 4)."""

from __future__ import annotations

import pytest

from avo import ModelRequest, ModelResponse
from avo.combo.models import ComboProfile, ComboTier
from avo.combo.provider import ComboRouterProvider
from avo.exceptions import ProviderError
from avo.providers.fake import FakeProvider


def _make_req(text: str = "hello") -> ModelRequest:
    return ModelRequest(
        run_id="run-combo-1",
        step=1,
        messages=[{"role": "user", "content": text}],
    )


def _profile() -> ComboProfile:
    return ComboProfile(
        name="test_combo",
        tiers=[
            ComboTier(name="subscription", provider="claude", model="claude-sonnet-5"),
            ComboTier(name="cheap", provider="openrouter", model="llama-3.3-70b"),
            ComboTier(name="free", provider="ollama", model="llama3.2"),
        ],
    )


@pytest.mark.asyncio
async def test_combo_generates_from_primary_tier_on_success() -> None:
    p1 = FakeProvider([ModelResponse(content="primary answer")])
    p2 = FakeProvider([ModelResponse(content="secondary answer")])

    profile = _profile()
    router = ComboRouterProvider(
        profile,
        tiers=[(profile.tiers[0], p1), (profile.tiers[1], p2)],
    )

    resp = await router.generate(_make_req())
    assert resp.content == "primary answer"
    assert len(p1.requests) == 1
    assert len(p2.requests) == 0


class _ErrorProvider(FakeProvider):
    def __init__(self, err_msg: str) -> None:
        super().__init__([])
        self.err_msg = err_msg

    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise ProviderError(self.err_msg)


@pytest.mark.asyncio
async def test_combo_fails_over_to_next_tier_on_429() -> None:
    events: list[dict[str, object]] = []

    def on_event(ev: dict[str, object]) -> None:
        events.append(ev)

    p1 = _ErrorProvider("HTTP 429: Too Many Requests")
    p2 = FakeProvider([ModelResponse(content="fallback answer")])

    profile = _profile()
    router = ComboRouterProvider(
        profile,
        tiers=[(profile.tiers[0], p1), (profile.tiers[1], p2)],
        event_callback=on_event,
    )

    resp = await router.generate(_make_req())
    assert resp.content == "fallback answer"
    assert len(events) == 1
    ev = events[0]
    assert ev["combo"] == "test_combo"
    assert ev["from_tier"] == "subscription"
    assert ev["to_tier"] == "cheap"
    assert ev["reason"] == "rate_limited_429"


@pytest.mark.asyncio
async def test_combo_does_not_failover_on_400_bad_request() -> None:
    p1 = _ErrorProvider("HTTP 400: Invalid temperature parameter")
    p2 = FakeProvider([ModelResponse(content="should not be reached")])

    profile = _profile()
    router = ComboRouterProvider(
        profile,
        tiers=[(profile.tiers[0], p1), (profile.tiers[1], p2)],
    )

    with pytest.raises(ProviderError, match="HTTP 400"):
        await router.generate(_make_req())

    assert len(p2.requests) == 0


@pytest.mark.asyncio
async def test_combo_streams_chunks_with_failover() -> None:
    events: list[dict[str, object]] = []

    p1 = _ErrorProvider("quota_exceeded: out of credits")
    p2 = FakeProvider([ModelResponse(content="streamed fallback answer")])

    profile = _profile()
    router = ComboRouterProvider(
        profile,
        tiers=[(profile.tiers[0], p1), (profile.tiers[1], p2)],
        event_callback=lambda ev: events.append(ev),
    )

    chunks = []
    async for chunk in router.stream(_make_req()):
        if chunk.text:
            chunks.append(chunk.text)

    assert "".join(chunks) == "streamed fallback answer"
    assert len(events) == 1
    assert events[0]["reason"] == "quota_exceeded"


@pytest.mark.asyncio
async def test_combo_cooldown_and_health_status() -> None:
    p1 = _ErrorProvider("429 rate limit")
    p2 = FakeProvider([ModelResponse(content="fallback answer")])

    profile = _profile()
    router = ComboRouterProvider(
        profile,
        tiers=[(profile.tiers[0], p1), (profile.tiers[1], p2)],
    )

    await router.generate(_make_req())

    status = router.get_health_status()
    assert status["subscription"]["in_cooldown"] is True
    assert status["subscription"]["consecutive_failures"] == 1
    assert status["cheap"]["in_cooldown"] is False

    # Reset health
    router.reset_health("subscription")
    assert router.get_health_status()["subscription"]["in_cooldown"] is False


@pytest.mark.asyncio
async def test_combo_aclose_calls_underlying_providers() -> None:
    p1 = FakeProvider([ModelResponse(content="ans")])
    profile = _profile()
    router = ComboRouterProvider(
        profile,
        tiers=[(profile.tiers[0], p1)],
    )
    await router.aclose()


@pytest.mark.asyncio
async def test_tier_health_lock_prevents_concurrent_write_corruption() -> None:
    """Concurrent generate() calls must not corrupt TierHealth state."""
    import asyncio

    fail_count = 20

    class _RateLimitedProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__([])

        async def generate(self, request: ModelRequest) -> ModelResponse:
            raise ProviderError("HTTP 429: Too Many Requests")

    p_fail = _RateLimitedProvider()
    p_ok = FakeProvider([ModelResponse(content=f"ok-{i}") for i in range(fail_count)])
    profile = _profile()
    router = ComboRouterProvider(
        profile,
        tiers=[(profile.tiers[0], p_fail), (profile.tiers[1], p_ok)],
    )

    # Fire concurrent generate() calls — each will record a failure then a success.
    await asyncio.gather(*(router.generate(_make_req()) for _ in range(fail_count)))

    # After all concurrent calls, health for tier[0] should be unhealthy (cooldown),
    # and the consecutive_failures counter must be a consistent integer, not corrupted.
    h = router._health[profile.tiers[0].name]
    assert isinstance(h.consecutive_failures, int)
    assert h.consecutive_failures >= 1
    assert h.is_healthy is False

