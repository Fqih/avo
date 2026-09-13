"""Tests for the AVO_-prefixed environment loader."""

from __future__ import annotations

import pytest

from avo.config import (
    ConfigError,
    apply_runtime_overrides,
    build_provider_from_env,
    database_path_from_env,
)
from avo.providers.anthropic import AnthropicProvider
from avo.providers.minimax import MiniMaxProvider
from avo.providers.ollama import OllamaProvider
from avo.providers.openai_compatible import OpenAICompatibleProvider


def test_missing_provider_raises() -> None:
    with pytest.raises(ConfigError, match="AVO_PROVIDER"):
        build_provider_from_env({})


def test_unknown_provider_raises() -> None:
    with pytest.raises(ConfigError, match="AVO_PROVIDER"):
        build_provider_from_env({"AVO_PROVIDER": "azure", "AVO_MODEL": "x"})


def test_missing_model_raises() -> None:
    with pytest.raises(ConfigError, match="AVO_MODEL"):
        build_provider_from_env({"AVO_PROVIDER": "ollama"})


def test_ollama_provider_built_with_no_credentials() -> None:
    provider = build_provider_from_env({"AVO_PROVIDER": "ollama", "AVO_MODEL": "llama3.1"})
    assert isinstance(provider, OllamaProvider)
    assert provider._config.model == "llama3.1"
    assert provider._config.base_url == "http://localhost:11434"


def test_ollama_provider_respects_overrides() -> None:
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "ollama",
            "AVO_MODEL": "default-model",
            "AVO_OLLAMA_MODEL": "qwen2.5",
            "AVO_OLLAMA_BASE_URL": "http://gpu.local:11434/",
            "AVO_OLLAMA_API_KEY": "proxy-token",
        }
    )
    assert provider._config.model == "qwen2.5"
    assert provider._config.base_url == "http://gpu.local:11434"
    assert provider._config._api_key == "proxy-token"


def test_minimax_requires_api_key() -> None:
    with pytest.raises(ValueError, match="AVO_MINIMAX_API_KEY"):
        build_provider_from_env({"AVO_PROVIDER": "minimax", "AVO_MODEL": "MiniMax-M3"})


def test_minimax_provider_built() -> None:
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "minimax",
            "AVO_MODEL": "MiniMax-M3",
            "AVO_MINIMAX_API_KEY": "test-key",
        }
    )
    assert isinstance(provider, MiniMaxProvider)
    assert provider._config.model == "MiniMax-M3"
    assert provider._config.api_style == "anthropic"
    headers = provider._config.headers()
    assert headers["x-api-key"] == "test-key"


def test_minimax_rejects_unknown_api_style() -> None:
    with pytest.raises(ValueError, match="AVO_MINIMAX_API_STYLE"):
        build_provider_from_env(
            {
                "AVO_PROVIDER": "minimax",
                "AVO_MODEL": "MiniMax-M3",
                "AVO_MINIMAX_API_KEY": "test-key",
                "AVO_MINIMAX_API_STYLE": "bogus",
            }
        )


def test_minimax_openai_style_uses_bearer() -> None:
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "minimax",
            "AVO_MODEL": "MiniMax-M3",
            "AVO_MINIMAX_API_KEY": "test-key",
            "AVO_MINIMAX_API_STYLE": "openai",
        }
    )
    assert isinstance(provider, MiniMaxProvider)
    headers = provider._config.headers()
    assert headers["Authorization"] == "Bearer test-key"


def test_anthropic_requires_api_key() -> None:
    with pytest.raises(ValueError, match="AVO_ANTHROPIC_API_KEY"):
        build_provider_from_env({"AVO_PROVIDER": "anthropic", "AVO_MODEL": "claude-sonnet-4-6"})


def test_anthropic_provider_built() -> None:
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "anthropic",
            "AVO_MODEL": "claude-sonnet-4-6",
            "AVO_ANTHROPIC_API_KEY": "test-key",
        }
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider._config.model == "claude-sonnet-4-6"
    headers = provider._config.headers()
    assert headers["x-api-key"] == "test-key"
    assert headers["anthropic-version"] == "2023-06-01"


def test_openai_requires_api_key() -> None:
    with pytest.raises(ValueError, match="AVO_OPENAI_API_KEY"):
        build_provider_from_env({"AVO_PROVIDER": "openai", "AVO_MODEL": "gpt-4o-mini"})


def test_openai_provider_built() -> None:
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "openai",
            "AVO_MODEL": "gpt-4o-mini",
            "AVO_OPENAI_API_KEY": "test-key",
            "AVO_OPENAI_BASE_URL": "https://api.example.com/v1/",
        }
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.model == "gpt-4o-mini"
    assert provider._config.base_url == "https://api.example.com/v1"
    headers = provider._config.headers()
    assert headers["Authorization"] == "Bearer test-key"


def test_apply_runtime_overrides_parses_integers_and_floats() -> None:
    kwargs: dict[str, object] = {}
    apply_runtime_overrides(
        kwargs,
        {
            "AVO_MAX_TOTAL_TOKENS": "50000",
            "AVO_MAX_RUNTIME_SECONDS": "120.5",
            "AVO_REPEATED_ACTION_LIMIT": "5",
        },
    )
    assert kwargs == {
        "max_total_tokens": 50000,
        "max_runtime_seconds": 120.5,
        "repeated_action_limit": 5,
    }


def test_apply_runtime_overrides_rejects_garbage() -> None:
    with pytest.raises(ConfigError, match="AVO_MAX_TOTAL_TOKENS"):
        apply_runtime_overrides({}, {"AVO_MAX_TOTAL_TOKENS": "not-a-number"})


def test_database_path_from_env_default_none() -> None:
    assert database_path_from_env({}) is None


def test_database_path_from_env_strips_and_returns() -> None:
    assert database_path_from_env({"AVO_DATABASE_PATH": "  /tmp/avo.db  "}) == ("/tmp/avo.db")


def test_provider_models_catalog_has_all_providers() -> None:
    from avo.config import PROVIDER_MODELS

    assert set(PROVIDER_MODELS.keys()) == {
        "ollama",
        "minimax",
        "anthropic",
        "openai",
        "groq",
        "cerebras",
        "openrouter",
        "router",
    }
    for catalog in PROVIDER_MODELS.values():
        assert catalog, "every catalog must have at least one model"
        assert catalog[0] == catalog[0].strip(), "default model cannot be blank"


def test_default_model_returns_first_catalog_entry() -> None:
    from avo.config import PROVIDER_MODELS, default_model

    for name, catalog in PROVIDER_MODELS.items():
        assert default_model(name) == catalog[0]
    # Case-insensitive lookup so /model accepts the typed form.
    assert default_model("OpenAI") == PROVIDER_MODELS["openai"][0]


def test_default_model_unknown_provider_raises() -> None:
    from avo.config import default_model

    with pytest.raises(ConfigError, match="unknown provider"):
        default_model("mystery")


def test_available_models_empty_for_unknown_provider() -> None:
    from avo.config import available_models

    assert available_models("mystery") == ()


def test_is_known_model_accepts_catalog_and_default_variant() -> None:
    from avo.config import PROVIDER_MODELS, is_known_model

    assert is_known_model("openai", "gpt-4o")
    assert is_known_model("anthropic", "claude-sonnet-4-5")
    # Default case-variant (exact default still accepted).
    default_openai = PROVIDER_MODELS["openai"][0]
    assert is_known_model("openai", default_openai)
    # Random string is rejected.
    assert not is_known_model("openai", "definitely-not-a-model")
    # Unknown provider short-circuits to False.
    assert not is_known_model("mystery", "anything")
