"""Cost aggregation CLI.

Reads the persistent :class:`~avo.ledger.TokenLedger` and reports the
total tokens and USD spend aggregated across all recorded runs, with
per-run and per-model breakdowns. Output supports both a human-readable
table and machine-readable JSON (``--json``).

The ledger is colocated with the event store at ``$AVO_DATABASE_PATH``
by convention; pass ``--database`` to override.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from avo.ledger import TokenLedger
from avo.models import TokenUsage


@dataclass(frozen=True)
class ModelCost:
    """Aggregated cost for a single model across all runs."""

    model: str
    runs: int
    usage: TokenUsage
    cost_usd: Decimal | None


@dataclass(frozen=True)
class RunCost:
    """Aggregated cost for a single run."""

    run_id: str
    entries: int
    usage: TokenUsage
    cost_usd: Decimal | None
    by_model: tuple[ModelCost, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CostReport:
    """Top-level cost rollup covering every recorded run."""

    database: str
    run_count: int
    total: TokenUsage
    cost_usd: Decimal | None
    runs: tuple[RunCost, ...] = ()
    models: tuple[ModelCost, ...] = ()

    def to_text(self) -> str:
        lines: list[str] = []
        lines.append(f"Database: {self.database}")
        lines.append(f"Runs: {self.run_count}")
        lines.append(
            f"Tokens: {self.total.total_tokens:,} "
            f"(input={self.total.input_tokens:,}, output={self.total.output_tokens:,})"
        )
        if self.cost_usd is None:
            lines.append("Cost:   (no USD entries recorded)")
        else:
            lines.append(f"Cost:   ${self.cost_usd}")
        if self.models:
            lines.append("")
            lines.append("Per-model:")
            lines.append(f"  {'MODEL':32}  {'RUNS':>5}  {'IN TOK':>12}  {'OUT TOK':>12}  USD")
            for model_cost in self.models:
                cost_str = f"${model_cost.cost_usd}" if model_cost.cost_usd is not None else "-"
                lines.append(
                    f"  {model_cost.model or '(unknown)':32}  "
                    f"{model_cost.runs:>5}  "
                    f"{model_cost.usage.input_tokens:>12,}  "
                    f"{model_cost.usage.output_tokens:>12,}  "
                    f"{cost_str}"
                )
        if self.runs:
            lines.append("")
            lines.append("Per-run:")
            lines.append(f"  {'RUN ID':36}  {'ENTRIES':>7}  {'TOKENS':>12}  USD")
            for run_cost in self.runs:
                cost_str = f"${run_cost.cost_usd}" if run_cost.cost_usd is not None else "-"
                lines.append(
                    f"  {run_cost.run_id:36}  {run_cost.entries:>7}  "
                    f"{run_cost.usage.total_tokens:>12,}  {cost_str}"
                )
        lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(_report_to_jsonable(self), indent=2, sort_keys=True)


def _report_to_jsonable(report: CostReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["total"] = _usage_to_dict(report.total)
    payload["cost_usd"] = str(report.cost_usd) if report.cost_usd is not None else None
    payload["runs"] = [_run_to_dict(run) for run in report.runs]
    payload["models"] = [_model_to_dict(model) for model in report.models]
    return payload


def report_to_dict(report: CostReport) -> dict[str, Any]:
    """Convert a :class:`CostReport` to a JSON-serializable dictionary."""
    return _report_to_jsonable(report)


def _run_to_dict(run: RunCost) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "entries": run.entries,
        "usage": _usage_to_dict(run.usage),
        "cost_usd": str(run.cost_usd) if run.cost_usd is not None else None,
        "by_model": [_model_to_dict(m) for m in run.by_model],
    }


def _model_to_dict(model: ModelCost) -> dict[str, Any]:
    return {
        "model": model.model,
        "runs": model.runs,
        "usage": _usage_to_dict(model.usage),
        "cost_usd": str(model.cost_usd) if model.cost_usd is not None else None,
    }


def _usage_to_dict(usage: TokenUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


def _list_run_ids(ledger: TokenLedger) -> tuple[str, ...]:
    """List every run_id with at least one ledger entry."""

    conn: sqlite3.Connection = ledger._connection
    try:
        rows = conn.execute("SELECT DISTINCT run_id FROM ledger_entries ORDER BY run_id").fetchall()
    except sqlite3.Error:
        return ()
    return tuple(str(row["run_id"]) for row in rows)


def aggregate_costs(database: Path | str) -> CostReport:
    """Aggregate every recorded ledger entry in ``database``.

    Returns a :class:`CostReport` with empty totals when the database
    does not exist or has no ledger table — this keeps the CLI exit
    status zero in the common "no runs yet" case.
    """

    path = Path(database)
    if not path.exists():
        return CostReport(
            database=str(path),
            run_count=0,
            total=TokenUsage(),
            cost_usd=None,
        )
    ledger = TokenLedger(path)
    try:
        run_ids = _list_run_ids(ledger)
        run_reports: list[RunCost] = []
        model_aggregate: dict[str | None, dict[str, Any]] = {}
        grand_usage = TokenUsage()
        grand_cost = Decimal("0")
        grand_cost_seen = False
        for run_id in run_ids:
            entries = ledger.entries(run_id)
            if not entries:
                continue
            run_usage = TokenUsage()
            run_cost = Decimal("0")
            run_cost_seen = False
            per_model: dict[str | None, dict[str, Any]] = {}
            for entry in entries:
                run_usage = run_usage.plus(entry.usage)
                if entry.cost_usd is not None:
                    run_cost += entry.cost_usd
                    run_cost_seen = True
                slot = per_model.setdefault(
                    entry.model,
                    {"usage": TokenUsage(), "runs": 1, "cost": Decimal("0"), "cost_seen": False},
                )
                slot["usage"] = slot["usage"].plus(entry.usage)
                if entry.cost_usd is not None:
                    slot["cost"] += entry.cost_usd
                    slot["cost_seen"] = True
            grand_usage = grand_usage.plus(run_usage)
            if run_cost_seen:
                grand_cost += run_cost
                grand_cost_seen = True
            by_model = tuple(
                ModelCost(
                    model=str(model) if model is not None else "(unknown)",
                    runs=slot["runs"],
                    usage=slot["usage"],
                    cost_usd=slot["cost"] if slot["cost_seen"] else None,
                )
                for model, slot in sorted(
                    per_model.items(), key=lambda kv: (kv[0] is None, str(kv[0]))
                )
            )
            run_reports.append(
                RunCost(
                    run_id=run_id,
                    entries=len(entries),
                    usage=run_usage,
                    cost_usd=run_cost if run_cost_seen else None,
                    by_model=by_model,
                )
            )
            for model, slot in per_model.items():
                agg = model_aggregate.setdefault(
                    model,
                    {"usage": TokenUsage(), "runs": 0, "cost": Decimal("0"), "cost_seen": False},
                )
                agg["usage"] = agg["usage"].plus(slot["usage"])
                agg["runs"] += 1
                if slot["cost"] is not None and slot["cost_seen"]:
                    agg["cost"] += slot["cost"]
                    agg["cost_seen"] = True
        model_costs = tuple(
            ModelCost(
                model=str(model) if model is not None else "(unknown)",
                runs=agg["runs"],
                usage=agg["usage"],
                cost_usd=agg["cost"] if agg["cost_seen"] else None,
            )
            for model, agg in sorted(
                model_aggregate.items(), key=lambda kv: (kv[0] is None, str(kv[0]))
            )
        )
        return CostReport(
            database=str(path),
            run_count=len(run_reports),
            total=grand_usage,
            cost_usd=grand_cost if grand_cost_seen else None,
            runs=tuple(run_reports),
            models=model_costs,
        )
    finally:
        ledger.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo cost",
        description="Aggregate token usage and USD cost across recorded Avo runs.",
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=Path("avo.db"),
        help="SQLite database path (default: avo.db).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable table.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = aggregate_costs(args.database)
    if args.json:
        sys.stdout.write(report.to_json() + "\n")
    else:
        sys.stdout.write(report.to_text())
    return 0


__all__ = [
    "CostReport",
    "ModelCost",
    "RunCost",
    "aggregate_costs",
    "main",
    "report_to_dict",
]


if __name__ == "__main__":  # pragma: no cover - module CLI entrypoint
    raise SystemExit(main())
