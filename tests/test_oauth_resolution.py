from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from avo.auth import store_token
from avo.oauth.resolution import resolve_credential
from avo.oauth.store import Credential, store_credential

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def store_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))


def _cred(**over: Any) -> Credential:
    base: dict[str, Any] = {
        "provider": "claude",
        "kind": "oauth",
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "obtained_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(hours=5),
        "subscription": True,
        "account": "fqih@example.com",
    }
    base.update(over)
    return Credential(**base)


def test_env_key_wins_over_store(store_dir: None) -> None:
    store_token("claude", "sk-stored")
    cred = resolve_credential("claude", {"AVO_CLAUDE_API_KEY": "sk-env"})
    assert cred is not None
    assert cred.secret() == "sk-env"


def test_stored_api_key_used(store_dir: None) -> None:
    store_token("claude", "sk-stored")
    cred = resolve_credential("claude", {})
    assert cred is not None
    assert cred.secret() == "sk-stored"


def test_oauth_blocked_when_disabled(store_dir: None) -> None:
    store_credential(_cred())
    assert resolve_credential("claude", {"AVO_ALLOW_SUBSCRIPTION": "0"}) is None


def test_oauth_ok_by_default(store_dir: None) -> None:
    store_credential(_cred(expires_at=datetime.now(UTC) + timedelta(hours=5)))
    cred = resolve_credential("claude", {})
    assert cred is not None
    assert cred.kind == "oauth"


def test_oauth_ok_with_gate(store_dir: None) -> None:
    store_credential(_cred(expires_at=datetime.now(UTC) + timedelta(hours=5)))
    cred = resolve_credential("claude", {"AVO_ALLOW_SUBSCRIPTION": "1"})
    assert cred is not None
    assert cred.kind == "oauth"
