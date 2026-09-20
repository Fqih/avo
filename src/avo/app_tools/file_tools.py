"""``read_file`` and ``write_file`` tools bound to a fixed workspace.

Both tools resolve the model-supplied path through
:class:`avo.app_tools.workspace.Workspace` before any I/O. The
returned :class:`FunctionTool` instances plug into
``AgentRuntime(tools=[...])`` without any change to the runtime itself.

Approval and timeout policy are configured at the ``AgentRuntime`` level
via ``approval_callback=`` and ``LoopPolicy.tool_timeout_seconds=``. This
module does **not** introduce its own timeout or approval mechanism.

The workspace itself is bound per call site via :func:`bind_workspace`,
which keeps the tools stateless across runs and avoids relying on
thread-locals or mutable module state that a hostile prompt could
mutate.
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from .workspace import Workspace, assert_workspace_target


class ReadFileArguments(BaseModel):
    """Arguments for the ``read_file`` tool."""

    path: str = Field(min_length=1, description="Workspace-relative or absolute path")
    encoding: str = Field(default="utf-8", min_length=1)


class WriteFileArguments(BaseModel):
    """Arguments for the ``write_file`` tool."""

    path: str = Field(min_length=1, description="Workspace-relative or absolute path")
    content: str = Field(description="Full file content to write")
    encoding: str = Field(default="utf-8", min_length=1)


class WorkspaceNotBoundError(RuntimeError):
    """Raised when a file tool is invoked without an active workspace binding."""


_workspace_stack_var: contextvars.ContextVar[tuple[Workspace, ...]] = contextvars.ContextVar(
    "_workspace_stack_var", default=()
)
_workspace_stack: list[Workspace] = []


@contextmanager
def bind_workspace(workspace: Workspace) -> Generator[None, None, None]:
    """Push ``workspace`` onto the active binding for the duration of the block."""

    _workspace_stack.append(workspace)
    stack = _workspace_stack_var.get()
    token = _workspace_stack_var.set((*stack, workspace))
    try:
        yield
    finally:
        _workspace_stack_var.reset(token)
        if _workspace_stack:
            _workspace_stack.pop()


def _current_workspace() -> Workspace:
    stack = _workspace_stack_var.get()
    if stack:
        return stack[-1]
    if _workspace_stack:
        return _workspace_stack[-1]
    raise WorkspaceNotBoundError(
        "file tool invoked without an active workspace; wrap the run in "
        "avo.app_tools.file_tools.bind_workspace(...)"
    )


async def _read_file(arguments: ReadFileArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    resolved = workspace.validate_path(arguments.path, must_exist=True)
    text = resolved.read_text(encoding=arguments.encoding)
    return {
        "path": str(resolved),
        "size": len(text.encode(arguments.encoding)),
        "content": text,
    }


async def _write_file(arguments: WriteFileArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    resolved = workspace.validate_for_write(arguments.path)
    # Re-check containment and symlink state immediately before opening the
    # leaf. The earlier validation protects the user-facing error path; this
    # final check narrows the replacement window for a changed target.
    resolved = assert_workspace_target(workspace.root, resolved)
    encoded = arguments.content.encode(arguments.encoding)
    # Open the leaf explicitly, refusing to follow symlinks at the leaf so
    # a model cannot redirect a write to an external target after the
    # workspace check. O_NOFOLLOW is POSIX-only; on Windows we accept the
    # residual TOCTOU window.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(resolved, flags, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)
    return {
        "path": str(resolved),
        "size": len(encoded),
    }


def read_file_tool() -> PublicFunctionTool[ReadFileArguments]:
    """Return a :class:`FunctionTool` that reads a file inside the active workspace."""

    return PublicFunctionTool(
        name="read_file",
        description=(
            "Read the full content of a file inside the workspace. Paths "
            "are resolved against the workspace root; any traversal "
            "outside the workspace is rejected before the file is opened."
        ),
        arguments_model=ReadFileArguments,
        function=_read_file,
    )


def write_file_tool() -> PublicFunctionTool[WriteFileArguments]:
    """Return a :class:`FunctionTool` that writes a file inside the active workspace."""

    return PublicFunctionTool(
        name="write_file",
        description=(
            "Write content to a file inside the workspace, creating the "
            "file if necessary. Existing files are overwritten. Paths "
            "are resolved against the workspace root; any traversal "
            "outside the workspace is rejected before the file is opened."
        ),
        arguments_model=WriteFileArguments,
        function=_write_file,
    )


__all__ = [
    "ReadFileArguments",
    "WorkspaceNotBoundError",
    "WriteFileArguments",
    "bind_workspace",
    "read_file_tool",
    "write_file_tool",
]
