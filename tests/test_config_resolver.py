"""Tests for canonical security configuration resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avo.config_resolver import (
    AvoSecurityConfig,
    ConfigResolutionError,
    ConfigSource,
    parse_permission_mode,
    render_security_diagnostics,
    resolve_security_config,
)
from avo.permissions import PermissionMode


def test_resolution_precedence_is_explicit_environment_project_user_default(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    user = tmp_path / "user"
    (workspace / ".avo").mkdir(parents=True)
    user.mkdir()
    (workspace / ".avo" / "config.toml").write_text(
        "permission_mode = 'plan'\nsandbox_timeout_seconds = 90\n",
        encoding="utf-8",
    )
    (user / "config.toml").write_text(
        "permission_mode = 'accept_edits'\nsandbox_required = false\n",
        encoding="utf-8",
    )

    config = resolve_security_config(
        explicit={"permission_mode": "bypass_permissions"},
        environ={"AVO_PERMISSION_MODE": "default", "AVO_SANDBOX_TIMEOUT_SECONDS": "45"},
        workspace_root=workspace,
        user_root=user,
    )

    assert config.permission_mode.value is PermissionMode.BYPASS_PERMISSIONS
    assert config.permission_mode.source is ConfigSource.CLI
    assert config.sandbox_timeout_seconds.value == 45.0
    assert config.sandbox_timeout_seconds.source is ConfigSource.ENVIRONMENT
    assert config.sandbox_required.value is False
    assert config.sandbox_required.source is ConfigSource.USER
    assert config.sandbox_network.value is False
    assert config.sandbox_network.source is ConfigSource.DEFAULT


@pytest.mark.parametrize("value", ["nuclear", "", None, 42])
def test_invalid_permission_value_mentions_source(value: object) -> None:
    with pytest.raises(ConfigResolutionError, match="environment"):
        parse_permission_mode(value, source="environment")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AVO_SANDBOX_REQUIRED", "maybe"),
        ("AVO_SANDBOX_NETWORK", "sometimes"),
        ("AVO_SANDBOX_TIMEOUT_SECONDS", "0"),
        ("AVO_SANDBOX_TIMEOUT_SECONDS", "not-a-number"),
    ],
)
def test_invalid_typed_values_fail_with_key_and_source(key: str, value: str) -> None:
    with pytest.raises(ConfigResolutionError, match=f"{key}.*environment"):
        resolve_security_config(environ={key: value})


def test_project_config_must_remain_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.toml"
    workspace.mkdir()
    outside.write_text("permission_mode = 'plan'\n", encoding="utf-8")
    (workspace / ".avo").symlink_to(outside)

    with pytest.raises(ConfigResolutionError, match="inside workspace"):
        resolve_security_config(workspace_root=workspace, user_root=tmp_path / "user")


def test_xdg_user_root_is_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg = tmp_path / "xdg"
    xdg_avo = xdg / "avo"
    xdg_avo.mkdir(parents=True)
    (xdg_avo / "config.json").write_text(
        json.dumps({"permission_mode": "plan"}),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    config = resolve_security_config(environ={"XDG_CONFIG_HOME": str(xdg)})
    assert config.permission_mode.value is PermissionMode.PLAN
    assert config.permission_mode.source is ConfigSource.USER


def test_diagnostics_report_sources_without_secret_contents(tmp_path: Path) -> None:
    config = resolve_security_config(
        explicit={"web_allowed_origin": "http://127.0.0.1:9999"},
        environ={"AVO_TOOLS_REQUIRE_APPROVAL": "run_shell", "AVO_API_KEY": "secret-value"},
        user_root=tmp_path,
    )

    rendered = render_security_diagnostics(config)
    assert "permission_mode=default (default)" in rendered
    assert "web_allowed_origin=http://127.0.0.1:9999 (cli)" in rendered
    assert "require_approval=run_shell (environment)" in rendered
    assert "secret-value" not in rendered
    assert "api_key" not in rendered.lower()


def test_resolver_returns_expected_public_shape(tmp_path: Path) -> None:
    config = resolve_security_config(user_root=tmp_path)
    assert isinstance(config, AvoSecurityConfig)
    assert config.plugin_editable.value is False
    assert config.plugin_activation.value is False
    assert config.web_cors_enabled.value is False
