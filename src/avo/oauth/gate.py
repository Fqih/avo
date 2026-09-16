"""Explicit opt-in policy for subscription (non-API-key) inference."""

from __future__ import annotations

import os
from collections.abc import Mapping

from avo.auth import AuthError

SUBSCRIPTION_ENV = "AVO_ALLOW_SUBSCRIPTION"


def subscription_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """Return True if subscription / web OAuth inference is allowed.

    By default, subscription and web OAuth logins (ChatGPT, Gemini, Claude web)
    are permitted for inference. Users may explicitly disable them by setting
    AVO_ALLOW_SUBSCRIPTION to 0, false, no, or off.
    """

    env = os.environ if environ is None else environ
    return env.get(SUBSCRIPTION_ENV, "").strip().lower() not in {"0", "false", "no", "off"}


def require_subscription_allowed(environ: Mapping[str, str] | None = None) -> None:
    """Raise AuthError if subscription / web OAuth inference is explicitly disabled."""

    if not subscription_allowed(environ):
        raise AuthError(
            f"web/subscription inference is disabled because {SUBSCRIPTION_ENV}=0 is set. "
            f"Unset or remove {SUBSCRIPTION_ENV} to enable chat with your login."
        )
