"""``lint`` tool — run linter and syntax checks on workspace files.

Provides automated code-health checks so the agent and operator can verify
syntactic validity and stylistic cleanliness before finalizing changes.
Prefers ``ruff check`` and falls back to ``py_compile`` for syntax verification.
"""

from __future__ import annotations

import py_compile
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from .edit_file import EditFileError
from .file_tools import _workspace_stack
from .workspace import Workspace


class LintArguments(BaseModel):
    """Arguments for the ``lint`` tool."""

    path: str | None = Field(
        default=None,
        description=(
            "Workspace-relative file or directory path to check. "
            "If omitted, checks the entire workspace."
        ),
    )


def _current_workspace() -> Workspace:
    if not _workspace_stack:
        raise EditFileError(
            "lint tool invoked without an active workspace; wrap the run in "
            "avo.app_tools.file_tools.bind_workspace(...)"
        )
    return _workspace_stack[-1]


def run_linter(workspace_root: Path, rel_path: str | None = None) -> dict[str, Any]:
    """Execute code quality checks against workspace files."""
    target = workspace_root if not rel_path else workspace_root / rel_path
    if not target.exists():
        return {
            "ok": False,
            "tool": "none",
            "target": str(rel_path or "."),
            "issue_count": 1,
            "issues": [f"Path not found: {target}"],
        }

    # Prefer ruff if available
    if shutil.which("ruff"):
        cmd = ["ruff", "check", str(target), "--output-format=concise"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                return {
                    "ok": True,
                    "tool": "ruff",
                    "target": str(rel_path or "."),
                    "issue_count": 0,
                    "issues": [],
                }

            raw_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            ruff_issues = raw_lines[:50]  # Cap issues to protect context
            return {
                "ok": False,
                "tool": "ruff",
                "target": str(rel_path or "."),
                "issue_count": len(raw_lines),
                "issues": ruff_issues,
            }
        except Exception as exc:
            return {
                "ok": False,
                "tool": "ruff",
                "target": str(rel_path or "."),
                "issue_count": 1,
                "issues": [f"ruff execution failed: {exc}"],
            }

    # Fallback to python syntax check via py_compile for python files
    issues: list[str] = []
    if target.is_file() and target.suffix == ".py":
        py_files = [target]
    else:
        py_files = list(target.rglob("*.py"))
    for py_file in py_files[:100]:
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as exc:
            rel = py_file.relative_to(workspace_root)
            issues.append(f"{rel}: {exc.msg}")

    return {
        "ok": len(issues) == 0,
        "tool": "py_compile",
        "target": str(rel_path or "."),
        "issue_count": len(issues),
        "issues": issues,
    }


async def _lint(arguments: LintArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    return run_linter(workspace.root, arguments.path)


def lint_tool() -> PublicFunctionTool[LintArguments]:
    """Return a :class:`FunctionTool` that checks workspace code quality."""
    return PublicFunctionTool(
        name="lint",
        description=(
            "Run linter and syntax checks on the workspace or a specific file. "
            "Returns diagnostic issues so you can fix errors before finishing."
        ),
        arguments_model=LintArguments,
        function=_lint,
    )


__all__ = ["LintArguments", "lint_tool", "run_linter"]
