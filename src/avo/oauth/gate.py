"""Explicit opt-in policy for subscription (non-API-key) inference."""

from __future__ import annotations

import os
from collections.abc import Mapping

from avo.auth import AuthError

SUBSCRIPTION_ENV = "AVO_ALLOW_SUBSCRIPTION"


def subscription_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """Return True if the subscription opt-in environment variable is enabled."""

    env = os.environ if environ is None else environ
    return env.get(SUBSCRIPTION_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def require_subscription_allowed(environ: Mapping[str, str] | None = None) -> None:
    """Raise AuthError if subscription inference is not explicitly allowed."""

    if not subscription_allowed(environ):
        raise AuthError(
            "subscription-backed inference is disabled by default because it "
            "uses unofficial client endpoints and may risk account limits. "
            f"Set {SUBSCRIPTION_ENV}=1 to opt in; see docs/guides/subscription-auth.md"
        )
