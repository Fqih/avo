"""Tests for ComboProfile models and combos.json persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.combo.models import ComboProfile, ComboTier
from avo.combo.store import (
    combos_file_path,
    delete_combo,
    get_combo,
    load_combos,
    save_combo,
)


def test_combo_tier_validation() -> None:
    tier = ComboTier(
        name="primary",
        provider="claude",
        model="claude-sonnet-5",
    )
    assert tier.name == "primary"
    assert tier.provider == "claude"
    assert tier.model == "claude-sonnet-5"
    assert tier.timeout_seconds == 60.0
    assert tier.cooldown_seconds == 60.0


def test_load_combos_returns_builtin_presets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    combos = load_combos()
    assert "default" in combos
    assert "coder" in combos
    assert "budget" in combos

    coder = combos["coder"]
    assert len(coder.tiers) >= 2
    assert coder.tiers[0].name == "subscription"
    assert coder.tiers[-1].name == "free"


def test_save_and_get_custom_combo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    profile = ComboProfile(
        name="speedy",
        description="Fast responses first",
        tiers=[
            ComboTier(name="fast", provider="groq", model="llama-3.3-70b-versatile"),
            ComboTier(name="free", provider="ollama", model="llama3.2"),
        ],
    )
    save_combo(profile)

    file_path = combos_file_path()
    assert file_path.is_file()
    # Check permissions 0600
    assert oct(file_path.stat().st_mode & 0o777) == "0o600"

    loaded = get_combo("speedy")
    assert loaded is not None
    assert loaded.name == "speedy"
    assert len(loaded.tiers) == 2
    assert loaded.tiers[0].provider == "groq"
    assert loaded.tiers[1].provider == "ollama"


def test_delete_custom_combo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    profile = ComboProfile(
        name="temp",
        tiers=[ComboTier(name="free", provider="ollama", model="llama3.2")],
    )
    save_combo(profile)
    assert get_combo("temp") is not None

    deleted = delete_combo("temp")
    assert deleted is True
    assert get_combo("temp") is None

    # Cannot delete non-existent or built-in combo
    assert delete_combo("nonexistent") is False
