"""Tests for autonomous direct prompt CLI execution (`avo run`)."""

from __future__ import annotations

import io
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


@pytest.mark.asyncio
async def test_run_cli_task_with_worktree_auto_merge(tmp_path: Path) -> None:
    repo = tmp_path / "git_repo_merge"
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

    fake_provider = FakeProvider([ModelResponse(content="Auto merge task done.")])
    db_path = repo / "avo.db"

    result = await run_cli_task(
        task="Modify code with auto merge",
        workspace_root=repo,
        database_path=db_path,
        provider=fake_provider,
        use_worktree=True,
        auto_merge=True,
    )

    assert result.status is RunState.COMPLETED
    assert result.output == "Auto merge task done."


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
        del _kwargs
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


@pytest.mark.asyncio
async def test_build_cli_progress_hooks_renders_badges() -> None:
    import io

    from avo.cli_run import build_cli_progress_hooks
    from avo.hooks import HookContext, HookEvent
    from avo.models import ToolCall, ToolResult

    out = io.StringIO()
    hooks = build_cli_progress_hooks(out, color=False)

    call = ToolCall(name="read_file", arguments={"path": "src/avo/cli.py"})
    res = ToolResult(
        tool_call_id="call-1",
        tool_name="read_file",
        success=True,
        output="content",
    )

    # Fire pre-tool hook
    ctx_pre = HookContext(event=HookEvent.PRE_TOOL_USE, run_id="run-1", tool_call=call)
    await hooks.fire(ctx_pre)
    assert "tool: read_file" in out.getvalue()
    assert "path='src/avo/cli.py'" in out.getvalue()

    # Fire post-tool hook
    ctx_post = HookContext(
        event=HookEvent.POST_TOOL_USE, run_id="run-1", tool_call=call, tool_result=res
    )
    await hooks.fire(ctx_post)
    assert "read_file completed" in out.getvalue()


@pytest.mark.asyncio
async def test_run_cli_task_executes_file_tools_end_to_end(tmp_path: Path) -> None:
    from avo.models import ToolCall

    workspace = tmp_path / "ws_tools"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("Hello Avo Workspace!", encoding="utf-8")

    # Step 1: Model calls read_file
    # Step 2: Model calls write_file
    # Step 3: Model finishes
    provider = FakeProvider(
        [
            ModelResponse(
                tool_call=ToolCall(name="read_file", arguments={"path": "sample.txt"}),
            ),
            ModelResponse(
                tool_call=ToolCall(
                    name="write_file",
                    arguments={"path": "out.txt", "content": "Updated content"},
                ),
            ),
            ModelResponse(content="Task completed successfully."),
        ]
    )

    db_path = tmp_path / "test_tools.db"
    out = io.StringIO()
    err = io.StringIO()

    result = await run_cli_task(
        task="Read sample.txt and write out.txt",
        workspace_root=workspace,
        database_path=db_path,
        provider=provider,
        stdout=out,
        stderr=err,
    )

    assert result.status is RunState.COMPLETED
    assert (workspace / "out.txt").exists()
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "Updated content"
    assert "read_file" in out.getvalue()
    assert "write_file" in out.getvalue()


@pytest.mark.asyncio
async def test_run_cli_task_worktree_failure_fails_closed(tmp_path: Path) -> None:
    from avo.exceptions import AvoError

    non_git_dir = tmp_path / "not_git"
    non_git_dir.mkdir()

    fake_provider = FakeProvider([ModelResponse(content="done")])
    db_path = tmp_path / "test.db"

    # With allow_in_place=False (default), it MUST fail closed with AvoError
    with pytest.raises(AvoError, match="Git worktree isolation failed"):
        await run_cli_task(
            task="do work",
            workspace_root=non_git_dir,
            database_path=db_path,
            provider=fake_provider,
            use_worktree=True,
            allow_in_place=False,
        )

    # With allow_in_place=True, it warns and continues
    err = io.StringIO()
    res = await run_cli_task(
        task="do work",
        workspace_root=non_git_dir,
        database_path=db_path,
        provider=fake_provider,
        use_worktree=True,
        allow_in_place=True,
        stderr=err,
    )
    assert res.status is RunState.COMPLETED
    assert "Warning: Git worktree isolation unavailable" in err.getvalue()
