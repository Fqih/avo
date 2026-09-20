"""Tests for GitWorktreeManager lifecycle, branch restoration, and fail-closed isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from avo.app_tools.worktree import GitWorktreeError, GitWorktreeManager


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
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

    # Create and switch to a separate working branch
    subprocess.run(
        ["git", "checkout", "-b", "feature-dev"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def _current_branch(repo: Path) -> str:
    res = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def test_worktree_merge_success_restores_original_branch(git_repo: Path) -> None:
    assert _current_branch(git_repo) == "feature-dev"

    mgr = GitWorktreeManager(repo_root=git_repo)
    wt_path = mgr.create_worktree(run_id="test-restore-1")
    assert wt_path.exists()

    # Commit a change inside the worktree
    (wt_path / "patch.txt").write_text("patch content\n", encoding="utf-8")
    subprocess.run(["git", "add", "patch.txt"], cwd=wt_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "worktree commit"],
        cwd=wt_path,
        check=True,
        capture_output=True,
    )

    # Merge into main
    cleaned = mgr.cleanup_worktree("test-restore-1", merge=True, target_branch="main")
    assert cleaned is True
    assert not wt_path.exists()

    # Verify original branch is restored on base repo
    assert _current_branch(git_repo) == "feature-dev"

    # Verify change landed in main
    main_res = subprocess.run(
        ["git", "show", "main:patch.txt"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert main_res.stdout.strip() == "patch content"


def test_worktree_merge_failure_restores_original_branch(git_repo: Path) -> None:
    assert _current_branch(git_repo) == "feature-dev"

    mgr = GitWorktreeManager(repo_root=git_repo)
    wt_path = mgr.create_worktree(run_id="test-conflict-1")

    # In main, modify README.md
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)
    (git_repo / "README.md").write_text("# Main conflict\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "conflict in main"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "feature-dev"], cwd=git_repo, check=True, capture_output=True
    )

    # In worktree, modify README.md differently to induce merge conflict
    (wt_path / "README.md").write_text("# Worktree conflict\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "conflict in worktree"],
        cwd=wt_path,
        check=True,
        capture_output=True,
    )

    # Merge into main must raise GitWorktreeError
    with pytest.raises(GitWorktreeError, match="Failed to merge"):
        mgr.cleanup_worktree("test-conflict-1", merge=True, target_branch="main")

    # Original branch MUST be restored even on failure
    assert _current_branch(git_repo) == "feature-dev"

    # Clean up
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "merge", "--abort"], cwd=git_repo, capture_output=True, check=False)
    subprocess.run(
        ["git", "checkout", "feature-dev"], cwd=git_repo, check=True, capture_output=True
    )
