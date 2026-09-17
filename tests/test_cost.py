"""Tests for the avo cost CLI."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from avo.cost import aggregate_costs
from avo.cost import main as cost_main
from avo.ledger import TokenLedger
from avo.models import TokenUsage


@pytest.fixture()
def ledger_path(tmp_path: Path) -> Path:
    path = tmp_path / "avo.db"
    ledger = TokenLedger(path)
    ledger.record(
        "run-a",
        step=1,
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        model="gpt-4o-mini",
        cost_usd=Decimal("0.0021"),
    )
    ledger.record(
        "run-a",
        step=2,
        usage=TokenUsage(input_tokens=80, output_tokens=40),
        model="gpt-4o-mini",
        cost_usd=Decimal("0.0017"),
    )
    ledger.record(
        "run-a",
        step=3,
        usage=TokenUsage(input_tokens=200, output_tokens=120),
        model="claude-haiku-4-5",
        cost_usd=Decimal("0.0044"),
    )
    ledger.record(
        "run-b",
        step=1,
        usage=TokenUsage(input_tokens=300, output_tokens=150),
        model="gpt-4o-mini",
        cost_usd=Decimal("0.0060"),
    )
    # Mix in one entry without USD to verify the (unknown) bucket handling.
    ledger.record(
        "run-b",
        step=2,
        usage=TokenUsage(input_tokens=20, output_tokens=10),
        model=None,
        cost_usd=None,
    )
    ledger.close()
    return path


def test_aggregate_returns_empty_when_database_missing(tmp_path: Path) -> None:
    report = aggregate_costs(tmp_path / "missing.db")
    assert report.run_count == 0
    assert report.total == TokenUsage()
    assert report.cost_usd is None
    assert report.runs == ()
    assert report.models == ()


def test_aggregate_totals_tokens_and_cost(ledger_path: Path) -> None:
    report = aggregate_costs(ledger_path)
    assert report.run_count == 2
    assert report.total.input_tokens == 700
    assert report.total.output_tokens == 370
    assert report.total.total_tokens == 1070
    expected = Decimal("0.0021") + Decimal("0.0017") + Decimal("0.0044") + Decimal("0.0060")
    assert report.cost_usd == expected


def test_aggregate_per_model_breakdown(ledger_path: Path) -> None:
    report = aggregate_costs(ledger_path)
    by_name = {m.model: m for m in report.models}
    gpt = by_name["gpt-4o-mini"]
    assert gpt.runs == 2
    assert gpt.usage.input_tokens == 480
    assert gpt.usage.output_tokens == 240
    assert gpt.cost_usd == Decimal("0.0098")
    claude = by_name["claude-haiku-4-5"]
    assert claude.runs == 1
    assert claude.usage.input_tokens == 200
    assert claude.usage.output_tokens == 120
    unknown = by_name["(unknown)"]
    assert unknown.runs == 1
    assert unknown.cost_usd is None
    assert unknown.usage.input_tokens == 20


def test_aggregate_per_run_breakdown(ledger_path: Path) -> None:
    report = aggregate_costs(ledger_path)
    by_id = {r.run_id: r for r in report.runs}
    run_a = by_id["run-a"]
    assert run_a.entries == 3
    assert run_a.cost_usd == Decimal("0.0082")
    assert {m.model for m in run_a.by_model} == {"gpt-4o-mini", "claude-haiku-4-5"}


def test_text_output_includes_models_and_runs(
    capsys: pytest.CaptureFixture[str], ledger_path: Path
) -> None:
    rc = cost_main(["--database", str(ledger_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Runs: 2" in out
    assert "Per-model:" in out
    assert "Per-run:" in out
    assert "gpt-4o-mini" in out
    assert "run-a" in out


def test_cost_main_uses_database_path_from_environment_when_option_is_omitted(
    tmp_path: Path,
    ledger_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AVO_DATABASE_PATH", str(ledger_path))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert cost_main([]) == 0
    output = capsys.readouterr().out
    assert f"Database: {ledger_path}" in output
    assert "Runs: 2" in output


def test_cost_main_explicit_database_option_wins_over_environment(
    tmp_path: Path,
    ledger_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AVO_DATABASE_PATH", str(tmp_path / "environment.db"))

    assert cost_main(["--database", str(ledger_path)]) == 0
    output = capsys.readouterr().out
    assert f"Database: {ledger_path}" in output
    assert "Runs: 2" in output


def test_json_output_emits_decimal_as_string(ledger_path: Path) -> None:
    rc = cost_main(["--database", str(ledger_path), "--json"])
    assert rc == 0
    # Re-run via main with stdout captured
    import io
    import sys

    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        cost_main(["--database", str(ledger_path), "--json"])
    finally:
        sys.stdout = real_stdout
    payload = json.loads(buf.getvalue())
    assert payload["run_count"] == 2
    assert payload["cost_usd"] == "0.0142"
    model_names = sorted(m["model"] for m in payload["models"])
    assert model_names == ["(unknown)", "claude-haiku-4-5", "gpt-4o-mini"]
    gpt = next(m for m in payload["models"] if m["model"] == "gpt-4o-mini")
    assert gpt["cost_usd"] == "0.0098"
    assert gpt["usage"]["input_tokens"] == 480


def test_help_lists_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cost_main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--database" in out
    assert "--json" in out
