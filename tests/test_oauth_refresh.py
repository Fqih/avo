from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from avo.auth import AuthError
from avo.oauth.refresh import ensure_fresh, needs_refresh
from avo.oauth.registry import OAUTH, OAuthEntry
from avo.oauth.store import Credential, get_credential, store_credential

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def store_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))


@pytest.fixture
def frozen_now() -> datetime:
    return NOW


def _cred(**over: Any) -> Credential:
    base: dict[str, Any] = {
        "provider": "claude",
        "kind": "oauth",
        "access_token": "at-old",
        "refresh_token": "rt-old",
        "obtained_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(hours=3),
        "subscription": True,
        "account": "fqih@example.com",
    }
    base.update(over)
    return Credential(**base)


class FakeRequester:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def __call__(self, entry: OAuthEntry, form: dict[str, str]) -> Any:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_needs_refresh_boundary() -> None:
    e = OAUTH["claude"]
    assert needs_refresh(
        _cred(expires_at=NOW + timedelta(seconds=e.refresh_lead_seconds)),
        now=NOW,
        entry=e,
    )
    assert not needs_refresh(
        _cred(expires_at=NOW + timedelta(seconds=e.refresh_lead_seconds + 1)),
        now=NOW,
        entry=e,
    )


def test_api_key_credential_never_needs_refresh() -> None:
    assert not needs_refresh(
        Credential(provider="x", kind="api_key", access_token="k"),
        now=NOW,
        entry=OAUTH["claude"],
    )


@pytest.mark.asyncio
async def test_rotation_persists_new_refresh_token(store_dir: None, frozen_now: datetime) -> None:
    store_credential(_cred())
    fake = FakeRequester(
        [{"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 21600}]
    )
    cred = await ensure_fresh("claude", now=frozen_now, token_requester=fake)
    assert cred.secret() == "at-new"
    stored = get_credential("claude")
    assert stored is not None and stored.refresh_token == "rt-new"
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_second_failure_requires_relogin(store_dir: None, frozen_now: datetime) -> None:
    store_credential(_cred())
    fake = FakeRequester([AuthError("401"), AuthError("401")])
    with pytest.raises(AuthError, match="avo login claude"):
        await ensure_fresh("claude", now=frozen_now, token_requester=fake)


@pytest.mark.asyncio
async def test_concurrent_ensure_fresh_refreshes_once(
    store_dir: None, frozen_now: datetime
) -> None:
    store_credential(_cred())
    fake = FakeRequester([{"access_token": "at-new", "expires_in": 21600}])
    await asyncio.gather(
        *(ensure_fresh("claude", now=frozen_now, token_requester=fake) for _ in range(10))
    )
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_codex_max_age_forces_relogin(store_dir: None) -> None:
    e = OAUTH["codex"]
    assert e.max_age_seconds is not None
    old = _cred(
        provider="codex",
        obtained_at=NOW - timedelta(seconds=e.max_age_seconds + 60),
        expires_at=NOW + timedelta(days=1),
    )
    store_credential(old)
    with pytest.raises(AuthError, match="re-login"):
        await ensure_fresh("codex", now=NOW, token_requester=FakeRequester([]))
