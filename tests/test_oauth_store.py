from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from avo.auth import AuthError, auth_file_path, get_stored_token, store_token
from avo.oauth.store import (
    Credential,
    get_credential,
    load_all_credentials,
    remove_credential,
    store_credential,
)


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))


def _cred(provider: str = "claude", **over: object) -> Credential:
    base = {
        "provider": provider,
        "kind": "oauth",
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": datetime.now(UTC) + timedelta(hours=6),
        "account": "fqih@example.com",
        "subscription": True,
    }
    base.update(over)
    return Credential(**base)  # type: ignore[arg-type]


def test_v1_string_file_still_loads() -> None:
    path = auth_file_path()
    path.write_text(json.dumps({"openrouter": "sk-or-1"}))
    assert get_stored_token("openrouter") == "sk-or-1"
    assert load_all_credentials()["openrouter"].kind == "api_key"
    assert load_all_credentials()["openrouter"].secret() == "sk-or-1"


def test_store_oauth_record_roundtrip() -> None:
    store_credential(_cred())
    cred = get_credential("claude")
    assert cred is not None
    assert cred.secret() == "at-1"
    assert json.loads(auth_file_path().read_text())["claude"]["kind"] == "oauth"
    assert auth_file_path().stat().st_mode & 0o777 == 0o600


def test_repr_and_str_redact_tokens() -> None:
    text = repr(_cred())
    assert "at-1" not in text and "rt-1" not in text


def test_mixed_file_preserves_other_entries_on_write() -> None:
    store_token("openrouter", "sk-or-1")
    store_credential(_cred())
    assert get_stored_token("openrouter") == "sk-or-1"
    assert get_credential("claude") is not None


def test_secret_raises_when_empty() -> None:
    with pytest.raises(AuthError):
        Credential(provider="x", kind="oauth").secret()


def test_remove_credential() -> None:
    store_credential(_cred("claude"))
    assert get_credential("claude") is not None
    assert remove_credential("claude") is True
    assert get_credential("claude") is None
    assert remove_credential("claude") is False
