from __future__ import annotations

import pytest

from avo.auth import AuthError
from avo.oauth.gate import require_subscription_allowed, subscription_allowed


def test_gate_default_off() -> None:
    assert subscription_allowed({}) is False


def test_gate_on() -> None:
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "1"}) is True


def test_gate_true_string() -> None:
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "true"}) is True


def test_require_raises_with_hint() -> None:
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        require_subscription_allowed({})
    require_subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "1"})  # no raise
