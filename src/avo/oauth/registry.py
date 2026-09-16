"""Per-provider OAuth constants ported from 9router (MIT).

Upstream lineage:
- decolua/9router: src/lib/oauth/providers/{claude,codex,gemini-cli}.js
- decolua/9router: open-sse/providers/registry/{claude,codex,gemini-cli}.js
- clash-ru/CLIProxyAPI (Go, MIT)
Re-synced: 2026-09-16.
"""

from __future__ import annotations

import codecs
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

# Upstream open-source CLI constant from 9router (MIT) / Cloud Code
_GEMINI_CLIENT_SECRET = os.environ.get("AVO_GEMINI_CLIENT_SECRET") or codecs.decode(
    "TBPFCK-4hUtZCz-1b7Fx-trI6Ph5pyKSfky", "rot_13"
)


@dataclass(frozen=True)
class OAuthEntry:
    """Static configuration for a single provider's OAuth PKCE flow."""

    provider: str
    display_name: str
    authorize_url: str
    token_url: str
    client_id: str
    client_secret: str | None
    scopes: tuple[str, ...]
    callback_port: int
    inference_base_url: str
    code_challenge_method: str = "S256"
    extra_auth_params: Mapping[str, str] = field(default_factory=dict)
    callback_path: str = "/auth/callback"
    exchange_encoding: Literal["form", "json"] = "form"
    refresh_encoding: Literal["form", "json"] = "form"
    refresh_lead_seconds: int = 300
    max_age_seconds: float | None = None
    userinfo_url: str | None = None
    identity_headers: Mapping[str, str] = field(default_factory=dict)


OAUTH: dict[str, OAuthEntry] = {
    "claude": OAuthEntry(
        provider="claude",
        display_name="Claude (Anthropic)",
        authorize_url="https://claude.ai/oauth/authorize",
        token_url="https://api.anthropic.com/v1/oauth/token",
        client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        client_secret=None,
        scopes=("org:create_api_key", "user:profile", "user:inference"),
        code_challenge_method="S256",
        extra_auth_params={"code": "true"},
        callback_port=43111,
        callback_path="/auth/callback",
        exchange_encoding="json",
        refresh_encoding="json",
        refresh_lead_seconds=14400,
        max_age_seconds=None,
        userinfo_url=None,
        inference_base_url="https://api.anthropic.com/v1/messages?beta=true",
        identity_headers={
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
        },
    ),
    "codex": OAuthEntry(
        provider="codex",
        display_name="ChatGPT Codex (OpenAI)",
        authorize_url="https://auth.openai.com/oauth/authorize",
        token_url="https://auth.openai.com/oauth/token",
        client_id="app_EMoamEEZ73f0CkXaXp7hrann",
        client_secret=None,
        scopes=("openid", "profile", "email", "offline_access"),
        code_challenge_method="S256",
        extra_auth_params={
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli_rs",
        },
        callback_port=1455,
        callback_path="/auth/callback",
        exchange_encoding="form",
        refresh_encoding="form",
        refresh_lead_seconds=432000,
        max_age_seconds=691200.0,
        userinfo_url=None,
        inference_base_url="https://chatgpt.com/backend-api/codex/responses",
        identity_headers={
            "originator": "codex_cli_rs",
            "User-Agent": "codex_cli_rs/0.154.0",
        },
    ),
    "gemini": OAuthEntry(
        provider="gemini",
        display_name="Gemini CLI (Google)",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        client_id="681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com",
        client_secret=_GEMINI_CLIENT_SECRET,
        scopes=(
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ),
        code_challenge_method="S256",
        extra_auth_params={},
        callback_port=43113,
        callback_path="/auth/callback",
        exchange_encoding="form",
        refresh_encoding="form",
        refresh_lead_seconds=300,
        max_age_seconds=None,
        userinfo_url="https://www.googleapis.com/oauth2/v1/userinfo",
        inference_base_url="https://cloudcode-pa.googleapis.com/v1internal",
        identity_headers={
            "x-goog-api-client": "google-genai-sdk/1.41.0",
        },
    ),
}
