"""Application tools: workspace sandboxing, shell execution, and approval.

Application tools build on the existing ``FunctionTool`` / ``ToolRegistry``
contract documented in ``src/avo/tools.py``. They do not modify
``AgentRuntime`` or the state machine; they plug in via the existing
``approval_callback`` parameter on ``AgentRuntime``.

The sandbox module is optional and lives behind the ``[sandbox]`` extra in
``pyproject.toml`` because it requires the ``docker`` package.
"""

from __future__ import annotations

from .file_tools import bind_workspace, read_file_tool, write_file_tool
from .git_diff import git_diff_tool
from .git_status import git_status_tool
from .linter import lint_tool
from .workspace_map import workspace_map_tool

__all__ = [
    "bind_workspace",
    "git_diff_tool",
    "git_status_tool",
    "lint_tool",
    "read_file_tool",
    "workspace_map_tool",
    "write_file_tool",
]
