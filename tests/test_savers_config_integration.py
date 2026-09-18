"""Provider-factory integration tests for token savers."""

from __future__ import annotations

import pytest

from avo.combo.models import ComboProfile, ComboTier
from avo.combo.provider import ComboRouterProvider
from avo.combo.store import save_combo
from avo.config import ConfigError, build_provider_from_env
from avo.providers.ollama import OllamaProvider
from avo.savers.provider import SaverProvider


def test_factory_leaves_provider_unwrapped_when_saver_is_unset() -> None:
    provider = build_provider_from_env({"AVO_PROVIDER": "ollama", "AVO_MODEL": "llama3.1"})
    assert isinstance(provider, OllamaProvider)
    assert not isinstance(provider, SaverProvider)


def test_factory_wraps_provider_when_saver_is_selected() -> None:
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "ollama",
            "AVO_MODEL": "llama3.1",
            "AVO_SAVER": "compact",
        }
    )
    assert isinstance(provider, SaverProvider)
    assert isinstance(provider.inner, OllamaProvider)
    assert provider.preset.name == "compact"


def test_factory_rejects_unknown_saver_with_valid_names() -> None:
    with pytest.raises(ConfigError, match=r"terse.*compact"):
        build_provider_from_env(
            {
                "AVO_PROVIDER": "ollama",
                "AVO_MODEL": "llama3.1",
                "AVO_SAVER": "unknown",
            }
        )


def test_factory_wraps_combo_once_and_keeps_tiers_unwrapped() -> None:
    save_combo(
        ComboProfile(
            name="saver-test",
            description="test",
            tiers=(ComboTier(name="local", provider="ollama", model="llama3.1"),),
        )
    )
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "combo",
            "AVO_COMBO": "saver-test",
            "AVO_SAVER": "compact",
        }
    )
    assert isinstance(provider, SaverProvider)
    assert isinstance(provider.inner, ComboRouterProvider)
    assert isinstance(provider.inner._tiers[0][1], OllamaProvider)
    assert not isinstance(provider.inner._tiers[0][1], SaverProvider)
