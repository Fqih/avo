"""Schedule parsing and delay computation for autonomous loops."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from avo.exceptions import AvoError

_DURATION_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*([smhd])?$", re.IGNORECASE)
_UNIT_MULTIPLIERS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    None: 1.0,
}


class LoopScheduleError(AvoError):
    """Raised when loop schedule syntax is invalid."""


@dataclass(frozen=True)
class LoopSchedule:
    """Parsed schedule defining the cadence of an autonomous loop."""

    interval_seconds: float | None = None
    cron_expr: str | None = None

    @property
    def is_cron(self) -> bool:
        return self.cron_expr is not None

    def compute_next_delay(self, now: datetime | None = None) -> float:
        """Compute seconds to sleep until the next run."""
        if self.interval_seconds is not None:
            return self.interval_seconds

        # Simple cron evaluation or fallback
        if self.cron_expr:
            # When croniter is not installed, fallback to 60s minimum interval
            try:
                import croniter  # type: ignore[import-untyped]

                current_time = now or datetime.now(UTC)
                iter_cron = croniter.croniter(self.cron_expr, current_time)
                next_time = iter_cron.get_next(datetime)
                delay = (next_time - current_time).total_seconds()
                return float(max(1.0, delay))
            except (ImportError, Exception):
                return 60.0

        return 60.0


def parse_schedule(expression: str) -> LoopSchedule:
    """Parse a schedule string into a :class:`LoopSchedule`.

    Accepts durations (e.g. ``"30s"``, ``"5m"``, ``"2h"``, ``"1d"``, ``"120"``)
    or standard 5-part cron expressions (e.g. ``"*/5 * * * *"``).
    """
    expr = expression.strip()
    if not expr:
        raise LoopScheduleError("schedule expression cannot be empty")

    # Check for cron expression (5 space-separated parts)
    parts = expr.split()
    if len(parts) == 5:
        return LoopSchedule(cron_expr=expr)

    # Check for duration
    match = _DURATION_RE.match(expr)
    if not match:
        raise LoopScheduleError(f"invalid duration or schedule expression: {expr!r}")

    value_str, unit = match.groups()
    try:
        val = float(value_str)
    except ValueError as exc:
        raise LoopScheduleError(f"invalid number in duration: {value_str!r}") from exc

    if val <= 0:
        raise LoopScheduleError(f"duration must be positive; got {val}")

    unit_key = unit.lower() if unit else None
    mult = _UNIT_MULTIPLIERS.get(unit_key, 1.0)
    total_seconds = val * mult

    return LoopSchedule(interval_seconds=total_seconds)
