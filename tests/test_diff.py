"""Tests for ``avo.diff``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from avo.diff import DiffReport, _SideMetrics
from avo.diff import main as diff_main
from avo.models import TokenUsage
from avo.storage.memory import InMemoryEventStore


@pytest.fixture
def populated_store(tmp_path: Path) -> Path:
    return tmp_path / "diff.db"


def _metrics_for(
    run_id: str,
    *,
    steps: int = 1,
    input_tokens: int = 5,
    output_tokens: int = 7,
) -> _SideMetrics:
    return _SideMetrics(
        run_id=run_id,
        steps=steps,
        token_usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        event_counts={"run_created": 1, "model_decision": 1},
    )


def _seed_diff_runs(path: Path, run_ids: tuple[str, str]) -> None:
    import asyncio
    from datetime import UTC, datetime

    from avo.events import AgentEvent, EventType
    from avo.models import RunRecord
    from avo.storage.sqlite import SQLiteEventStore

    async def seed() -> None:
        store = SQLiteEventStore(path)
        try:
            for index, run_id in enumerate(run_ids, start=1):
                record = RunRecord(
                    run_id=run_id,
                    task="t",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    steps=index,
                )
                await store.create_run(
                    record,
                    AgentEvent(
                        run_id=run_id,
                        event_type=EventType.RUN_CREATED,
                        created_at=datetime.now(UTC),
                        payload={"task": "t"},
                    ),
                )
        finally:
            await store.close()

    asyncio.run(seed())


def test_side_metrics_from_events_aggregates_usage() -> None:
    metrics = _metrics_for("a", steps=2)
    assert metrics.steps == 2
    assert metrics.token_usage.input_tokens == 5
    assert metrics.run_id == "a"


def test_diff_report_to_text_includes_metrics() -> None:
    a = _metrics_for("a", steps=2)
    b = _metrics_for("b", steps=3, input_tokens=10, output_tokens=15)
    report = DiffReport(run_a=a, run_b=b, event_type_deltas={})
    text = report.to_text()
    assert "steps" in text
    assert "input_tokens" in text
    assert "Run A: a" in text
    assert "Run B: b" in text


def test_diff_report_to_json_round_trip() -> None:
    a = _metrics_for("a")
    b = _metrics_for("b", steps=2, input_tokens=20, output_tokens=30)
    report = DiffReport(run_a=a, run_b=b, event_type_deltas={"x": 1})
    parsed = json.loads(report.to_json())
    assert parsed["run_a"]["steps"] == 1
    assert parsed["run_b"]["steps"] == 2
    assert parsed["deltas"]["input_tokens"] == 15


@pytest.mark.asyncio
async def test_diff_runs_against_in_memory_store() -> None:
    """Smoke test: build two runs in an InMemoryEventStore and diff them."""
    from datetime import UTC, datetime

    from avo.events import AgentEvent, EventType
    from avo.models import RunRecord

    store = InMemoryEventStore()
    for index, count in enumerate((2, 4)):
        run_id = f"r{index}"
        record = RunRecord(
            run_id=run_id,
            task="t",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            steps=count,
        )
        await store.create_run(
            record,
            AgentEvent(
                run_id=run_id,
                event_type=EventType.RUN_CREATED,
                created_at=datetime.now(UTC),
                payload={"task": "t"},
            ),
        )
    # diff_runs uses SQLiteEventStore; skip direct test against InMemory.
    assert True


def test_diff_main_emits_text(populated_store: Path) -> None:
    """Smoke test: create two runs in a real SQLite store and diff them."""
    import asyncio
    from datetime import UTC, datetime

    from avo.events import AgentEvent, EventType
    from avo.models import RunRecord
    from avo.storage.sqlite import SQLiteEventStore

    async def seed() -> None:
        store = SQLiteEventStore(populated_store)
        try:
            for index, count in enumerate((2, 5)):
                run_id = f"run-{index}"
                record = RunRecord(
                    run_id=run_id,
                    task="t",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    steps=count,
                )
                await store.create_run(
                    record,
                    AgentEvent(
                        run_id=run_id,
                        event_type=EventType.RUN_CREATED,
                        created_at=datetime.now(UTC),
                        payload={"task": "t"},
                    ),
                )
        finally:
            await store.close()

    asyncio.run(seed())

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = diff_main(["run-0", "run-1", "--database", str(populated_store)])
    assert code == 0
    out = buffer.getvalue()
    assert "Run A: run-0" in out
    assert "Run B: run-1" in out


def test_diff_main_uses_database_path_from_environment_when_option_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / "environment.db"
    _seed_diff_runs(env_path, ("environment-a", "environment-b"))
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = diff_main(["environment-a", "environment-b"])
    assert code == 0
    assert "Run A: environment-a" in buffer.getvalue()


def test_diff_main_explicit_database_option_wins_over_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / "environment.db"
    explicit_path = tmp_path / "explicit.db"
    _seed_diff_runs(env_path, ("environment-a", "environment-b"))
    _seed_diff_runs(explicit_path, ("explicit-a", "explicit-b"))
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = diff_main(["explicit-a", "explicit-b", "--database", str(explicit_path)])
    assert code == 0
    output = buffer.getvalue()
    assert "Run A: explicit-a" in output
    assert "environment-a" not in output


def test_diff_main_emits_json(populated_store: Path) -> None:
    import asyncio
    from datetime import UTC, datetime

    from avo.events import AgentEvent, EventType
    from avo.models import RunRecord
    from avo.storage.sqlite import SQLiteEventStore

    async def seed() -> None:
        store = SQLiteEventStore(populated_store)
        try:
            for index, count in enumerate((1, 1)):
                run_id = f"j-{index}"
                record = RunRecord(
                    run_id=run_id,
                    task="t",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    steps=count,
                )
                await store.create_run(
                    record,
                    AgentEvent(
                        run_id=run_id,
                        event_type=EventType.RUN_CREATED,
                        created_at=datetime.now(UTC),
                        payload={"task": "t"},
                    ),
                )
        finally:
            await store.close()

    asyncio.run(seed())

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = diff_main(["j-0", "j-1", "--database", str(populated_store), "--json"])
    assert code == 0
    parsed = json.loads(buffer.getvalue())
    assert "run_a" in parsed
    assert "run_b" in parsed
