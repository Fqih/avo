"""Workspace terminal tool for the coding agent.

This is deliberately separate from the interactive ``/shell`` command: it
is a typed runtime tool that the model can call while inspecting, editing,
and testing a project. Operators can require approval with
``AVO_TOOLS_REQUIRE_APPROVAL=run_terminal``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from .edit_file import EditFileError
from .file_tools import _current_workspace


class RunTerminalArguments(BaseModel):
    """Arguments for the host terminal tool."""

    command: str = Field(min_length=1, max_length=20_000, description="Shell command to run")
    timeout_seconds: float = Field(
        default=120.0,
        ge=0.1,
        le=900.0,
        description="Maximum command runtime in seconds",
    )


def _current_workspace_or_error() -> Any:
    try:
        return _current_workspace()
    except Exception as exc:
        raise EditFileError("run_terminal invoked without an active workspace") from exc


async def _run_command(command: str, workspace_root: str, timeout_seconds: float) -> dict[str, Any]:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-lc",
            command,
            cwd=workspace_root,
            env={**os.environ, "PWD": workspace_root},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        return {
            "command": command,
            "cwd": workspace_root,
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "timed_out": False,
        }
    except TimeoutError:
        if process is not None:
            process.kill()
            await process.communicate()
        return {
            "command": command,
            "cwd": workspace_root,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
            "error": f"command timed out after {timeout_seconds:g}s",
        }


async def _run_terminal(arguments: RunTerminalArguments) -> dict[str, Any]:
    workspace = _current_workspace_or_error()
    return await _run_command(
        arguments.command,
        str(workspace.root),
        arguments.timeout_seconds,
    )


def run_terminal_tool() -> PublicFunctionTool[RunTerminalArguments]:
    """Return the model-facing host terminal tool."""

    return PublicFunctionTool(
        name="run_terminal",
        description=(
            "Run a shell command in the active workspace and return its exit code, "
            "stdout, and stderr. Use it for project commands, tests, linters, and "
            "other coding tasks. The command runs on the Avo host."
        ),
        arguments_model=RunTerminalArguments,
        function=_run_terminal,
    )


__all__ = ["RunTerminalArguments", "run_terminal_tool"]
