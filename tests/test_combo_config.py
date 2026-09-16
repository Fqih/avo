"""Tests for combo provider factory wiring in avo.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.combo.models import ComboProfile, ComboTier
from avo.combo.provider import ComboRouterProvider
from avo.combo.store import save_combo
from avo.config import ConfigError, build_provider_from_env, supported_providers


def test_supported_providers_includes_combo() -> None:
    assert "combo" in supported_providers()


def test_build_provider_from_env_builds_combo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    profile = ComboProfile(
        name="test_local",
        tiers=[
            ComboTier(name="free1", provider="ollama", model="llama3.2"),
            ComboTier(name="free2", provider="ollama", model="qwen2.5"),
        ],
    )
    save_combo(profile)

    env = {
        "AVO_PROVIDER": "combo",
        "AVO_COMBO": "test_local",
    }
    provider = build_provider_from_env(env)
    assert isinstance(provider, ComboRouterProvider)
    assert provider.profile.name == "test_local"
    assert len(provider.tiers) == 2
    assert provider.tiers[0][0].name == "free1"
    assert provider.tiers[1][0].name == "free2"


def test_build_provider_from_env_unknown_combo_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    env = {
        "AVO_PROVIDER": "combo",
        "AVO_COMBO": "non_existent_profile_xyz",
    }
    with pytest.raises(ConfigError, match="Combo profile 'non_existent_profile_xyz' not found"):
        build_provider_from_env(env)


def test_build_provider_from_env_nested_combo_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    profile = ComboProfile(
        name="recursive",
        tiers=[
            ComboTier(name="t1", provider="combo", model="default"),
        ],
    )
    save_combo(profile)
    env = {"AVO_PROVIDER": "combo", "AVO_COMBO": "recursive"}
    with pytest.raises(ConfigError, match="Nested combo tiers are not supported"):
        build_provider_from_env(env)
