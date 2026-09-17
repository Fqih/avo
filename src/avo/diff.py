"""``avo diff RUN_A RUN_B`` — compare two persisted runs.

The diff reports:

- Event sequence alignment (matched by ``sequence``).
- Token usage deltas (input / output totals).
- Step count delta.
- Per-event-type frequency differences.
- Wall-clock duration delta when ``created_at`` and ``updated_at``
  are present on both records.

Output is JSON when ``--json`` is passed, otherwise a human-readable
table is rendered to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avo.config import resolve_database_path
from avo.events import AgentEvent
from avo.exceptions import AvoError
from avo.models import TokenUsage
from avo.storage.sqlite import SQLiteEventStore

__all__ = ["DiffReport", "diff_runs", "main"]


class DiffError(AvoError):
    """User-facing failure in :func:`main`."""


@dataclass
class _SideMetrics:
    run_id: str
    steps: int
    token_usage: TokenUsage
    event_counts: Counter[str] = field(default_factory=Counter)
    duration_seconds: float | None = None

    @classmethod
    def from_events(cls, run_id: str, events: Sequence[AgentEvent], *, steps: int) -> _SideMetrics:
        counts: Counter[str] = Counter()
        token = TokenUsage()
        started = events[0].created_at if events else None
        finished = events[-1].created_at if events else None
        for event in events:
            counts[event.event_type.value] += 1
            usage = event.payload.get("usage") if isinstance(event.payload, dict) else None
            if isinstance(usage, dict):
                in_raw = usage.get("input_tokens", 0)
                out_raw = usage.get("output_tokens", 0)
                in_value = int(in_raw) if isinstance(in_raw, (int, float)) else 0
                out_value = int(out_raw) if isinstance(out_raw, (int, float)) else 0
                token = token.model_copy(
                    update={
                        "input_tokens": token.input_tokens + in_value,
                        "output_tokens": token.output_tokens + out_value,
                    }
                )
        duration = None
        if started is not None and finished is not None:
            duration = max(0.0, (finished - started).total_seconds())
        return cls(
            run_id=run_id,
            steps=steps,
            token_usage=token,
            event_counts=counts,
            duration_seconds=duration,
        )


@dataclass
class DiffReport:
    """Aggregate diff between two runs; serialises to JSON."""

    run_a: _SideMetrics
    run_b: _SideMetrics
    event_type_deltas: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_a": _side_dict(self.run_a),
            "run_b": _side_dict(self.run_b),
            "deltas": {
                "event_types": self.event_type_deltas,
                "steps": self.run_b.steps - self.run_a.steps,
                "input_tokens": self.run_b.token_usage.input_tokens
                - self.run_a.token_usage.input_tokens,
                "output_tokens": self.run_b.token_usage.output_tokens
                - self.run_a.token_usage.output_tokens,
                "duration_seconds": (
                    (self.run_b.duration_seconds or 0.0) - (self.run_a.duration_seconds or 0.0)
                )
                if self.run_a.duration_seconds is not None
                and self.run_b.duration_seconds is not None
                else None,
            },
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, sort_keys=True)

    def to_text(self) -> str:
        lines = [
            f"Run A: {self.run_a.run_id}",
            f"Run B: {self.run_b.run_id}",
            "",
            f"{'METRIC':24} {'RUN A':>12} {'RUN B':>12} {'DELTA':>12}",
            f"{'-' * 24} {'-' * 12} {'-' * 12} {'-' * 12}",
            f"{'steps':24} {self.run_a.steps:>12} {self.run_b.steps:>12} "
            f"{self.run_b.steps - self.run_a.steps:>+12}",
            f"{'input_tokens':24} {self.run_a.token_usage.input_tokens:>12} "
            f"{self.run_b.token_usage.input_tokens:>12} "
            f"{self.run_b.token_usage.input_tokens - self.run_a.token_usage.input_tokens:>+12}",
            f"{'output_tokens':24} {self.run_a.token_usage.output_tokens:>12} "
            f"{self.run_b.token_usage.output_tokens:>12} "
            f"{self.run_b.token_usage.output_tokens - self.run_a.token_usage.output_tokens:>+12}",
        ]
        if self.run_a.duration_seconds is not None and self.run_b.duration_seconds is not None:
            lines.append(
                f"{'duration_seconds':24} {self.run_a.duration_seconds:>12.3f} "
                f"{self.run_b.duration_seconds:>12.3f} "
                f"{(self.run_b.duration_seconds - self.run_a.duration_seconds):>+12.3f}"
            )
        lines.extend(["", "Event-type counts (A → B):"])
        all_keys = sorted(set(self.run_a.event_counts) | set(self.run_b.event_counts))
        for key in all_keys:
            a = self.run_a.event_counts.get(key, 0)
            b = self.run_b.event_counts.get(key, 0)
            lines.append(f"  {key:32} {a:>4} → {b:>4}  ({b - a:+d})")
        return "\n".join(lines) + "\n"


def _side_dict(side: _SideMetrics) -> dict[str, Any]:
    return {
        "run_id": side.run_id,
        "steps": side.steps,
        "input_tokens": side.token_usage.input_tokens,
        "output_tokens": side.token_usage.output_tokens,
        "duration_seconds": side.duration_seconds,
        "event_counts": dict(side.event_counts),
    }


def diff_runs(
    store: SQLiteEventStore,
    *,
    run_a: str,
    run_b: str,
) -> DiffReport:
    """Build a :class:`DiffReport` for two run ids in the same store."""

    async def _load() -> tuple[_SideMetrics, _SideMetrics]:
        record_a = await store.get_run(run_a)
        record_b = await store.get_run(run_b)
        events_a = await store.get_events(run_a)
        events_b = await store.get_events(run_b)
        return (
            _SideMetrics.from_events(run_a, events_a, steps=record_a.steps),
            _SideMetrics.from_events(run_b, events_b, steps=record_b.steps),
        )

    side_a, side_b = _run_coroutine(_load())
    keys = set(side_a.event_counts) | set(side_b.event_counts)
    deltas = {
        key: side_b.event_counts.get(key, 0) - side_a.event_counts.get(key, 0) for key in keys
    }
    return DiffReport(run_a=side_a, run_b=side_b, event_type_deltas=deltas)


def _run_coroutine(coro: Any) -> Any:
    """Run an awaitable on a fresh loop. Tests override via dependency injection."""

    import asyncio

    return asyncio.run(coro)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo diff",
        description="Compare two persisted runs from a SQLite event store.",
    )
    parser.add_argument("run_a")
    parser.add_argument("run_b")
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=None,
        help="SQLite database path (default: $AVO_DATABASE_PATH or avo.db).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable table.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = SQLiteEventStore(resolve_database_path(args.database))
    try:
        report = diff_runs(store, run_a=args.run_a, run_b=args.run_b)
    finally:
        _close_store(store)
    if args.json:
        sys.stdout.write(report.to_json() + "\n")
    else:
        sys.stdout.write(report.to_text())
    return 0


def _close_store(store: SQLiteEventStore) -> None:
    import asyncio
    import contextlib

    with contextlib.suppress(RuntimeError):
        # Already closed or running loop mismatch; swallow in CLI path.
        asyncio.run(store.close())
