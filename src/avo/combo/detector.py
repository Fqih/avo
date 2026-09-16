"""Quota and rate-limit error classifier for combo route failover."""

from __future__ import annotations

import re

from avo.auth import AuthError

# Matches HTTP 429 and rate-limiting phrasing across model vendors
_RATE_LIMIT_PATTERNS = (
    r"\b429\b",
    r"rate_limit",
    r"rate limit",
    r"too many requests",
    r"tpm_limit",
    r"rpm_limit",
    r"requests per minute",
    r"tokens per minute",
)

# Matches quota and credit balance exhaustion across model vendors
_QUOTA_PATTERNS = (
    r"insufficient_quota",
    r"quota_exceeded",
    r"exceeded.*quota",
    r"usage_limit",
    r"usage limit",
    r"out of credits",
    r"credit balance",
    r"billing",
    r"payment required",
    r"\b402\b",
)

# Matches transient provider/server capacity overload
_RESOURCE_PATTERNS = (
    r"\b503\b",
    r"model_overloaded",
    r"overloaded",
    r"capacity_exceeded",
    r"resource_exhausted",
)

# Matches circuit-breaker cooldown conditions
_COOLDOWN_PATTERNS = (
    r"cooldown",
    r"cooling down",
    r"circuit[-_ ]breaker",
)


def classify_failover_reason(exc: Exception) -> str | None:
    """Classify the failover reason if `exc` is a recoverable quota/capacity error.

    Returns:
        One of 'rate_limited_429', 'quota_exceeded', 'resource_exhausted',
        'cooldown', or None if the error is fatal / not eligible for failover.
    """

    if isinstance(exc, AuthError):
        return None

    msg = str(exc).lower()

    # Check fatal non-failover conditions first (400 Bad Request / validation)
    if "http 400" in msg or "invalid parameter" in msg or "schemavalidation" in msg:
        return None
    if "401" in msg or "unauthorized" in msg or "invalid api key" in msg:
        return None

    # Check cooldown
    for pattern in _COOLDOWN_PATTERNS:
        if re.search(pattern, msg):
            return "cooldown"

    # Check 429 / rate limits
    for pattern in _RATE_LIMIT_PATTERNS:
        if re.search(pattern, msg):
            return "rate_limited_429"

    # Check quota / billing
    for pattern in _QUOTA_PATTERNS:
        if re.search(pattern, msg):
            return "quota_exceeded"

    # Check resource overload
    for pattern in _RESOURCE_PATTERNS:
        if re.search(pattern, msg):
            return "resource_exhausted"

    # Check HTTP status attribute if attached to exception
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code == 429:
        return "rate_limited_429"
    if status_code in (502, 503, 504):
        return "resource_exhausted"

    return None


def is_quota_or_rate_limit_error(exc: Exception) -> bool:
    """Return True if `exc` qualifies for transparent combo failover."""

    return classify_failover_reason(exc) is not None
