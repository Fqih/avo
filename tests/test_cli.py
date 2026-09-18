"""CLI list, inspect, error, and fake-provider resume behavior."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from avo import AgentRuntime, EventType, ModelResponse
from avo.cli import main
from avo.providers import FakeProvider
from avo.storage import SQLiteEventStore
from tests.test_resume import InjectedInterruption, InterruptOnEventRuntime


def _seed_completed_run(path: Path, run_id: str) -> None:
    async def seed() -> None:
        store = SQLiteEventStore(path)
        runtime = AgentRuntime(
            provider=FakeProvider([ModelResponse(content="cli output")]),
            event_store=store,
        )
        await runtime.run("cli task", run_id=run_id)
        await store.close()

    asyncio.run(seed())


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

    assert main(["--database", str(path), "replay", run_id]) == 0
    replayed = capsys.readouterr().out
    assert "Replay verified" in replayed
    assert run_id in replayed

    assert main(["--database", str(path), "replay", run_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True

    assert main(["--database", str(path), "runs", "replay", run_id]) == 0
    assert "Replay verified" in capsys.readouterr().out


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


def test_cli_uses_database_path_from_environment_when_option_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = tmp_path / "environment.db"
    _seed_completed_run(env_path, "environment-run")
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert main(["runs", "list"]) == 0
    assert "environment-run" in capsys.readouterr().out


def test_cli_explicit_database_option_wins_over_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = tmp_path / "environment.db"
    explicit_path = tmp_path / "explicit.db"
    _seed_completed_run(env_path, "environment-run")
    _seed_completed_run(explicit_path, "explicit-run")
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))

    assert main(["--database", str(explicit_path), "runs", "list"]) == 0
    output = capsys.readouterr().out
    assert "explicit-run" in output
    assert "environment-run" not in output


def test_cli_chat_entry_point_preserves_global_database_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / "environment.db"
    explicit_path = tmp_path / "explicit.db"
    selected_paths: list[Path] = []

    async def capture_database_path(*, database_path: Path, **_: object) -> int:
        selected_paths.append(database_path)
        return 0

    monkeypatch.setattr("avo.cli.run_repl", capture_database_path)
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))

    assert main(["--database", str(explicit_path), "chat", "--new-session"]) == 0
    assert main(["chat", "--new-session"]) == 0
    assert selected_paths == [explicit_path, env_path]


def test_cli_cost_entry_point_forwards_global_explicit_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = tmp_path / "environment.db"
    explicit_path = tmp_path / "explicit.db"
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))
    monkeypatch.setattr(sys, "argv", ["avo", "--unrelated-process-argument"])

    assert main(["--database", str(explicit_path), "cost", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["database"] == str(explicit_path)


def test_cli_cost_entry_point_uses_environment_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = tmp_path / "environment.db"
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))
    monkeypatch.setattr(sys, "argv", ["avo", "--unrelated-process-argument"])

    assert main(["cost", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["database"] == str(env_path)


def test_cli_runs_diff_entry_point_preserves_global_database_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / "environment.db"
    explicit_path = tmp_path / "explicit.db"
    selected_paths: list[Path] = []

    class CapturedReport:
        @staticmethod
        def to_text() -> str:
            return "captured\n"

    def capture_store(store: SQLiteEventStore, *, run_a: str, run_b: str) -> CapturedReport:
        assert (run_a, run_b) in {
            ("explicit-a", "explicit-b"),
            ("environment-a", "environment-b"),
        }
        selected_paths.append(store.path)
        return CapturedReport()

    monkeypatch.setattr("avo.diff.diff_runs", capture_store)
    monkeypatch.setenv("AVO_DATABASE_PATH", str(env_path))

    assert (
        main(
            [
                "--database",
                str(explicit_path),
                "runs",
                "diff",
                "explicit-a",
                "explicit-b",
            ]
        )
        == 0
    )
    assert main(["runs", "diff", "environment-a", "environment-b"]) == 0
    assert selected_paths == [explicit_path, env_path]


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    from avo.cli import main as cli_main

    with pytest.raises(SystemExit) as exc:
        cli_main(["--version"])
    assert exc.value.code == 0
    from avo import __version__

    out = capsys.readouterr().out.strip()
    assert out == f"avo {__version__}"


def test_cli_without_command_starts_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_run_repl(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("avo.cli.run_repl", fake_run_repl)

    assert main([]) == 0
    assert len(calls) == 1
    assert calls[0]["force_new_session"] is False
    assert calls[0]["resume_latest"] is False


def test_cli_resume_command_starts_explicit_resume_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_run_repl(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("avo.cli.run_repl", fake_run_repl)

    assert main(["resume"]) == 0
    assert calls[0]["resume_latest"] is True
    assert calls[0]["session_id"] is None

    calls.clear()
    assert main(["resume", "session-123"]) == 0
    assert calls[0]["resume_latest"] is False
    assert calls[0]["session_id"] == "session-123"


def test_cli_help_explains_default_chat_and_core_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "avo                 Start chat" in output
    assert "avo setup" in output
    assert "avo login" in output
    assert "avo models" in output
    assert "avo doctor" in output


def test_cli_chat_help_lists_session_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["chat", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "--workspace-root" in output
    assert "--session" in output
    assert "--new-session" in output


def test_cli_login_status(capsys: pytest.CaptureFixture[str]) -> None:
    from avo.cli import main as cli_main

    assert cli_main(["login", "--status"]) == 0
    assert "authenticated" in capsys.readouterr().out.lower()
