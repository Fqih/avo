"""Tests for LoopSchedule and schedule parsing."""

from __future__ import annotations

import pytest

from avo.loop.schedule import LoopScheduleError, parse_schedule


def test_parse_seconds_and_units() -> None:
    assert parse_schedule("30s").interval_seconds == 30.0
    assert parse_schedule("5m").interval_seconds == 300.0
    assert parse_schedule("2h").interval_seconds == 7200.0
    assert parse_schedule("1d").interval_seconds == 86400.0
    assert parse_schedule("120").interval_seconds == 120.0


def test_parse_invalid_units_raises() -> None:
    with pytest.raises(LoopScheduleError, match="invalid duration"):
        parse_schedule("abc")

    with pytest.raises(LoopScheduleError, match="positive"):
        parse_schedule("0s")

    with pytest.raises(LoopScheduleError, match="positive"):
        parse_schedule("-5m")


def test_schedule_cron_expression() -> None:
    sched = parse_schedule("*/5 * * * *")
    assert sched.is_cron
    assert sched.cron_expr == "*/5 * * * *"


def test_schedule_delay_calculation() -> None:
    sched = parse_schedule("10s")
    delay = sched.compute_next_delay()
    assert 9.0 <= delay <= 11.0
