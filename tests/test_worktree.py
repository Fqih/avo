"""Tests for GitWorktreeManager (app_tools/worktree.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from avo.app_tools.worktree import GitWorktreeError, GitWorktreeManager


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal initialized git repository with a main branch and an initial commit."""
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

    initial_file = repo / "README.md"
    initial_file.write_text("# Initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True
    )

    return repo


def test_worktree_create_and_isolation(git_repo: Path) -> None:
    manager = GitWorktreeManager(repo_root=git_repo)
    run_id = "run-123"

    worktree_path = manager.create_worktree(run_id=run_id)
    assert worktree_path.exists()
    assert worktree_path.is_dir()
    assert (worktree_path / "README.md").exists()

    # Verify isolated change: write in worktree
    isolated_file = worktree_path / "isolated.txt"
    isolated_file.write_text("isolated content", encoding="utf-8")

    # Main repo must not see the file
    assert not (git_repo / "isolated.txt").exists()

    # List active worktrees
    active = manager.list_active_worktrees()
    assert any(w["run_id"] == run_id for w in active)


def test_worktree_cleanup_without_merge(git_repo: Path) -> None:
    manager = GitWorktreeManager(repo_root=git_repo)
    run_id = "run-discard"

    worktree_path = manager.create_worktree(run_id=run_id)
    (worktree_path / "discard.txt").write_text("throwaway", encoding="utf-8")

    success = manager.cleanup_worktree(run_id=run_id, merge=False)
    assert success is True
    assert not worktree_path.exists()

    # Temporary branch avo/task-run-discard must not exist
    branches = subprocess.run(
        ["git", "branch"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout
    assert f"avo/task-{run_id}" not in branches


def test_worktree_cleanup_with_merge(git_repo: Path) -> None:
    manager = GitWorktreeManager(repo_root=git_repo)
    run_id = "run-merge"

    worktree_path = manager.create_worktree(run_id=run_id)
    new_file = worktree_path / "feature.txt"
    new_file.write_text("feature content", encoding="utf-8")

    # Commit inside worktree
    subprocess.run(
        ["git", "add", "feature.txt"], cwd=worktree_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "feat: add feature file"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )

    success = manager.cleanup_worktree(run_id=run_id, merge=True, target_branch="main")
    assert success is True
    assert not worktree_path.exists()

    # Main repo must now have the merged file
    assert (git_repo / "feature.txt").exists()
    assert (git_repo / "feature.txt").read_text(encoding="utf-8") == "feature content"


def test_worktree_rejects_path_traversal(git_repo: Path) -> None:
    manager = GitWorktreeManager(repo_root=git_repo)
    with pytest.raises(GitWorktreeError, match="Invalid run_id"):
        manager.create_worktree(run_id="../escape")
