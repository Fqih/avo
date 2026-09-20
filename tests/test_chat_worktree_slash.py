"""Tests for `/worktree` and `/review` slash commands in chat REPL."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from avo.chat import build_chat_context
from avo.chat_commands import _manage_worktree_command, _run_review_command
from avo.providers.fake import FakeProvider


@pytest.fixture
def chat_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _ollama_env() -> dict[str, str]:
    return {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "llama3.1",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }


def test_manage_worktree_lifecycle(chat_git_repo: Path) -> None:
    ctx = build_chat_context(
        database_path=chat_git_repo / "avo.db",
        workspace_root=chat_git_repo,
        environ=_ollama_env(),
    )

    out = io.StringIO()
    err = io.StringIO()

    # 1. Status with no worktree
    _manage_worktree_command(ctx, ["/worktree", "status"], out, err)
    assert "No active worktree" in out.getvalue() or "active worktrees: 0" in out.getvalue().lower()

    # 2. Isolate worktree
    out = io.StringIO()
    _manage_worktree_command(ctx, ["/worktree", "isolate", "feature-test"], out, err)
    assert "Isolated worktree active" in out.getvalue()
    assert ".avo/worktrees/feature-test" in str(ctx.workspace.root)

    # 3. List active worktree
    out = io.StringIO()
    _manage_worktree_command(ctx, ["/worktree", "list"], out, err)
    assert "feature-test" in out.getvalue()

    # 4. Discard worktree
    out = io.StringIO()
    _manage_worktree_command(ctx, ["/worktree", "discard"], out, err)
    assert "Discarded worktree" in out.getvalue()
    assert ctx.workspace.root == chat_git_repo.resolve()


@pytest.mark.asyncio
async def test_run_review_command_diff(chat_git_repo: Path) -> None:
    # Modify a file to produce diff
    (chat_git_repo / "README.md").write_text("# Modified\n", encoding="utf-8")

    from avo import ModelResponse

    fake_provider = FakeProvider([ModelResponse(content="Code review: change looks clean.")])

    env = _ollama_env()
    ctx = build_chat_context(
        database_path=chat_git_repo / "avo.db",
        workspace_root=chat_git_repo,
        environ=env,
    )
    ctx.runtime.provider = fake_provider

    out = io.StringIO()
    err = io.StringIO()

    await _run_review_command(ctx, ["/review"], out, err)
    assert "Reviewing uncommitted changes" in out.getvalue()
