from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from avo.oauth.imports import find_importable, import_credential
from avo.oauth.store import get_credential


def _fake_id_token(email: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.fake_signature"


def test_import_claude_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    creds_file = fake_claude / ".credentials.json"
    creds_file.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "at-claude-cli",
                    "refreshToken": "rt-claude-cli",
                    "expiresAt": 1770000000000,
                    "scope": "user:inference",
                }
            }
        )
    )

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path / "avo_config"))

    cred = find_importable("claude")
    assert cred is not None
    assert cred.provider == "claude"
    assert cred.kind == "oauth"
    assert cred.secret() == "at-claude-cli"
    assert cred.refresh_token == "rt-claude-cli"
    assert cred.subscription is True
    assert cred.expires_at is not None

    # Test import_credential saves to store
    saved = import_credential("claude")
    assert saved is not None
    stored = get_credential("claude")
    assert stored is not None and stored.secret() == "at-claude-cli"


def test_import_codex_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_codex = fake_home / ".codex"
    fake_codex.mkdir(parents=True)
    auth_file = fake_codex / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "at-codex-cli",
                    "refresh_token": "rt-codex-cli",
                    "id_token": _fake_id_token("codex_user@example.com"),
                }
            }
        )
    )

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path / "avo_config"))

    for alias in ("codex", "chatgpt"):
        cred = find_importable(alias)
        assert cred is not None
        assert cred.provider == "codex"
        assert cred.account == "codex_user@example.com"
        assert cred.secret() == "at-codex-cli"
        assert cred.refresh_token == "rt-codex-cli"
        assert cred.subscription is True


def test_missing_files_return_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "empty_home"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    assert find_importable("claude") is None
    assert find_importable("codex") is None
    assert find_importable("unknown") is None


def test_malformed_json_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home_bad"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    (fake_claude / ".credentials.json").write_text("not json!")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    assert find_importable("claude") is None
