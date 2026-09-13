"""CLI list, inspect, error, and fake-provider resume behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from avo import AgentRuntime, EventType, ModelResponse
from avo.cli import main
from avo.providers import FakeProvider
from avo.storage import SQLiteEventStore
from tests.test_resume import InjectedInterruption, InterruptOnEventRuntime


def test_cli_lists_and_inspects_sqlite_runs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "cli.db"

    async def seed() -> str:
        store = SQLiteEventStore(path)
        runtime = AgentRuntime(
            provider=FakeProvider([ModelResponse(content="cli output")]),
            event_store=store,
        )
        result = await runtime.run("cli task", run_id="cli-run")
        await store.close()
        return result.run_id

    run_id = asyncio.run(seed())

    assert main(["--database", str(path), "runs", "list"]) == 0
    listed = capsys.readouterr().out
    assert run_id in listed
    assert "completed" in listed

    assert main(["--database", str(path), "runs", "inspect", run_id]) == 0
    inspected = capsys.readouterr().out
    assert "Stop reason: completed" in inspected
    assert "model_responded" in inspected


def test_cli_invalid_run_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "missing.db"

    assert main(["--database", str(path), "runs", "inspect", "missing"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_cli_resumes_persisted_fake_provider_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "resume-cli.db"

    async def interrupt() -> None:
        store = SQLiteEventStore(path)
        runtime = InterruptOnEventRuntime(
            provider=FakeProvider([ModelResponse(content="replayed final")]),
            event_store=store,
            interrupt_event=EventType.MODEL_RESPONDED,
        )
        with pytest.raises(InjectedInterruption):
            await runtime.run("resume from cli", run_id="cli-resume")
        await store.close()

    asyncio.run(interrupt())

    assert main(["--database", str(path), "runs", "resume", "cli-resume"]) == 0
    output = capsys.readouterr().out
    assert "completed (completed)" in output


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    from avo.cli import main as cli_main

    with pytest.raises(SystemExit) as exc:
        cli_main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    assert out == "avo 0.1.3"


def test_cli_login_status(capsys: pytest.CaptureFixture[str]) -> None:
    from avo.cli import main as cli_main

    assert cli_main(["login", "--status"]) == 0
    assert "authenticated" in capsys.readouterr().out.lower()
