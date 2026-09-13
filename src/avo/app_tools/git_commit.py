"""``git_commit`` tool — stage changes and create atomic git commits.

The tool binds to the active :class:`Workspace` and invokes
:class:`GitRepository.commit`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from ..workspace.git import GitError, GitRepository
from .edit_file import EditFileError
from .file_tools import _workspace_stack
from .workspace import Workspace


class GitCommitArguments(BaseModel):
    """Arguments for the ``git_commit`` tool."""

    message: str = Field(
        ...,
        description=(
            "The commit message following Conventional Commits format "
            "(e.g. 'feat(auth): add pkce flow')"
        ),
    )
    paths: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of workspace-relative paths to stage before committing. "
            "If omitted or empty, all modified/untracked files are staged."
        ),
    )


def _current_workspace() -> Workspace:
    if not _workspace_stack:
        raise EditFileError(
            "git_commit invoked without an active workspace; wrap the run in "
            "avo.app_tools.file_tools.bind_workspace(...)"
        )
    return _workspace_stack[-1]


async def _git_commit(arguments: GitCommitArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    repo = GitRepository(workspace.root)
    try:
        commit_hash = repo.commit(
            message=arguments.message,
            paths=arguments.paths,
        )
        branch = repo.current_branch()
        return {
            "ok": True,
            "commit_hash": commit_hash,
            "message": arguments.message,
            "branch": branch,
            "root": str(workspace.root),
        }
    except GitError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "root": str(workspace.root),
        }


def git_commit_tool() -> PublicFunctionTool[GitCommitArguments]:
    """Return a :class:`FunctionTool` that stages files and commits."""
    return PublicFunctionTool(
        name="git_commit",
        description=(
            "Stage modified/untracked files and create a git commit with a clear "
            "Conventional Commit message. Returns the new commit hash."
        ),
        arguments_model=GitCommitArguments,
        function=_git_commit,
    )


__all__ = ["GitCommitArguments", "git_commit_tool"]
