"""Tests for the ``git_diff`` tool."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from avo.app_tools.edit_file import EditFileError
from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.git_diff import GitDiffArguments, git_diff_tool
from avo.app_tools.workspace import Workspace


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
def workspace_with_git(tmp_path: Path, isolated_home: Path) -> Workspace:
    repo = tmp_path / "ws"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("line1\nline2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.txt").write_text("line1\nline2 edited\nline3\n", encoding="utf-8")
    return Workspace(repo, create=False)


async def _invoke(args: GitDiffArguments, workspace: Workspace) -> dict[str, object]:
    tool = git_diff_tool()
    with bind_workspace(workspace):
        return await tool._function(args)  # type: ignore[no-any-return]


async def test_git_diff_returns_unstaged_diff(workspace_with_git: Workspace) -> None:
    result = await _invoke(GitDiffArguments(), workspace_with_git)
    assert result["empty"] is False
    diff_str = str(result["diff"])
    assert "+line2 edited" in diff_str
    assert "-line2" in diff_str


async def test_git_diff_with_path_filter(workspace_with_git: Workspace) -> None:
    result = await _invoke(GitDiffArguments(path="a.txt"), workspace_with_git)
    assert "+line2 edited" in str(result["diff"])

    # Non-existent or clean file has empty diff
    result_clean = await _invoke(GitDiffArguments(path="nonexistent.txt"), workspace_with_git)
    assert result_clean["empty"] is True


async def test_git_diff_staged(workspace_with_git: Workspace) -> None:
    # Staged diff is empty before staging
    result_pre = await _invoke(GitDiffArguments(staged=True), workspace_with_git)
    assert result_pre["empty"] is True

    # Stage the file
    _git(Path(str(workspace_with_git.root)), "add", "a.txt")
    result_post = await _invoke(GitDiffArguments(staged=True), workspace_with_git)
    assert result_post["empty"] is False
    assert "+line2 edited" in str(result_post["diff"])


async def test_git_diff_requires_workspace(tmp_path: Path) -> None:
    tool = git_diff_tool()
    with pytest.raises(EditFileError, match="without an active workspace"):
        await tool._function(GitDiffArguments())  # type: ignore[no-any-return]


async def test_git_diff_raises_for_non_git_workspace(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path, create=True)
    tool = git_diff_tool()
    with bind_workspace(workspace), pytest.raises(Exception, match="not a git repository"):
        await tool._function(GitDiffArguments())  # type: ignore[no-any-return]
