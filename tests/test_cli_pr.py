"""Tests for autonomous PR generator (`avo pr` / `src/avo/cli_pr.py`)."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from avo import ModelResponse
from avo.cli_pr import run_cli_pr
from avo.providers.fake import FakeProvider


@pytest.fixture
def repo_with_commit(tmp_path: Path) -> Path:
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
    (repo / "README.md").write_text("# Base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=repo, check=True, capture_output=True
    )

    # Create feature branch with a change
    subprocess.run(
        ["git", "checkout", "-b", "feat/my-feature"], cwd=repo, check=True, capture_output=True
    )
    (repo / "feature.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add hello function"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    return repo


@pytest.mark.asyncio
async def test_run_cli_pr_generates_pr_description(repo_with_commit: Path) -> None:
    pr_response_text = (
        "Title: feat(core): implement hello world utility\n\n"
        "## Summary\n"
        "Adds a new greeting function for core services.\n\n"
        "## Key Changes\n"
        "- Added `feature.py` with `hello()` function.\n\n"
        "## Test Plan\n"
        "- Verified with unit tests."
    )
    fake_provider = FakeProvider([ModelResponse(content=pr_response_text)])

    out = io.StringIO()
    err = io.StringIO()

    pr_doc = await run_cli_pr(
        workspace_root=repo_with_commit,
        base_branch="main",
        provider=fake_provider,
        stdout=out,
        stderr=err,
    )

    assert pr_doc is not None
    assert "Title: feat(core): implement hello world utility" in pr_doc
    assert "Pull Request Draft" in out.getvalue()
    assert "feat(core): implement hello world utility" in out.getvalue()


@pytest.mark.asyncio
async def test_run_cli_pr_when_no_diff(tmp_path: Path) -> None:
    repo = tmp_path / "clean_repo"
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
    (repo / "README.md").write_text("# Clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)  # noqa: ASYNC221
    subprocess.run(  # noqa: ASYNC221
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )

    out = io.StringIO()
    err = io.StringIO()

    result = await run_cli_pr(
        workspace_root=repo,
        base_branch="main",
        stdout=out,
        stderr=err,
    )

    assert result is None
    assert "No diff found" in out.getvalue() or "Working tree is clean" in out.getvalue()


def test_main_maps_pr_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.cli import _parser, main

    parser = _parser()
    args, _ = parser.parse_known_args(["pr", "--base", "develop"])
    assert args.command == "pr"
    assert args.base == "develop"

    called_base: list[str] = []

    async def mock_run_cli_pr(base_branch: str = "main", **_kwargs: object) -> str:
        del _kwargs
        called_base.append(base_branch)
        return "PR text"

    monkeypatch.setattr("avo.cli_pr.run_cli_pr", mock_run_cli_pr)

    code = main(["pr", "--base", "develop"])
    assert code == 0
    assert called_base == ["develop"]


@pytest.mark.asyncio
async def test_chat_slash_pr_dispatches_cleanly(
    repo_with_commit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from avo.chat import build_chat_context
    from avo.chat_commands import _run_slash

    ctx = build_chat_context(
        database_path=repo_with_commit / "avo.db",
        workspace_root=repo_with_commit,
        environ={
            "AVO_PROVIDER": "ollama",
            "AVO_MODEL": "llama3.1",
            "AVO_OLLAMA_BASE_URL": "http://example.invalid",
        },
    )

    out = io.StringIO()
    err = io.StringIO()

    called_pr_base: list[str] = []

    async def mock_run_cli_pr(base_branch: str = "main", **_kwargs: object) -> str:
        del _kwargs
        called_pr_base.append(base_branch)
        return "PR text"

    monkeypatch.setattr("avo.cli_pr.run_cli_pr", mock_run_cli_pr)

    exit_flag = await _run_slash(ctx, ["/pr", "main"], out, err, {})
    assert exit_flag is False
    assert called_pr_base == ["main"]
