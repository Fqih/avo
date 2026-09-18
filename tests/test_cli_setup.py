"""Tests for global Avo setup configuration."""

from __future__ import annotations

import json
from pathlib import Path

from avo.cli_setup import load_global_avo_config, remember_last_provider, setup_global_avo
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


def test_setup_can_explicitly_enable_subscription_inference(tmp_path: Path) -> None:
    setup_global_avo(tmp_path, allow_subscription=True)

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert config["allow_subscription"] is True
    assert load_global_avo_config(tmp_path)["AVO_ALLOW_SUBSCRIPTION"] == "1"


def test_remember_last_provider_persists_only_non_secret_runtime_state(tmp_path: Path) -> None:
    setup_global_avo(tmp_path)

    remember_last_provider(
        {
            "AVO_PROVIDER": "codex",
            "AVO_MODEL": "gpt-5.6-luna",
            "AVO_ALLOW_SUBSCRIPTION": "1",
            "AVO_CODEX_API_KEY": "must-not-be-written",
        },
        tmp_path,
    )

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert config["provider"] == "codex"
    assert config["model"] == "gpt-5.6-luna"
    assert config["allow_subscription"] is True
    assert "API_KEY" not in json.dumps(config)
