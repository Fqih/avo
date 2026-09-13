"""Tests for OAuth authentication and token storage."""

from __future__ import annotations

import asyncio
import urllib.request
from pathlib import Path

import pytest

from avo.auth import (
    auth_file_path,
    default_auth_dir,
    generate_pkce_pair,
    get_stored_token,
    load_all_tokens,
    login_openrouter,
    main_login,
    remove_stored_token,
    run_localhost_callback_server,
    store_token,
)


def test_auth_dir_uses_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom_config"
    monkeypatch.setenv("AVO_CONFIG_DIR", str(custom))
    assert default_auth_dir() == custom
    assert auth_file_path() == custom / "auth.json"


def test_store_and_get_token_with_permissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    assert get_stored_token("openrouter") is None

    target = store_token("openrouter", "sk-or-secret-token-123")
    assert target.is_file()

    # Verify file permission is 0600 (-rw-------)
    stat = target.stat()
    assert oct(stat.st_mode)[-3:] == "600"

    assert get_stored_token("openrouter") == "sk-or-secret-token-123"
    # Case-insensitive
    assert get_stored_token("OpenRouter") == "sk-or-secret-token-123"

    tokens = load_all_tokens()
    assert tokens == {"openrouter": "sk-or-secret-token-123"}


def test_remove_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    store_token("p1", "token1")
    store_token("p2", "token2")

    assert remove_stored_token("p1") is True
    assert get_stored_token("p1") is None
    assert get_stored_token("p2") == "token2"

    assert remove_stored_token("p1") is False  # already removed
    assert remove_stored_token("p2") is True
    assert not auth_file_path().exists()


def test_generate_pkce_pair() -> None:
    verifier, challenge = generate_pkce_pair()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert "=" not in challenge


def test_run_localhost_callback_server() -> None:
    port = 43219
    callback_path = "/test/callback"

    async def scenario() -> dict[str, str]:
        server_task = asyncio.create_task(
            run_localhost_callback_server(port, callback_path, timeout_seconds=5.0)
        )
        await asyncio.sleep(0.05)

        def _call_http() -> None:
            url = f"http://127.0.0.1:{port}{callback_path}?code=secret-code-abc&state=xyz"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                body = resp.read().decode("utf-8")
                assert "Authorization Successful" in body

        await asyncio.to_thread(_call_http)
        return await server_task

    params = asyncio.run(scenario())
    assert params["code"] == "secret-code-abc"
    assert params["state"] == "xyz"


def test_login_openrouter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    port = 43220

    out_lines: list[str] = []

    async def scenario() -> str:
        login_task = asyncio.create_task(
            login_openrouter(
                port=port,
                timeout_seconds=5.0,
                open_browser=False,
                output_writer=out_lines.append,
            )
        )
        await asyncio.sleep(0.05)

        def _call_callback() -> None:
            url = f"http://127.0.0.1:{port}/auth/callback?code=sk-or-oauth-token-999"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200

        await asyncio.to_thread(_call_callback)
        return await login_task

    token = asyncio.run(scenario())
    assert token == "sk-or-oauth-token-999"
    assert get_stored_token("openrouter") == "sk-or-oauth-token-999"
    assert any("OpenRouter token successfully stored" in line for line in out_lines)


def test_main_login_status_and_logout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))

    # Empty status
    assert main_login(["--status"]) == 0
    assert "No providers currently authenticated" in capsys.readouterr().out

    store_token("openrouter", "sk-or-super-secret-key-12345")
    assert main_login(["--status"]) == 0
    out = capsys.readouterr().out
    assert "openrouter" in out
    assert "sk-o...2345" in out

    # Logout
    assert main_login(["--logout", "openrouter"]) == 0
    assert "Logged out of openrouter" in capsys.readouterr().out
    assert get_stored_token("openrouter") is None
