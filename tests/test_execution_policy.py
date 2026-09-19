"""Regression tests for the unified command execution boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.app_tools.sandbox import (
    ExecutionDecision,
    ExecutionMode,
    ExecutionPolicy,
    build_safe_environment,
    require_execution_policy,
    resolve_execution_decision,
)
from avo.exceptions import ToolExecutionError


def test_required_sandbox_rejects_host_fallback() -> None:
    policy = ExecutionPolicy(sandbox_required=True, network_enabled=False)

    with pytest.raises(ToolExecutionError, match=r"sandbox.*run_terminal"):
        require_execution_policy(policy, sandbox_available=False, operation="run_terminal")


def test_required_sandbox_resolves_to_sandbox_decision(tmp_path: Path) -> None:
    decision = resolve_execution_decision(
        policy=ExecutionPolicy(sandbox_required=True, network_enabled=False),
        workspace_root=tmp_path,
        operation="run_terminal",
        sandbox_available=True,
    )

    assert isinstance(decision, ExecutionDecision)
    assert decision.mode is ExecutionMode.SANDBOX
    assert decision.workspace_root == tmp_path
    assert decision.network_enabled is False
    assert decision.approval_required is False


def test_host_execution_requires_explicit_operator_approval(tmp_path: Path) -> None:
    policy = ExecutionPolicy(sandbox_required=False, network_enabled=False)

    with pytest.raises(ToolExecutionError, match="operator approval"):
        resolve_execution_decision(
            policy=policy,
            workspace_root=tmp_path,
            operation="run_terminal",
            sandbox_available=False,
        )


def test_approved_host_execution_has_no_network_flag(tmp_path: Path) -> None:
    decision = resolve_execution_decision(
        policy=ExecutionPolicy(sandbox_required=False, network_enabled=False),
        workspace_root=tmp_path,
        operation="run_terminal",
        sandbox_available=False,
        approval_granted=True,
    )

    assert decision.mode is ExecutionMode.HOST
    assert decision.network_enabled is False
    assert decision.approval_required is False


def test_safe_environment_removes_provider_secrets(tmp_path: Path) -> None:
    safe = build_safe_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/tester",
            "LANG": "C.UTF-8",
            "AVO_OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "Authorization": "Bearer secret",
        },
        workspace_root=tmp_path,
    )

    assert safe["PWD"] == str(tmp_path)
    assert safe["PATH"] == "/usr/bin"
    assert safe["LANG"] == "C.UTF-8"
    assert "AVO_OPENAI_API_KEY" not in safe
    assert "ANTHROPIC_API_KEY" not in safe
    assert "Authorization" not in safe


def test_safe_environment_requires_mapping_values() -> None:
    with pytest.raises(TypeError, match="environment values must be strings"):
        build_safe_environment({"PATH": 123})  # type: ignore[dict-item]
