"""Resolution of credentials from environment overrides or stored records."""

from __future__ import annotations

import os
from collections.abc import Mapping

from avo.oauth.gate import subscription_allowed
from avo.oauth.store import Credential, get_credential

PROVIDER_TO_STORE_KEY: dict[str, str] = {
    "anthropic": "claude",
    "openrouter": "openrouter",
    "github": "github",
    "openai": "codex",
    "gemini": "gemini",
}


def resolve_credential(
    store_key: str,
    environ: Mapping[str, str] | None = None,
) -> Credential | None:
    """Resolve credential for ``store_key`` prioritizing environment over stored records.

    Order of evaluation:
    1. Environment variable ``AVO_{STORE_KEY_UPPER}_API_KEY``
    2. Stored API key record
    3. Stored OAuth record (allowed only if subscription gate is enabled)
    """

    env = os.environ if environ is None else environ
    key = store_key.lower()

    env_var_name = f"AVO_{key.upper()}_API_KEY"
    env_token = env.get(env_var_name, "").strip()
    if env_token:
        return Credential(provider=key, kind="api_key", access_token=env_token)

    stored = get_credential(key)
    if stored is None:
        return None

    if stored.kind == "api_key":
        return stored

    if stored.kind == "oauth":
        if not subscription_allowed(env):
            return None
        return stored

    return None
