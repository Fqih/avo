"""Reuse existing official CLI logins from Claude Code and Codex CLI."""

from __future__ import annotations

import base64
import contextlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from avo.oauth.store import Credential, store_credential


def _claude_code_credential() -> Credential | None:
    """Read credentials from Claude Code CLI (~/.claude/.credentials.json)."""

    path = Path.home() / ".claude" / ".credentials.json"
    if not path.is_file():
        return None

    with contextlib.suppress(Exception):
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get("claudeAiOauth")
        if not isinstance(entry, dict):
            return None

        access_token = entry.get("accessToken")
        refresh_token = entry.get("refreshToken")
        if not access_token:
            return None

        expires_at = None
        expires_at_ms = entry.get("expiresAt")
        if isinstance(expires_at_ms, (int, float)):
            expires_at = datetime.fromtimestamp(expires_at_ms / 1000.0, tz=UTC)

        return Credential(
            provider="claude",
            kind="oauth",
            access_token=str(access_token),
            refresh_token=str(refresh_token) if refresh_token else None,
            expires_at=expires_at,
            obtained_at=datetime.now(UTC),
            scope=str(entry.get("scope")) if entry.get("scope") else None,
            subscription=True,
        )

    return None


def _codex_cli_credential() -> Credential | None:
    """Read credentials from OpenAI Codex CLI (~/.codex/auth.json)."""

    path = Path.home() / ".codex" / "auth.json"
    if not path.is_file():
        return None

    with contextlib.suppress(Exception):
        data = json.loads(path.read_text(encoding="utf-8"))
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            return None

        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token:
            return None

        account = None
        id_token = tokens.get("id_token")
        if isinstance(id_token, str) and "." in id_token:
            parts = id_token.split(".")
            if len(parts) >= 2:
                with contextlib.suppress(Exception):
                    payload_b64 = parts[1]
                    payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                    decoded = base64.urlsafe_b64decode(payload_b64)
                    payload_raw = decoded.decode("utf-8", errors="replace")
                    payload = json.loads(payload_raw)
                    if isinstance(payload, dict):
                        account = payload.get("email")

        return Credential(
            provider="codex",
            kind="oauth",
            access_token=str(access_token),
            refresh_token=str(refresh_token) if refresh_token else None,
            obtained_at=datetime.now(UTC),
            account=str(account) if account else None,
            subscription=True,
        )

    return None


_READERS: dict[str, Callable[[], Credential | None]] = {
    "claude": _claude_code_credential,
    "codex": _codex_cli_credential,
    "chatgpt": _codex_cli_credential,
}


def find_importable(store_key: str) -> Credential | None:
    """Inspect local vendor credential paths and return importable Credential if found."""

    reader = _READERS.get(store_key.lower())
    if reader is None:
        return None

    with contextlib.suppress(Exception):
        return reader()

    return None


def import_credential(store_key: str, *, copy: bool = True) -> Credential | None:
    """Import vendor credential for ``store_key``, optionally persisting to Avo store."""

    cred = find_importable(store_key)
    if cred is not None and copy:
        store_credential(cred)
    return cred
