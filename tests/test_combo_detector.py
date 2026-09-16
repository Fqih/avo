"""Tests for quota and rate-limit error classifier (Task 3)."""

from __future__ import annotations

import pytest

from avo.auth import AuthError
from avo.combo.detector import classify_failover_reason, is_quota_or_rate_limit_error
from avo.exceptions import ProviderError


@pytest.mark.parametrize(
    ("err_msg", "expected_reason"),
    [
        ("HTTP 429 Too Many Requests: rate limit exceeded for model", "rate_limited_429"),
        ("rate_limit_exceeded: TPM limit reached", "rate_limited_429"),
        ("Error 429: Requests per minute limit exceeded", "rate_limited_429"),
        ("insufficient_quota: You have exceeded your current quota", "quota_exceeded"),
        ("billing error: out of credits on OpenRouter", "quota_exceeded"),
        ("Usage limit reached for subscription tier", "quota_exceeded"),
        ("HTTP 503 Service Unavailable: model_overloaded", "resource_exhausted"),
        ("Anthropic server is temporarily overloaded", "resource_exhausted"),
        ("Route 'claude' is currently in circuit-breaker cooldown", "cooldown"),
    ],
)
def test_classifier_detects_failover_conditions(err_msg: str, expected_reason: str) -> None:
    exc = ProviderError(err_msg)
    assert is_quota_or_rate_limit_error(exc) is True
    assert classify_failover_reason(exc) == expected_reason


@pytest.mark.parametrize(
    "err_msg",
    [
        "HTTP 400: Invalid parameter 'temperature'",
        "JSONDecodeError: Expecting value: line 1 column 1",
        "SchemaValidationError: required property 'prompt' missing",
    ],
)
def test_classifier_rejects_syntax_and_bad_request_errors(err_msg: str) -> None:
    exc = ProviderError(err_msg)
    assert is_quota_or_rate_limit_error(exc) is False
    assert classify_failover_reason(exc) is None


def test_classifier_rejects_auth_errors() -> None:
    exc = AuthError("HTTP 401: Invalid API key provided")
    assert is_quota_or_rate_limit_error(exc) is False
    assert classify_failover_reason(exc) is None
