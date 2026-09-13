"""Tests for the git_commit tool and /commit REPL command."""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
from pathlib import Path

import pytest

from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.git_commit import GitCommitArguments, git_commit_tool
from avo.app_tools.workspace import Workspace
from avo.chat import _run_slash, build_chat_context
from avo.workspace.git import (
    GitError,
    GitRepository,
    GitStatus,
    GitStatusEntry,
    generate_commit_message_heuristic,
)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_LFS_SKIP_SMUDGE", "1")
    return home


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


@pytest.fixture
def repo_dir(tmp_path: Path, isolated_home: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("# Project\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "chore: initial commit")
    return repo


def test_git_repo_add_and_commit(repo_dir: Path) -> None:
    repo = GitRepository(repo_dir)
    (repo_dir / "app.py").write_text("print('hello')", encoding="utf-8")

    status = repo.status()
    assert not status.is_clean

    sha = repo.commit("feat: add app.py")
    assert isinstance(sha, str)
    assert len(sha) >= 4
    assert repo.status().is_clean


def test_git_repo_commit_empty_tree_raises(repo_dir: Path) -> None:
    repo = GitRepository(repo_dir)
    with pytest.raises(GitError, match="nothing to commit"):
        repo.commit("feat: nothing")


def test_git_repo_commit_empty_message_raises(repo_dir: Path) -> None:
    repo = GitRepository(repo_dir)
    (repo_dir / "app.py").write_text("x = 1", encoding="utf-8")
    with pytest.raises(GitError, match="commit message cannot be empty"):
        repo.commit("   ")


def test_generate_commit_message_heuristic_single_files() -> None:
    # Test file
    status = GitStatus(
        root=Path("/fake"),
        branch="main",
        entries=(GitStatusEntry(path="tests/test_foo.py", status_code="M "),),
    )
    assert generate_commit_message_heuristic(status) == "test: update test_foo"

    # Docs file
    status_doc = GitStatus(
        root=Path("/fake"),
        branch="main",
        entries=(GitStatusEntry(path="README.md", status_code="M "),),
    )
    assert generate_commit_message_heuristic(status_doc) == "docs: update README.md"

    # Src file
    status_src = GitStatus(
        root=Path("/fake"),
        branch="main",
        entries=(GitStatusEntry(path="src/avo/router.py", status_code="M "),),
    )
    assert generate_commit_message_heuristic(status_src) == "feat(router): update router"


def test_generate_commit_message_heuristic_multiple_files() -> None:
    status = GitStatus(
        root=Path("/fake"),
        branch="main",
        entries=(
            GitStatusEntry(path="tests/test_a.py", status_code="M "),
            GitStatusEntry(path="tests/test_b.py", status_code="??"),
        ),
    )
    assert generate_commit_message_heuristic(status) == "test: update test suite (2 files)"

    status_mixed = GitStatus(
        root=Path("/fake"),
        branch="main",
        entries=(
            GitStatusEntry(path="src/avo/a.py", status_code="M "),
            GitStatusEntry(path="docs/b.md", status_code="??"),
        ),
    )
    assert "2 files across workspace" in generate_commit_message_heuristic(status_mixed)


def test_git_commit_tool_invoked(repo_dir: Path) -> None:
    (repo_dir / "feature.py").write_text("VALUE = 42\n", encoding="utf-8")
    tool = git_commit_tool()
    assert tool.metadata.name == "git_commit"

    ws = Workspace(repo_dir, create=False)
    with bind_workspace(ws):
        res = asyncio.run(tool.invoke(GitCommitArguments(message="feat: add feature module")))

    assert res["ok"] is True
    assert res["branch"] == "main"
    assert res["message"] == "feat: add feature module"
    assert "commit_hash" in res


def test_repl_commit_slash_command(repo_dir: Path, tmp_path: Path) -> None:
    (repo_dir / "notes.txt").write_text("some notes\n", encoding="utf-8")

    db_path = tmp_path / "chat.db"
    environ = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "llama3.2",
    }
    ctx = build_chat_context(
        database_path=db_path,
        workspace_root=repo_dir,
        environ=environ,
    )

    out = io.StringIO()
    err = io.StringIO()

    # Commit with custom message
    exited = asyncio.run(_run_slash(ctx, ["/commit", "docs: add release notes"], out, err, environ))
    assert exited is False
    assert "✓ Committed" in out.getvalue()
    assert "docs: add release notes" in out.getvalue()
    assert GitRepository(repo_dir).status().is_clean

    # Commit again when clean
    out2 = io.StringIO()
    err2 = io.StringIO()
    exited = asyncio.run(_run_slash(ctx, ["/commit"], out2, err2, environ))
    assert exited is False
    assert "Working tree is clean" in out2.getvalue()
