from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from avo.auth import get_stored_token, main_login
from avo.oauth.store import Credential, store_credential


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))


def test_login_key_stdin_any_provider(
    isolated_config: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-deepseek-12345678\n"))
    rc = main_login(["deepseek", "--key-stdin"])
    assert rc == 0
    assert get_stored_token("deepseek") == "sk-deepseek-12345678"
    out = capsys.readouterr().out
    assert "Stored API key for deepseek" in out


def test_login_key_stdin_empty_error(
    isolated_config: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
    rc = main_login(["deepseek", "--key-stdin"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Empty API key" in err


def test_login_unknown_provider_fails(
    isolated_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main_login(["some_random_vendor"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Unknown login provider" in err


def test_login_oauth_claude(
    isolated_config: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called = False

    async def fake_pkce(entry: Any, **kwargs: Any) -> Credential:
        nonlocal called
        called = True
        return Credential(
            provider="claude",
            kind="oauth",
            access_token="at-claude-test",
            refresh_token="rt-claude-test",
            account="fqih@example.com",
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            subscription=True,
        )

    monkeypatch.setattr("avo.oauth.flows.run_pkce_login", fake_pkce)
    rc = main_login(["claude", "--no-browser"])
    assert rc == 0
    assert called is True
    out = capsys.readouterr().out
    assert "fqih@example.com" in out
    assert "at-claude-test" not in out


def test_login_status_v2_redaction(
    isolated_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="secret-access-token-123",
            refresh_token="secret-refresh-token-456",
            account="user@example.com",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            subscription=True,
        )
    )
    rc = main_login(["--status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude: oauth ✓ user@example.com" in out
    assert "secret-access-token" not in out
    assert "secret-refresh-token" not in out
