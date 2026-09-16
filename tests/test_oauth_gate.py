from __future__ import annotations

import pytest

from avo.auth import AuthError
from avo.oauth.gate import require_subscription_allowed, subscription_allowed


def test_gate_default_allowed() -> None:
    assert subscription_allowed({}) is True


def test_gate_explicit_disabled() -> None:
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "0"}) is False
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "false"}) is False
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "no"}) is False
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "off"}) is False


def test_gate_on() -> None:
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "1"}) is True
    assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "true"}) is True


def test_require_raises_when_explicitly_disabled() -> None:
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        require_subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "0"})


def test_require_succeeds_by_default() -> None:
    require_subscription_allowed({})  # no raise
    require_subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "1"})  # no raise
