"""``git_diff`` tool — inspect unified diff of unstaged or staged changes.

The tool binds to the active :class:`Workspace` and invokes
:class:`GitRepository.diff`. Output is safely capped by ``max_lines``
to prevent context overflow.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from ..workspace.git import GitRepository
from .edit_file import EditFileError
from .file_tools import _workspace_stack
from .workspace import Workspace

_DEFAULT_MAX_LINES = 500


class GitDiffArguments(BaseModel):
    """Arguments for the ``git_diff`` tool."""

    path: str | None = Field(
        default=None,
        description="Workspace-relative path to diff (optional; omit for full repository diff)",
    )
    staged: bool = Field(
        default=False,
        description="Whether to diff staged changes against HEAD (git diff --cached)",
    )
    max_lines: int = Field(default=_DEFAULT_MAX_LINES, gt=0, le=5_000)


def _current_workspace() -> Workspace:
    if not _workspace_stack:
        raise EditFileError(
            "git_diff invoked without an active workspace; wrap the run in "
            "avo.app_tools.file_tools.bind_workspace(...)"
        )
    return _workspace_stack[-1]


async def _git_diff(arguments: GitDiffArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    repo = GitRepository(workspace.root)
    diff_text = repo.diff(
        path=arguments.path,
        staged=arguments.staged,
        max_lines=arguments.max_lines,
    )
    return {
        "root": str(workspace.root),
        "path": arguments.path,
        "staged": arguments.staged,
        "diff": diff_text,
        "empty": len(diff_text.strip()) == 0,
    }


def git_diff_tool() -> PublicFunctionTool[GitDiffArguments]:
    """Return a :class:`FunctionTool` that produces unified git diffs."""

    return PublicFunctionTool(
        name="git_diff",
        description=(
            "Return the unified git diff of unstaged changes (or staged changes when staged=True). "
            "Supports filtering by workspace path. Read-only."
        ),
        arguments_model=GitDiffArguments,
        function=_git_diff,
    )


__all__ = ["GitDiffArguments", "git_diff_tool"]
