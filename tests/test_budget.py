"""Tests for budget enforcement."""

from __future__ import annotations

from decimal import Decimal

import pytest

from avo.budget import BudgetChecker, BudgetConfig, BudgetError, resolve_budget_config
from avo.models import TokenUsage


def test_config_defaults_to_no_limits() -> None:
    config = BudgetConfig()
    assert not config.has_limits
    assert config.warning_usd is None
    assert config.hard_limit_usd is None


def test_config_rejects_negative_warning() -> None:
    with pytest.raises(BudgetError, match="warning_usd"):
        BudgetConfig(warning_usd=Decimal("-1"))


def test_config_rejects_negative_hard_limit() -> None:
    with pytest.raises(BudgetError, match="hard_limit_usd"):
        BudgetConfig(hard_limit_usd=Decimal("-1"))


def test_config_rejects_warning_above_hard_limit() -> None:
    with pytest.raises(BudgetError, match="warning_usd"):
        BudgetConfig(warning_usd=Decimal("10"), hard_limit_usd=Decimal("5"))


def test_check_no_rates_returns_allowed() -> None:
    checker = BudgetChecker(BudgetConfig(hard_limit_usd=Decimal("1")))
    decision = checker.check(TokenUsage(input_tokens=1000, output_tokens=1000))
    assert decision.allowed is True
    assert decision.spent_usd is None


def test_check_under_warning() -> None:
    checker = BudgetChecker(
        BudgetConfig(
            warning_usd=Decimal("1"),
            hard_limit_usd=Decimal("10"),
        )
    )
    rates = (Decimal("0.01"), Decimal("0.02"))
    decision = checker.check(
        TokenUsage(input_tokens=1000, output_tokens=1000),
        rates=rates,
    )
    assert decision.allowed is True
    assert decision.crossed_warning is False
    assert decision.exceeded_hard_limit is False
    assert decision.spent_usd == Decimal("0.030000")


def test_check_at_warning_triggers_flag() -> None:
    checker = BudgetChecker(
        BudgetConfig(
            warning_usd=Decimal("0.05"),
            hard_limit_usd=Decimal("10"),
        )
    )
    rates = (Decimal("0.01"), Decimal("0.02"))
    decision = checker.check(
        TokenUsage(input_tokens=1000, output_tokens=2000),
        rates=rates,
    )
    assert decision.allowed is True
    assert decision.crossed_warning is True
    assert decision.exceeded_hard_limit is False


def test_check_at_hard_limit_blocks() -> None:
    checker = BudgetChecker(
        BudgetConfig(
            warning_usd=Decimal("0.01"),
            hard_limit_usd=Decimal("0.05"),
        )
    )
    rates = (Decimal("0.01"), Decimal("0.02"))
    decision = checker.check(
        TokenUsage(input_tokens=2000, output_tokens=1500),
        rates=rates,
    )
    assert decision.allowed is False
    assert decision.crossed_warning is True
    assert decision.exceeded_hard_limit is True


def test_check_over_hard_limit_blocks() -> None:
    checker = BudgetChecker(BudgetConfig(hard_limit_usd=Decimal("0.01")))
    rates = (Decimal("0.01"), Decimal("0.02"))
    decision = checker.check(
        TokenUsage(input_tokens=10000, output_tokens=10000),
        rates=rates,
    )
    assert decision.allowed is False
    assert decision.exceeded_hard_limit is True


def test_check_only_hard_limit_no_warning() -> None:
    checker = BudgetChecker(BudgetConfig(hard_limit_usd=Decimal("0.05")))
    rates = (Decimal("0.01"), Decimal("0.02"))
    decision = checker.check(
        TokenUsage(input_tokens=1000, output_tokens=1500),
        rates=rates,
    )
    assert decision.allowed is True
    assert decision.crossed_warning is False
    assert decision.exceeded_hard_limit is False


def test_check_only_warning_no_hard() -> None:
    checker = BudgetChecker(BudgetConfig(warning_usd=Decimal("0.01")))
    rates = (Decimal("0.01"), Decimal("0.02"))
    decision = checker.check(
        TokenUsage(input_tokens=1000, output_tokens=1000),
        rates=rates,
    )
    assert decision.allowed is True
    assert decision.crossed_warning is True
    assert decision.exceeded_hard_limit is False


def test_checker_with_default_config() -> None:
    checker = BudgetChecker()
    decision = checker.check(TokenUsage(input_tokens=100, output_tokens=100))
    assert decision.allowed is True
    assert decision.spent_usd is None


def test_resolve_budget_config_from_environ() -> None:
    config = resolve_budget_config(
        {
            "AVO_BUDGET_HARD_LIMIT_USD": "5.00",
            "AVO_BUDGET_WARNING_USD": "3.50",
        }
    )
    assert config.hard_limit_usd == Decimal("5.00")
    assert config.warning_usd == Decimal("3.50")


def test_resolve_budget_config_empty_returns_defaults() -> None:
    config = resolve_budget_config({})
    assert config.hard_limit_usd is None
    assert config.warning_usd is None
