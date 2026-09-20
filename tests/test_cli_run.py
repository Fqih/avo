"""Tests for autonomous direct prompt CLI execution (`avo run`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from avo import ModelResponse, RunState
from avo.cli_run import run_cli_task
from avo.providers.fake import FakeProvider

ROOT = Path(__file__).parents[1]


@pytest.mark.asyncio
async def test_run_cli_task_executes_prompt_and_returns_result(tmp_path: Path) -> None:
    fake_provider = FakeProvider([ModelResponse(content="Task completed successfully.")])
    db_path = tmp_path / "test_runs.db"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = await run_cli_task(
        task="Explain what this repo does",
        workspace_root=workspace_root,
        database_path=db_path,
        provider=fake_provider,
    )

    assert result.status is RunState.COMPLETED
    assert result.output == "Task completed successfully."


@pytest.mark.asyncio
async def test_run_cli_task_with_worktree_isolation(tmp_path: Path) -> None:
    repo = tmp_path / "git_repo"
    repo.mkdir()
    subprocess.run(  # noqa: ASYNC221
        ["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(  # noqa: ASYNC221
        ["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(  # noqa: ASYNC221
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)  # noqa: ASYNC221
    subprocess.run(  # noqa: ASYNC221
        ["git", "commit", "-m", "initial commit"], cwd=repo, check=True, capture_output=True
    )

    fake_provider = FakeProvider([ModelResponse(content="Worktree task done.")])
    db_path = repo / "avo.db"

    result = await run_cli_task(
        task="Modify code in worktree",
        workspace_root=repo,
        database_path=db_path,
        provider=fake_provider,
        use_worktree=True,
        auto_merge=False,
    )

    assert result.status is RunState.COMPLETED
    assert result.output == "Worktree task done."


def test_main_maps_positional_prompt_to_run_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from avo.cli import _parser, main
    from avo.models import RunResult, TokenUsage
    from avo.state import StopReason

    parser = _parser()
    args, _ = parser.parse_known_args(["run", "my task prompt"])
    assert args.command == "run"
    assert args.prompt == "my task prompt"

    called_tasks: list[str] = []

    async def mock_run_cli_task(task: str, **_kwargs: object) -> RunResult:
        called_tasks.append(task)
        return RunResult(
            run_id="run-mock",
            status=RunState.COMPLETED,
            stop_reason=StopReason.COMPLETED,
            output="ok",
            steps=1,
            token_usage=TokenUsage(),
            token_accounting_available=True,
        )

    monkeypatch.setattr("avo.cli_run.run_cli_task", mock_run_cli_task)

    code = main(["my task prompt", "-d", str(tmp_path / "avo.db")])
    assert code == 0
    assert called_tasks == ["my task prompt"]
