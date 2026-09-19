"""Workspace terminal tool for the coding agent.

This is deliberately separate from the interactive ``/shell`` command: it
is a typed runtime tool that the model can call while inspecting, editing,
and testing a project. Operators can require approval with
``AVO_TOOLS_REQUIRE_APPROVAL=run_terminal``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool
from avo.config_resolver import resolve_security_config

from .edit_file import EditFileError
from .file_tools import _current_workspace
from .sandbox import (
    ExecutionMode,
    ExecutionPolicy,
    SandboxExecutor,
    SandboxResult,
    build_safe_environment,
    resolve_execution_decision,
)


def _secret_values(environment: Mapping[str, str]) -> set[str]:
    """Return non-empty values that should never be echoed by a tool."""

    markers = ("API_KEY", "AUTH", "COOKIE", "CREDENTIAL", "PASSWORD", "SECRET", "TOKEN")
    values: set[str] = set()
    for key, value in environment.items():
        normalized = key.upper().replace("-", "_")
        if value and any(marker in normalized for marker in markers):
            values.add(value)
    return values


def redact_output(text: str, secrets: set[str]) -> str:
    """Redact known secret values from command output and errors."""

    redacted = text
    for secret in sorted(secrets, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


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


async def _run_command(
    command: str,
    workspace_root: str,
    timeout_seconds: float,
    *,
    environment: Mapping[str, str] | None = None,
    secrets: set[str] | None = None,
) -> dict[str, Any]:
    process: asyncio.subprocess.Process | None = None
    safe_environment = dict(environment or {})
    secret_values = secrets or set()
    try:
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-lc",
            command,
            cwd=workspace_root,
            env=safe_environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        return {
            "command": command,
            "cwd": workspace_root,
            "exit_code": process.returncode,
            "stdout": redact_output(stdout.decode("utf-8", errors="replace"), secret_values),
            "stderr": redact_output(stderr.decode("utf-8", errors="replace"), secret_values),
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


def run_terminal_tool(
    *,
    security_config: Any | None = None,
    sandbox_executor: Any | None = None,
) -> PublicFunctionTool[RunTerminalArguments]:
    """Return the model-facing terminal tool with an explicit execution boundary."""

    async def _run_terminal(arguments: RunTerminalArguments) -> dict[str, Any]:
        workspace = _current_workspace_or_error()
        source_environment = dict(os.environ)
        safe_environment = build_safe_environment(
            source_environment,
            workspace_root=Path(workspace.root),
        )
        secrets = _secret_values(source_environment)

        resolved_security_config = security_config
        if resolved_security_config is None:
            # The tool is also public and can be instantiated outside chat
            # wiring.  Resolve a secure, environment-only default instead of
            # treating a missing policy as permission to execute on the host.
            resolved_security_config = resolve_security_config(
                environ=source_environment,
                user_root=Path("/__avo_no_user_config__"),
            )

        policy = ExecutionPolicy.from_security_config(resolved_security_config)
        executor = sandbox_executor
        if executor is None and policy.sandbox_required:
            executor = SandboxExecutor(
                network_mode=policy.network_mode,
                timeout_seconds=policy.timeout_seconds,
            )
        decision = resolve_execution_decision(
            policy=policy,
            workspace_root=Path(workspace.root),
            operation="run_terminal",
            sandbox_available=executor is not None,
            # An explicit sandbox opt-out is the operator's host-execution
            # approval; runtime-level tool approval still gates invocation.
            approval_granted=not policy.sandbox_required,
        )
        timeout = min(arguments.timeout_seconds, decision.timeout_seconds)
        if decision.mode is ExecutionMode.SANDBOX:
            assert executor is not None
            result: SandboxResult = await executor.run(
                arguments.command,
                workspace_dir=decision.workspace_root,
                env=safe_environment,
                timeout_seconds=timeout,
            )
            return {
                "command": arguments.command,
                "cwd": str(decision.workspace_root),
                "exit_code": result.exit_code,
                "stdout": redact_output(result.stdout, secrets),
                "stderr": redact_output(result.stderr, secrets),
                "timed_out": False,
                "mode": decision.mode.value,
                "network_mode": result.network_mode,
            }
        return await _run_command(
            arguments.command,
            str(decision.workspace_root),
            timeout,
            environment=safe_environment,
            secrets=secrets,
        )

    return PublicFunctionTool(
        name="run_terminal",
        description=(
            "Run a shell command in the active workspace and return its exit code, "
            "stdout, and stderr. Use it for project commands, tests, linters, and "
            "other coding tasks. The command follows the active Avo sandbox policy."
        ),
        arguments_model=RunTerminalArguments,
        function=_run_terminal,
    )


__all__ = ["RunTerminalArguments", "redact_output", "run_terminal_tool"]
