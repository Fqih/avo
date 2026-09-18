"""Tests for execution policy and final workspace containment checks."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from avo.app_tools.sandbox import ExecutionPolicy, require_execution_policy
from avo.app_tools.workspace import WorkspacePathError, assert_workspace_target


def test_required_sandbox_rejects_host_fallback() -> None:
    policy = ExecutionPolicy(sandbox_required=True, network_enabled=False)
    with pytest.raises(Exception, match=r"sandbox.*run_terminal"):
        require_execution_policy(policy, sandbox_available=False, operation="run_terminal")


def test_explicit_host_policy_is_allowed() -> None:
    policy = ExecutionPolicy(sandbox_required=False, network_enabled=False)
    require_execution_policy(policy, sandbox_available=False, operation="run_terminal")


def test_network_disabled_policy_maps_to_offline_docker_mode() -> None:
    assert ExecutionPolicy(network_enabled=False).network_mode == "none"
    assert ExecutionPolicy(network_enabled=True).network_mode == "bridge"


def test_final_workspace_target_rejects_symlink_replacement(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = root / "target.txt"
    target.write_text("safe", encoding="utf-8")
    target.unlink()
    os.symlink(outside / "escape.txt", target)

    with pytest.raises(WorkspacePathError, match="symlink"):
        assert_workspace_target(root, target)


def test_final_workspace_target_allows_new_contained_file(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = assert_workspace_target(root, root / "new.txt")
    assert target == root / "new.txt"
