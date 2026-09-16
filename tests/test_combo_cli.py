"""Tests for `avo combo` CLI subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avo.cli import main as cli_main
from avo.combo.cli import main as combo_main
from avo.combo.store import get_combo


def test_combo_cli_list_presets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(["list"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "default" in captured
    assert "coder" in captured
    assert "budget" in captured


def test_combo_cli_default_action_is_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main([])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "default" in captured


def test_combo_cli_list_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(["list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "default" in data
    assert "coder" in data
    assert "budget" in data
    assert len(data["default"]["tiers"]) >= 2


def test_combo_cli_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(["show", "coder"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Profile     : coder" in captured
    assert "Claude" in captured


def test_combo_cli_show_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(["show", "coder", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "coder"
    assert len(data["tiers"]) >= 2


def test_combo_cli_show_nonexistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(["show", "unknown_profile_xyz"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "profile 'unknown_profile_xyz' not found" in err


def test_combo_cli_new_and_rm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(
        [
            "new",
            "my_combo",
            "--tier",
            "primary:anthropic:claude-3-5-sonnet-latest",
            "--tier",
            "cheap:openrouter:meta-llama/llama-3.3-70b-instruct:45:30",
            "--tier",
            "ollama:llama3.2",
            "--description",
            "My personal combo",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Saved combo profile 'my_combo' with 3 tier(s)." in out

    profile = get_combo("my_combo")
    assert profile is not None
    assert profile.name == "my_combo"
    assert profile.description == "My personal combo"
    assert len(profile.tiers) == 3
    assert profile.tiers[0].name == "primary"
    assert profile.tiers[0].provider == "anthropic"
    assert profile.tiers[1].timeout_seconds == 45.0
    assert profile.tiers[1].cooldown_seconds == 30.0
    assert profile.tiers[2].name == "tier_3"
    assert profile.tiers[2].provider == "ollama"

    # Remove profile
    rc_rm = combo_main(["rm", "my_combo"])
    assert rc_rm == 0
    assert "Removed combo profile 'my_combo'." in capsys.readouterr().out
    assert get_combo("my_combo") is None


def test_combo_cli_new_validation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))

    # Reject nested combo
    rc = combo_main(["new", "bad_nested", "--tier", "t1:combo:default"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "nested combo tiers are not supported" in err

    # Reject unsupported provider
    rc = combo_main(["new", "bad_prov", "--tier", "t1:unknown_provider_foo:model1"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unsupported provider 'unknown_provider_foo'" in err

    # Reject invalid tier spec format
    rc = combo_main(["new", "bad_format", "--tier", "single_token"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid tier spec" in err


def test_combo_cli_rm_builtin_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(["rm", "default"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot delete built-in combo profile 'default'" in err


def test_combo_cli_rm_nonexistent_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = combo_main(["rm", "nonexistent_xyz"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "profile 'nonexistent_xyz' not found" in err


def test_parent_cli_delegates_to_combo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    rc = cli_main(["combo", "list"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "default" in captured
