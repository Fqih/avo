"""Tests for global Avo setup configuration."""

from __future__ import annotations

import json
from pathlib import Path

from avo.cli_setup import load_global_avo_config, setup_global_avo
from avo.permissions import PermissionMode, permission_policy_from_env


def test_setup_uses_canonical_default_permission_mode(tmp_path: Path) -> None:
    setup_global_avo(tmp_path)

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert config["permission_mode"] == "default"

    policy = permission_policy_from_env(load_global_avo_config(tmp_path))
    assert policy.mode is PermissionMode.DEFAULT


def test_load_global_config_normalizes_explicit_legacy_bypass(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"permission_mode": "bypass"}),
        encoding="utf-8",
    )

    assert load_global_avo_config(tmp_path)["AVO_PERMISSION_MODE"] == "bypass_permissions"

    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert "AVO_PERMISSION_MODE" not in load_global_avo_config(tmp_path)
