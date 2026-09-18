"""Shared provider metadata for onboarding, help, and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderOption:
    key: str
    label: str
    group: str
    auth_kind: str
    default_model: str
    description: str
    credential_key: str | None
    access_note: str


def provider_options() -> tuple[ProviderOption, ...]:
    return (
        ProviderOption(
            key="ollama",
            label="Ollama Local",
            group="local",
            auth_kind="none",
            default_model="qwen2.5-coder:7b",
            description="Run open models on this computer; no account required.",
            credential_key=None,
            access_note="Free after download; performance depends on local hardware.",
        ),
        ProviderOption(
            key="ollama-cloud",
            label="Ollama Cloud",
            group="cloud",
            auth_kind="api_key_or_device",
            default_model="qwen3-coder:480b-cloud",
            description="Run larger Ollama models remotely.",
            credential_key="ollama",
            access_note="Cloud usage follows the Ollama account, plan, and quota.",
        ),
        ProviderOption(
            key="claude",
            label="Claude account",
            group="account",
            auth_kind="vendor_account",
            default_model="claude-sonnet-4-6",
            description="Open the official Anthropic login in a browser.",
            credential_key="claude",
            access_note="Free web access does not guarantee eligible terminal access.",
        ),
        ProviderOption(
            key="codex",
            label="ChatGPT / Codex account",
            group="account",
            auth_kind="vendor_account",
            default_model="gpt-5.6-sol",
            description="Open the official OpenAI login in a browser.",
            credential_key="codex",
            access_note="Free or paid account; available models and quota depend on the account.",
        ),
        ProviderOption(
            key="gemini-cli",
            label="Gemini account",
            group="account",
            auth_kind="vendor_account",
            default_model="gemini-2.5-pro",
            description="Open the official Google login in a browser.",
            credential_key="gemini",
            access_note="Free or paid Google quota; limits depend on the account and model.",
        ),
        ProviderOption(
            key="openai",
            label="OpenAI API",
            group="api",
            auth_kind="api_key",
            default_model="gpt-4o-mini",
            description="Use an OpenAI-compatible API key.",
            credential_key="openai",
            access_note="API usage follows the project billing and rate limits.",
        ),
        ProviderOption(
            key="anthropic-api",
            label="Anthropic API",
            group="api",
            auth_kind="api_key",
            default_model="claude-sonnet-4-6",
            description="Use an Anthropic API key.",
            credential_key="anthropic",
            access_note="API usage follows the account billing and rate limits.",
        ),
        ProviderOption(
            key="gemini-api",
            label="Gemini API",
            group="api",
            auth_kind="api_key",
            default_model="gemini-2.5-flash",
            description="Use a Google AI Studio API key and its free or paid quota.",
            credential_key="gemini-api",
            access_note="Free tier is limited; paid API access requires billing in the project.",
        ),
        ProviderOption(
            key="minimax",
            label="MiniMax API",
            group="api",
            auth_kind="api_key",
            default_model="MiniMax-M3",
            description="Use the MiniMax API.",
            credential_key="minimax",
            access_note="API usage follows the MiniMax account and rate limits.",
        ),
        ProviderOption(
            key="groq",
            label="Groq API",
            group="api",
            auth_kind="api_key",
            default_model="llama-3.3-70b-versatile",
            description="Use Groq-hosted open models.",
            credential_key="groq",
            access_note="Free or paid access depends on the Groq account and quota.",
        ),
        ProviderOption(
            key="cerebras",
            label="Cerebras API",
            group="api",
            auth_kind="api_key",
            default_model="llama-3.3-70b",
            description="Use Cerebras-hosted open models.",
            credential_key="cerebras",
            access_note="Free or paid access depends on the Cerebras account and quota.",
        ),
        ProviderOption(
            key="openrouter",
            label="OpenRouter API",
            group="api",
            auth_kind="api_key_or_oauth",
            default_model="meta-llama/llama-3.3-70b-instruct:free",
            description="Use free and paid models through OpenRouter.",
            credential_key="openrouter",
            access_note="Model availability, free limits, and billing are provider-specific.",
        ),
        ProviderOption(
            key="router",
            label="Fallback Router",
            group="routing",
            auth_kind="mixed",
            default_model="auto",
            description="Try multiple configured providers in order.",
            credential_key=None,
            access_note="Each route uses its own account, key, model, and quota.",
        ),
        ProviderOption(
            key="combo",
            label="Combo profile",
            group="routing",
            auth_kind="mixed",
            default_model="default",
            description="Use a saved multi-tier provider profile.",
            credential_key=None,
            access_note="Each tier must be authenticated independently before it can run.",
        ),
    )


def get_provider_option(key: str) -> ProviderOption:
    normalized = key.strip().lower()
    aliases = {
        "gemini_cli": "gemini-cli",
        "chatgpt": "codex",
        "anthropic_api": "anthropic-api",
        "gemini_api": "gemini-api",
        "ollama_cloud": "ollama-cloud",
    }
    normalized = aliases.get(normalized, normalized)
    for option in provider_options():
        if option.key == normalized:
            return option
    raise KeyError(f"unknown provider option: {key!r}")
