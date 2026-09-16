"""OAuth authentication, token lifecycle, and credential store."""

from __future__ import annotations

from avo.oauth import flows
from avo.oauth.gate import require_subscription_allowed, subscription_allowed
from avo.oauth.registry import OAUTH, OAuthEntry
from avo.oauth.store import (
    Credential,
    get_credential,
    load_all_credentials,
    remove_credential,
    store_credential,
)

__all__ = [
    "OAUTH",
    "Credential",
    "OAuthEntry",
    "flows",
    "get_credential",
    "load_all_credentials",
    "remove_credential",
    "require_subscription_allowed",
    "store_credential",
    "subscription_allowed",
]
