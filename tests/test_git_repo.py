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


def test_git_repo_list_and_switch_branches(git_repo: Path) -> None:
    repo = GitRepository(git_repo)
    branches = repo.list_branches()
    assert len(branches) >= 1
    assert any(b["name"] == "main" and b["current"] is True for b in branches)

    # Create and switch to a new branch
    repo.switch_branch("feat/experiment", create=True)
    assert repo.current_branch() == "feat/experiment"

    branches_after = repo.list_branches()
    branch_names = [b["name"] for b in branches_after]
    assert "feat/experiment" in branch_names
    assert "main" in branch_names
    current_entry = next(b for b in branches_after if b["name"] == "feat/experiment")
    assert current_entry["current"] is True

    # Switch back to main
    repo.switch_branch("main", create=False)
    assert repo.current_branch() == "main"

    # Empty branch name raises GitError
    with pytest.raises(GitError, match="branch name cannot be empty"):
        repo.switch_branch("   ")

    # Non-existent branch without create raises GitError
    with pytest.raises(GitError, match="git switch failed"):
        repo.switch_branch("non-existent-branch-12345")


def test_git_repo_log(git_repo: Path) -> None:
    repo = GitRepository(git_repo)
    commits = repo.log(max_count=5)
    assert len(commits) == 1
    assert commits[0]["subject"] == "initial"
    assert commits[0]["author"] == "Test"
    assert "hash" in commits[0]
    assert "date" in commits[0]

    # Add a second commit
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "second commit")

    commits2 = repo.log(max_count=2)
    assert len(commits2) == 2
    assert commits2[0]["subject"] == "second commit"
    assert commits2[1]["subject"] == "initial"

    # Respect max_count=1
    assert len(repo.log(max_count=1)) == 1

    # max_count < 1 returns empty list
    assert repo.log(max_count=0) == []


def test_branch_and_log_outside_repo(tmp_path: Path) -> None:
    repo = GitRepository(tmp_path)
    with pytest.raises(GitError, match="not a git repository"):
        repo.list_branches()
    with pytest.raises(GitError, match="not a git repository"):
        repo.switch_branch("main")
    with pytest.raises(GitError, match="not a git repository"):
        repo.log()


def test_git_repo_stash_lifecycle(git_repo: Path) -> None:
    repo = GitRepository(git_repo)
    # git_repo fixture has tracked.txt modified and new.txt untracked
    assert not repo.status().is_clean

    # 1. Stash save with custom message
    res = repo.stash_save("experimental WIP")
    assert "WIP" in res or "Saved" in res
    assert repo.status().is_clean
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "hello"

    # 2. Stash list
    stashes = repo.stash_list()
    assert len(stashes) == 1
    assert "experimental WIP" in stashes[0]["description"]

    # 3. Stash pop
    repo.stash_pop(0)
    assert not repo.status().is_clean
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "hello world"
    assert len(repo.stash_list()) == 0

    # 4. Stash save & drop
    repo.stash_save("to be dropped")
    assert len(repo.stash_list()) == 1
    repo.stash_drop(0)
    assert len(repo.stash_list()) == 0


def test_stash_outside_repo(tmp_path: Path) -> None:
    repo = GitRepository(tmp_path)
    with pytest.raises(GitError, match="not a git repository"):
        repo.stash_save()
    with pytest.raises(GitError, match="not a git repository"):
        repo.stash_list()
    with pytest.raises(GitError, match="not a git repository"):
        repo.stash_pop()
    with pytest.raises(GitError, match="not a git repository"):
        repo.stash_drop()
