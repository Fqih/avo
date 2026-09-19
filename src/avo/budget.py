"""Budget enforcement on top of :class:`UsageTracker`.

A budget caps cumulative spend per run. Two thresholds:

* ``warning_usd`` — emit a notification when crossed
* ``hard_limit_usd`` — block further provider calls when crossed

The checker does not enforce by itself — it is a pure decision function
called by the runtime before each provider call. Keeping it stateless
makes it trivial to test and to compose with retry/backoff helpers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from avo.exceptions import AvoError
from avo.models import TokenUsage
from avo.usage import estimate_cost

BudgetError = AvoError


@dataclass(frozen=True)
class BudgetConfig:
    """Budget envelope for a run."""

    warning_usd: Decimal | None = None
    hard_limit_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if self.warning_usd is not None and self.warning_usd < 0:
            raise BudgetError("warning_usd must be non-negative")
        if self.hard_limit_usd is not None and self.hard_limit_usd < 0:
            raise BudgetError("hard_limit_usd must be non-negative")
        if (
            self.warning_usd is not None
            and self.hard_limit_usd is not None
            and self.warning_usd > self.hard_limit_usd
        ):
            raise BudgetError("warning_usd must not exceed hard_limit_usd")

    @property
    def has_limits(self) -> bool:
        return self.warning_usd is not None or self.hard_limit_usd is not None


@dataclass(frozen=True)
class BudgetDecision:
    """Result of :meth:`BudgetChecker.check`."""

    allowed: bool
    spent_usd: Decimal | None
    crossed_warning: bool
    exceeded_hard_limit: bool


class BudgetChecker:
    """Stateless budget gate — call :meth:`check` per provider call."""

    __slots__ = ("_config",)

    def __init__(self, config: BudgetConfig | None = None) -> None:
        self._config = config or BudgetConfig()

    @property
    def config(self) -> BudgetConfig:
        return self._config

    def check(
        self, total: TokenUsage, *, rates: tuple[Decimal, Decimal] | None = None
    ) -> BudgetDecision:
        spent = estimate_cost(total, rates=rates)
        if spent is None:
            return BudgetDecision(
                allowed=True,
                spent_usd=None,
                crossed_warning=False,
                exceeded_hard_limit=False,
            )
        warning = self._config.warning_usd
        hard = self._config.hard_limit_usd
        crossed_warning = warning is not None and spent >= warning
        exceeded = hard is not None and spent >= hard
        return BudgetDecision(
            allowed=not exceeded,
            spent_usd=spent,
            crossed_warning=crossed_warning,
            exceeded_hard_limit=exceeded,
        )


def resolve_budget_config(environ: Mapping[str, str] | None = None) -> BudgetConfig:
    """Resolve budget limits from environment variables."""
    env = environ if environ is not None else os.environ
    hard_raw = (
        env.get("AVO_BUDGET_HARD_LIMIT_USD")
        or env.get("AVO_BUDGET_USD")
        or env.get("AVO_DAILY_BUDGET")
    )
    warn_raw = env.get("AVO_BUDGET_WARNING_USD")

    hard_val: Decimal | None = None
    if hard_raw and hard_raw.strip():
        try:
            hard_val = Decimal(hard_raw.strip())
        except (ArithmeticError, ValueError) as exc:
            raise BudgetError(f"invalid budget hard limit: {hard_raw!r}") from exc

    warn_val: Decimal | None = None
    if warn_raw and warn_raw.strip():
        try:
            warn_val = Decimal(warn_raw.strip())
        except (ArithmeticError, ValueError) as exc:
            raise BudgetError(f"invalid budget warning limit: {warn_raw!r}") from exc

    return BudgetConfig(warning_usd=warn_val, hard_limit_usd=hard_val)


__all__ = [
    "BudgetChecker",
    "BudgetConfig",
    "BudgetDecision",
    "BudgetError",
    "resolve_budget_config",
]
