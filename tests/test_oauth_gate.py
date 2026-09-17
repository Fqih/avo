from __future__ import annotations

import pytest

from avo.auth import AuthError
from avo.oauth.gate import require_subscription_allowed, subscription_allowed


def test_gate_unset_is_disabled() -> None:
    assert subscription_allowed({}) is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "unexpected"])
def test_gate_false_values_are_disabled(value: str) -> None:
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": value}) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
def test_gate_explicit_true_values_are_enabled(value: str) -> None:
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": value}) is True


def test_require_raises_without_explicit_opt_in() -> None:
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        require_subscription_allowed({})


def test_require_succeeds_with_explicit_opt_in() -> None:
    require_subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "1"})  # no raise
