"""Explicit opt-in policy for subscription (non-API-key) inference."""

from __future__ import annotations

import os
from collections.abc import Mapping

from avo.auth import AuthError

SUBSCRIPTION_ENV = "AVO_ALLOW_SUBSCRIPTION"


def subscription_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """Return True if subscription / web OAuth inference is allowed.

    Subscription and web OAuth logins (ChatGPT, Gemini, Claude web) require an
    explicit opt-in through AVO_ALLOW_SUBSCRIPTION.
    """

    env = os.environ if environ is None else environ
    return env.get(SUBSCRIPTION_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def require_subscription_allowed(environ: Mapping[str, str] | None = None) -> None:
    """Raise AuthError unless subscription / web OAuth inference is explicitly enabled."""

    if not subscription_allowed(environ):
        raise AuthError(
            "web/subscription inference requires explicit opt-in. "
            f"Set {SUBSCRIPTION_ENV}=1 or run `avo setup --allow-subscription` "
            "to enable chat with your login."
        )
