"""Tests for the model-facing terminal tool security boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.sandbox import SandboxResult
from avo.app_tools.terminal_tool import redact_output, run_terminal_tool
from avo.app_tools.workspace import Workspace
from avo.config_resolver import resolve_security_config


class FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        command: str,
        *,
        workspace_dir: Path,
        env: dict[str, str],
        timeout_seconds: float,
    ) -> SandboxResult:
        self.calls.append(
            {
                "command": command,
                "workspace_dir": workspace_dir,
                "env": env,
                "timeout_seconds": timeout_seconds,
            }
        )
        return SandboxResult(
            exit_code=0,
            stdout="sandbox-ok",
            stderr="",
            duration_ms=1.0,
            image="python:3.12-slim",
            network_mode="none",
            mem_limit="256m",
        )


@pytest.mark.asyncio
async def test_terminal_uses_injected_sandbox_and_filtered_environment(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path, create=True)
    fake = FakeSandbox()
    config = resolve_security_config(
        explicit={"sandbox_required": True},
        environ={"AVO_OPENAI_API_KEY": "do-not-pass", "PATH": "/usr/bin"},
        user_root=tmp_path / "config",
    )

    with bind_workspace(workspace):
        result = await run_terminal_tool(
            security_config=config,
            sandbox_executor=fake,
        ).invoke({"command": "pytest -q", "timeout_seconds": 7})

    assert result["stdout"] == "sandbox-ok"
    assert fake.calls[0]["workspace_dir"] == tmp_path
    assert fake.calls[0]["timeout_seconds"] == 7
    environment = fake.calls[0]["env"]
    assert isinstance(environment, dict)
    assert "AVO_OPENAI_API_KEY" not in environment


@pytest.mark.asyncio
async def test_terminal_defaults_to_sandbox_when_policy_is_omitted(tmp_path: Path) -> None:
    """Direct tool consumers must not get an implicit host-execution escape hatch."""

    workspace = Workspace(tmp_path, create=True)
    fake = FakeSandbox()

    with bind_workspace(workspace):
        result = await run_terminal_tool(sandbox_executor=fake).invoke(
            {"command": "printf safe", "timeout_seconds": 5}
        )

    assert result["mode"] == "sandbox"
    assert result["stdout"] == "sandbox-ok"
    assert fake.calls[0]["workspace_dir"] == tmp_path


def test_redact_output_removes_known_secret_values() -> None:
    assert redact_output("token=openai-secret", {"openai-secret"}) == "token=[REDACTED]"
    assert redact_output("nothing sensitive", {"openai-secret"}) == "nothing sensitive"
