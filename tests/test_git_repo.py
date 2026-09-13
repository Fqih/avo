"""Tests for the GitRepository helper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from avo.workspace.git import GitError, GitRepository, GitStatus


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``HOME`` at a temp dir so global git config / lfs filters don't fire."""

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
def git_repo(tmp_path: Path, isolated_home: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("hello", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    # Add an unstaged edit + an untracked file.
    (repo / "tracked.txt").write_text("hello world", encoding="utf-8")
    (repo / "new.txt").write_text("new", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "x").write_text("nope", encoding="utf-8")
    return repo


def test_is_repository_true_inside(git_repo: Path) -> None:
    assert GitRepository(git_repo).is_repository() is True


def test_is_repository_false_outside(tmp_path: Path) -> None:
    assert GitRepository(tmp_path).is_repository() is False


def test_current_branch(git_repo: Path) -> None:
    repo = GitRepository(git_repo)
    assert repo.current_branch() == "main"


def test_status_detects_modified_and_untracked(git_repo: Path) -> None:
    status = GitRepository(git_repo).status()
    assert isinstance(status, GitStatus)
    assert status.branch == "main"
    assert status.clean is False
    paths = {e.path for e in status.entries}
    assert "tracked.txt" in paths
    assert "new.txt" in paths
    assert status.modified == ("tracked.txt",)
    assert status.untracked == ("new.txt",)


def test_status_clean_after_commit(git_repo: Path) -> None:
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "follow")
    status = GitRepository(git_repo).status()
    assert status.clean is True


def test_status_outside_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="not a git repository"):
        GitRepository(tmp_path).status()


def test_diff_summary_includes_branch(git_repo: Path) -> None:
    summary = GitRepository(git_repo).diff_summary()
    assert "main" in summary
    assert "tracked.txt" in summary
    assert "new.txt" in summary


def test_diff_summary_clean_tree(git_repo: Path) -> None:
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "all in")
    summary = GitRepository(git_repo).diff_summary()
    assert "working tree clean" in summary


def test_diff_summary_respects_max_files(git_repo: Path) -> None:
    for i in range(5):
        (git_repo / f"file{i}.md").write_text(f"x{i}", encoding="utf-8")
    summary = GitRepository(git_repo).diff_summary(max_files=2)
    # Five untracked files plus one modified; both are capped at 2.
    assert "+4 more" in summary


def test_run_git_handles_missing_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import avo.workspace.git as mod

    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no git"))
    )
    with pytest.raises(GitError, match="git executable not found"):
        GitRepository(tmp_path).is_repository()


def test_git_repo_diff_unstaged_and_staged(git_repo: Path) -> None:
    repo = GitRepository(git_repo)
    # tracked.txt was modified in fixture: hello -> hello world
    diff_unstaged = repo.diff()
    assert "-hello" in diff_unstaged
    assert "+hello world" in diff_unstaged

    # Filter by specific path
    diff_specific = repo.diff(path="tracked.txt")
    assert "+hello world" in diff_specific

    # Stage the change and check staged diff
    _git(git_repo, "add", "tracked.txt")
    diff_staged = repo.diff(staged=True)
    assert "+hello world" in diff_staged

    # Now unstaged diff should be empty for tracked.txt
    diff_now_unstaged = repo.diff(path="tracked.txt", staged=False)
    assert diff_now_unstaged.strip() == ""


def test_git_repo_rollback(git_repo: Path) -> None:
    repo = GitRepository(git_repo)
    assert not repo.status().is_clean
    reverted = repo.rollback()
    assert "tracked.txt" in reverted
    assert "new.txt" in reverted

    # Verify clean state restored
    assert repo.status().is_clean
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "hello"
    assert not (git_repo / "new.txt").exists()

    # Second rollback is a no-op
    assert repo.rollback() == []
