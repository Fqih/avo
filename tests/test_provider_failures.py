"""Typed provider failure classification tests."""

from avo.exceptions import ProviderError, ProviderFailureKind


def test_provider_error_exposes_stable_failure_kind_and_status() -> None:
    error = ProviderError.from_status(401, "invalid credentials")

    assert error.kind is ProviderFailureKind.AUTHENTICATION
    assert error.status_code == 401
    assert error.retryable is False


def test_provider_error_classifies_transient_statuses() -> None:
    for status in (429, 500, 503):
        error = ProviderError.from_status(status, "upstream")
        assert error.kind in {ProviderFailureKind.RATE_LIMIT, ProviderFailureKind.SERVER}
        assert error.retryable is True


def test_provider_error_keeps_legacy_constructor_defaults() -> None:
    error = ProviderError("transport failed")

    assert error.kind is ProviderFailureKind.UNKNOWN
    assert error.status_code is None
    assert error.retryable is True
