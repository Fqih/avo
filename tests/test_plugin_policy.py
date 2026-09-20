"""Tests for explicit plugin trust boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avo.cli_plugins import _write_index
from avo.plugin_policy import (
    PluginPolicy,
    PluginPolicyError,
    confirm_plugin_action,
    inspect_plugin_source,
)


def test_metadata_inspection_is_read_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = 'demo-plugin'
version = '1.2.3'
description = 'demo'
[project.entry-points.'avo.tools']
demo = 'demo:register'
""",
        encoding="utf-8",
    )
    before = sorted(path.name for path in tmp_path.iterdir())

    manifest = inspect_plugin_source(str(tmp_path))

    assert manifest.name == "demo-plugin"
    assert manifest.version == "1.2.3"
    assert manifest.groups == ("avo.tools",)
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_remote_preview_does_not_claim_unknown_metadata() -> None:
    manifest = inspect_plugin_source("https://example.test/demo.git")
    assert manifest.version == "unknown"
    assert manifest.groups == ()


def test_invalid_metadata_is_rejected_before_install(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.not-project]\n", encoding="utf-8")
    with pytest.raises(PluginPolicyError, match="no \\[project\\]"):
        inspect_plugin_source(str(tmp_path))


def test_confirmation_requires_exact_operator_token() -> None:
    assert confirm_plugin_action(
        "install demo", confirmation="CONFIRM install demo", interactive=True
    )
    assert not confirm_plugin_action("install demo", confirmation="yes", interactive=True)
    assert confirm_plugin_action(
        "install demo", confirmation="CONFIRM install demo", interactive=False
    )


def test_policy_defaults_to_non_editable_and_inactive() -> None:
    policy = PluginPolicy()
    assert policy.editable_allowed is False
    assert policy.activation_allowed is False


def test_index_helper_writes_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import avo.cli_plugins as plugins

    monkeypatch.setattr(plugins, "PLUGIN_ROOT", tmp_path)
    monkeypatch.setattr(plugins, "PLUGIN_INDEX", tmp_path / "index.json")
    _write_index({"demo": {"source": "local", "editable": False}})
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["demo"]
