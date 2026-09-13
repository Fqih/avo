"""AVO_-prefixed environment loader and provider factory.

This module is the single entry point that turns the operator-facing
environment into a ready-to-use ``ModelProvider``. It owns the
``AVO_PROVIDER`` / ``AVO_MODEL`` dispatch table and validates the
required per-provider credentials before any HTTP client is constructed.

The live-benchmark CLI under ``benchmark/live/`` keeps its own legacy env
names (``MODEL_MINIMAX``, ``AUTH_TOKEN``, ``OPENAI_AUTH_TOKEN``, ...) so that
the published reproduction commands stay stable. The new AVO_ names
documented in this module are the official Avo runtime API.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal, cast

ProviderName = Literal[
    "ollama", "minimax", "anthropic", "openai", "groq", "cerebras", "openrouter", "router"
]
_PROVIDER_NAMES: tuple[ProviderName, ...] = (
    "ollama",
    "minimax",
    "anthropic",
    "openai",
    "groq",
    "cerebras",
    "openrouter",
    "router",
)


# Curated catalog of models each provider knows about. Used by the
# `/model` slash command in the chat REPL to render a picker and to
# validate user-typed model names. Entries are ordered so the default
# always sits first — `/model` (no args) prints a numbered list and
# the first entry is the recommended pick.
PROVIDER_MODELS: dict[ProviderName, tuple[str, ...]] = {
    "ollama": (
        "llama3.1",
        "llama3.2",
        "qwen2.5-coder",
        "qwen2.5",
        "mistral",
        "mixtral",
        "codellama",
        "deepseek-coder-v2",
        "phi3",
        "gemma2",
        "command-r",
    ),
    "openai": (
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o1",
        "o1-mini",
        "o1-preview",
        "o3-mini",
        "gpt-3.5-turbo",
    ),
    "anthropic": (
        "claude-sonnet-4-5",
        "claude-opus-4-5",
        "claude-haiku-4-5",
        "claude-3-5-sonnet",
        "claude-3-5-haiku",
        "claude-3-opus",
    ),
    "minimax": (
        "MiniMax-M2",
        "MiniMax-M3",
    ),
    "groq": (
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ),
    "cerebras": (
        "llama-3.3-70b",
        "llama-3.1-8b",
        "qwen-2.5-32b",
    ),
    "openrouter": (
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-r1:free",
        "deepseek/deepseek-chat",
        "google/gemini-2.0-flash-exp:free",
        "qwen/qwen-2.5-coder-32b-instruct:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
        "anthropic/claude-3.5-sonnet",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
    ),
    "router": (
        "auto",
        "ollama,openrouter",
        "ollama,groq",
        "openrouter,anthropic",
    ),
}


def _lookup_catalog(provider_name: str) -> tuple[str, ...]:
    """Resolve ``provider_name`` to its catalog tuple (empty if unknown)."""

    key = cast(ProviderName, provider_name.lower())
    return PROVIDER_MODELS.get(key, ())


def default_model(provider_name: str) -> str:
    """Return the catalog's recommended default for ``provider_name``."""

    catalog = _lookup_catalog(provider_name)
    if not catalog:
        raise ConfigError(f"unknown provider {provider_name!r}; cannot pick a default model.")
    return catalog[0]


def available_models(provider_name: str) -> tuple[str, ...]:
    """Return every catalogued model for ``provider_name``.

    Returns an empty tuple when the provider is unknown so the chat
    REPL degrades gracefully (the operator typed an unsupported name).
    """

    return _lookup_catalog(provider_name)


def is_known_model(provider_name: str, model_name: str) -> bool:
    """Return True if ``model_name`` is in the catalog for ``provider_name``."""

    catalog = _lookup_catalog(provider_name)
    if not catalog:
        return False
    if model_name in catalog:
        return True
    # Allow exact-case variants of the recommended default so the
    # operator can paste whatever AVO_MODEL string they configured.
    return model_name == catalog[0]


class ConfigError(ValueError):
    """Raised when the AVO_-prefixed environment is missing or invalid."""


def build_provider_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    max_completion_tokens: int = 1024,
    request_timeout_seconds: float = 30.0,
) -> Any:
    """Build the configured ``ModelProvider`` from the AVO_ environment.

    Raises ``ConfigError`` for every missing required variable so callers can
    surface a single, actionable error message instead of a deferred
    ``KeyError`` deep inside a provider module.
    """

    env: Mapping[str, str] = os.environ if environ is None else environ

    name = env.get("AVO_PROVIDER", "").strip().lower()
    if name not in _PROVIDER_NAMES:
        allowed = ", ".join(_PROVIDER_NAMES)
        raise ConfigError(f"AVO_PROVIDER must be one of {allowed!s}; got {name!r}")

    model = env.get("AVO_MODEL", "").strip()
    if not model and name != "router":
        raise ConfigError("AVO_MODEL is required")

    if name == "router":
        return _build_router_from_env(
            env,
            max_completion_tokens=max_completion_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )

    if name == "ollama":
        from avo.providers.ollama import OllamaConfig, OllamaProvider

        ollama_config = OllamaConfig.from_avo_env(env, fallback_model=model)
        return OllamaProvider(
            ollama_config,
            max_completion_tokens=max_completion_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )

    if name == "minimax":
        from avo.providers.minimax import MiniMaxConfig, MiniMaxProvider

        minimax_config = MiniMaxConfig.from_avo_env(env, fallback_model=model)
        return MiniMaxProvider(
            minimax_config,
            max_completion_tokens=max_completion_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )

    if name == "anthropic":
        from avo.providers.anthropic import AnthropicConfig, AnthropicProvider

        anthropic_config = AnthropicConfig.from_avo_env(env, fallback_model=model)
        return AnthropicProvider(
            anthropic_config,
            max_completion_tokens=max_completion_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )

    if name == "groq":
        from avo.providers.groq import GroqConfig, GroqProvider

        groq_config = GroqConfig.from_avo_env(env, fallback_model=model)
        return GroqProvider(
            groq_config,
            max_completion_tokens=max_completion_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )

    if name == "cerebras":
        from avo.providers.cerebras import CerebrasConfig, CerebrasProvider

        cerebras_config = CerebrasConfig.from_avo_env(env, fallback_model=model)
        return CerebrasProvider(
            cerebras_config,
            max_completion_tokens=max_completion_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )

    if name == "openrouter":
        from avo.providers.openrouter import OpenRouterConfig, OpenRouterProvider

        openrouter_config = OpenRouterConfig.from_avo_env(env, fallback_model=model)
        return OpenRouterProvider(
            openrouter_config,
            max_completion_tokens=max_completion_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )

    # name == "openai"
    from avo.providers.openai_compatible import (
        OpenAICompatibleConfig,
        OpenAICompatibleProvider,
    )

    openai_config = OpenAICompatibleConfig.from_avo_env(env, fallback_model=model)
    return OpenAICompatibleProvider(
        openai_config,
        max_completion_tokens=max_completion_tokens,
        request_timeout_seconds=request_timeout_seconds,
    )


def _build_router_from_env(
    env: Mapping[str, str],
    *,
    max_completion_tokens: int = 1024,
    request_timeout_seconds: float = 30.0,
) -> Any:
    from avo.providers.router import FallbackRouterProvider

    chain_raw = (
        env.get("AVO_ROUTER_CHAIN", "").strip() or env.get("AVO_ROUTER_PROVIDERS", "").strip()
    )
    if chain_raw:
        requested = [p.strip().lower() for p in chain_raw.split(",") if p.strip()]
    else:
        requested = ["ollama"]
        if (
            env.get("AVO_OPENROUTER_API_KEY", "").strip()
            or env.get("OPENROUTER_API_KEY", "").strip()
        ):
            requested.append("openrouter")
        if env.get("AVO_GROQ_API_KEY", "").strip():
            requested.append("groq")
        if env.get("AVO_CEREBRAS_API_KEY", "").strip():
            requested.append("cerebras")
        if env.get("AVO_ANTHROPIC_API_KEY", "").strip():
            requested.append("anthropic")
        if env.get("AVO_OPENAI_API_KEY", "").strip():
            requested.append("openai")
        if len(requested) == 1:
            requested.append("openrouter")

    routes: list[tuple[str, Any]] = []
    for prov_name in requested:
        prov_model = env.get(f"AVO_{prov_name.upper()}_MODEL", "").strip()
        if not prov_model:
            catalog = _lookup_catalog(prov_name)
            prov_model = catalog[0] if catalog else "default"

        prov_env = dict(env)
        prov_env["AVO_PROVIDER"] = prov_name
        prov_env["AVO_MODEL"] = prov_model
        try:
            prov_instance = build_provider_from_env(
                prov_env,
                max_completion_tokens=max_completion_tokens,
                request_timeout_seconds=request_timeout_seconds,
            )
            routes.append((prov_name, prov_instance))
        except (ConfigError, ValueError) as exc:
            if len(requested) == 1:
                raise ConfigError(
                    f"router provider {prov_name!r} failed to initialize: {exc}"
                ) from exc

    if not routes:
        from avo.providers.ollama import OllamaConfig, OllamaProvider

        ollama_cfg = OllamaConfig.from_avo_env(env, fallback_model=default_model("ollama"))
        routes.append(("ollama", OllamaProvider(ollama_cfg)))

    return FallbackRouterProvider(routes)


def apply_runtime_overrides(policy_kwargs: dict[str, Any], environ: Mapping[str, str]) -> None:
    """Apply AVO_MAX_TOTAL_TOKENS / AVO_MAX_RUNTIME_SECONDS /
    AVO_REPEATED_ACTION_LIMIT.

    Mutates ``policy_kwargs`` in place; missing keys are no-ops so the loader
    never blocks startup.
    """

    tokens = environ.get("AVO_MAX_TOTAL_TOKENS", "").strip()
    if tokens:
        try:
            policy_kwargs["max_total_tokens"] = int(tokens)
        except ValueError as exc:
            raise ConfigError(f"AVO_MAX_TOTAL_TOKENS must be an integer; got {tokens!r}") from exc

    runtime = environ.get("AVO_MAX_RUNTIME_SECONDS", "").strip()
    if runtime:
        try:
            policy_kwargs["max_runtime_seconds"] = float(runtime)
        except ValueError as exc:
            raise ConfigError(f"AVO_MAX_RUNTIME_SECONDS must be a number; got {runtime!r}") from exc

    repeats = environ.get("AVO_REPEATED_ACTION_LIMIT", "").strip()
    if repeats:
        try:
            policy_kwargs["repeated_action_limit"] = int(repeats)
        except ValueError as exc:
            raise ConfigError(
                f"AVO_REPEATED_ACTION_LIMIT must be an integer; got {repeats!r}"
            ) from exc


def database_path_from_env(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the configured SQLite path or ``None`` for in-memory storage."""

    env: Mapping[str, str] = os.environ if environ is None else environ
    value = env.get("AVO_DATABASE_PATH", "").strip()
    return value or None


__all__ = [
    "PROVIDER_MODELS",
    "ConfigError",
    "ProviderName",
    "apply_runtime_overrides",
    "available_models",
    "build_provider_from_env",
    "database_path_from_env",
    "default_model",
    "is_known_model",
]
