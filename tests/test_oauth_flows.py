from __future__ import annotations

import asyncio
import json
import urllib.parse
from typing import Any

import pytest

from avo.auth import AuthError, auth_file_path
from avo.oauth import flows
from avo.oauth.registry import OAUTH
from avo.oauth.store import Credential, store_credential


def test_authorize_url_contains_pkce_and_state() -> None:
    e = OAUTH["claude"]
    url = flows.build_authorize_url(e, "http://localhost:43111/auth/callback", "ST", "CHAL")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge"] == ["CHAL"] and q["state"] == ["ST"]
    assert q["client_id"] == [e.client_id]
    assert q["code"] == ["true"]  # claude extra param


def test_exchange_sends_verifier_and_grant() -> None:
    captured: dict[str, Any] = {}

    def fake(
        url: str, body: dict[str, Any], headers: dict[str, str], timeout: float = 15.0
    ) -> dict[str, Any]:
        captured.update(url=url, body=body)
        return {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 21600,
            "scope": "user:inference",
            "account": {"email_address": "fqih@example.com"},
        }

    async def _fake_cb(**kw: Any) -> dict[str, str]:
        return {"code": "c1", "state": kw["expected_state"]}

    cred = asyncio.run(
        flows.run_pkce_login(
            OAUTH["claude"],
            callback_runner=_fake_cb,
            token_requester=lambda entry, form: fake(entry.token_url, form, {}),
            open_browser=False,
            output_writer=lambda s: None,
        )
    )
    assert captured["body"]["code_verifier"]
    assert captured["body"]["grant_type"] == "authorization_code"
    assert cred.secret() == "at" and cred.account == "fqih@example.com"


def test_state_mismatch_raises() -> None:
    async def _fake_cb(**kw: Any) -> dict[str, str]:
        return {"code": "c1", "state": "BAD"}

    with pytest.raises(AuthError, match="state"):
        asyncio.run(
            flows.run_pkce_login(
                OAUTH["claude"],
                callback_runner=_fake_cb,
                token_requester=lambda entry, form: {},
                open_browser=False,
                output_writer=lambda s: None,
            )
        )


def test_token_error_becomes_auth_error() -> None:
    async def _fake_cb(**kw: Any) -> dict[str, str]:
        return {"code": "c1", "state": kw["expected_state"]}

    def _failing_requester(entry: Any, form: Any) -> dict[str, Any]:
        raise AuthError("token exchange failed: invalid_grant")

    with pytest.raises(AuthError, match="invalid_grant"):
        asyncio.run(
            flows.run_pkce_login(
                OAUTH["claude"],
                callback_runner=_fake_cb,
                token_requester=_failing_requester,
                open_browser=False,
                output_writer=lambda s: None,
            )
        )


def test_map_account_from_claude_shape() -> None:
    raw = {
        "access_token": "at-x",
        "refresh_token": "rt-x",
        "expires_in": 3600,
        "scope": "user:profile",
        "account": {"email_address": "test@domain.com"},
    }
    cred = flows.map_tokens(raw, OAUTH["claude"])
    assert cred.account == "test@domain.com"
    assert cred.secret() == "at-x"


def test_stored_oauth_credentials_are_plaintext_json_with_restrictive_permissions() -> None:
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="access-token",
            refresh_token="refresh-token",
            subscription=True,
        )
    )

    path = auth_file_path()
    stored = json.loads(path.read_text(encoding="utf-8"))

    assert stored["claude"]["access_token"] == "access-token"
    assert stored["claude"]["refresh_token"] == "refresh-token"
    assert path.stat().st_mode & 0o777 == 0o600
