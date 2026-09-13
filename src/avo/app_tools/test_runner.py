"""``test_runner`` tool — execute automated tests on workspace code.

Enables the autonomous agent to verify implementation correctness and
diagnose failures before reporting back to the operator.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from .edit_file import EditFileError
from .file_tools import _workspace_stack
from .workspace import Workspace


class TestRunnerArguments(BaseModel):
    """Arguments for the ``test_runner`` tool."""

    __test__ = False

    target: str | None = Field(
        default=None,
        description=(
            "Optional test path or expression (e.g. 'tests/test_router.py' "
            "or 'tests/test_router.py::test_name'). If omitted, runs the test suite."
        ),
    )
    max_failures: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Stop test run after this many failures.",
    )


def _current_workspace() -> Workspace:
    if not _workspace_stack:
        raise EditFileError(
            "test_runner tool invoked without an active workspace; wrap the run in "
            "avo.app_tools.file_tools.bind_workspace(...)"
        )
    return _workspace_stack[-1]


def run_tests(
    workspace_root: Path,
    target: str | None = None,
    *,
    max_failures: int = 5,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Execute automated tests in the workspace and return parsed results."""
    # Determine test runner command
    runner_cmd: list[str]
    runner_name: str
    try:
        import pytest  # noqa: F401

        has_pytest = True
    except ImportError:
        has_pytest = bool(shutil.which("pytest"))

    if has_pytest:
        runner_name = "pytest"
        runner_cmd = [sys.executable, "-m", "pytest", "-q", f"--maxfail={max_failures}"]
    elif shutil.which("uv"):
        runner_name = "pytest"
        runner_cmd = ["uv", "run", "pytest", "-q", f"--maxfail={max_failures}"]
    else:
        runner_name = "unittest"
        runner_cmd = [sys.executable, "-m", "unittest"]

    if target:
        runner_cmd.append(target)

    try:
        proc = subprocess.run(
            runner_cmd,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        passed = proc.returncode == 0
        output = proc.stdout.strip() or proc.stderr.strip()
        lines = [line.strip() for line in output.splitlines() if line.strip()]

        summary = lines[-1] if lines else ("All tests passed" if passed else "Tests failed")
        failures = [line for line in lines if "FAIL" in line or "ERROR" in line][:20]

        return {
            "ok": passed,
            "runner": runner_name,
            "target": str(target or "all"),
            "returncode": proc.returncode,
            "summary": summary,
            "failures": failures,
            "output": "\n".join(lines[-40:]) if len(lines) > 40 else output,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "runner": runner_cmd[0],
            "target": str(target or "all"),
            "returncode": -1,
            "summary": f"Test run timed out after {timeout_seconds}s",
            "failures": ["TimeoutExpired"],
            "output": "Execution timed out.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "runner": runner_cmd[0],
            "target": str(target or "all"),
            "returncode": -1,
            "summary": f"Failed to execute test runner: {exc}",
            "failures": [str(exc)],
            "output": "",
        }


async def _test_runner(arguments: TestRunnerArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    return run_tests(
        workspace.root,
        arguments.target,
        max_failures=arguments.max_failures,
    )


def test_runner_tool() -> PublicFunctionTool[TestRunnerArguments]:
    """Return a :class:`FunctionTool` that executes workspace tests."""
    return PublicFunctionTool(
        name="test_runner",
        description=(
            "Run automated test suite (e.g. pytest) against the workspace. "
            "Returns status, failure traces, and execution summary."
        ),
        arguments_model=TestRunnerArguments,
        function=_test_runner,
    )


__all__ = ["TestRunnerArguments", "run_tests", "test_runner_tool"]
