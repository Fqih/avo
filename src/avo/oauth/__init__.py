"""OAuth authentication, token lifecycle, and credential store."""

from __future__ import annotations

from avo.oauth import flows
from avo.oauth.gate import require_subscription_allowed, subscription_allowed
from avo.oauth.refresh import ensure_fresh, needs_refresh, refresh_credential
from avo.oauth.registry import OAUTH, OAuthEntry
from avo.oauth.resolution import PROVIDER_TO_STORE_KEY, resolve_credential
from avo.oauth.store import (
    Credential,
    get_credential,
    load_all_credentials,
    remove_credential,
    store_credential,
)

__all__ = [
    "OAUTH",
    "PROVIDER_TO_STORE_KEY",
    "Credential",
    "OAuthEntry",
    "ensure_fresh",
    "flows",
    "get_credential",
    "load_all_credentials",
    "needs_refresh",
    "refresh_credential",
    "remove_credential",
    "require_subscription_allowed",
    "resolve_credential",
    "store_credential",
    "subscription_allowed",
]
