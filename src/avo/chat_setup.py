"""Interactive first-run setup wizard for ``avo chat``.

When the REPL sees a missing or invalid ``AVO_PROVIDER`` it asks the
operator to pick a provider through a numbered menu, prompts for an API
key through ``getpass`` (never echoing the value), collects optional
per-provider tweaks (base URL, MiniMax API style), and returns a dict
of env-style variables ready to be merged into the runtime
environment. The wizard never writes to disk; the operator is offered
the chance to persist to ``~/.zshrc`` / ``~/.bashrc`` separately.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable
from typing import TextIO


class _SetupAborted(Exception):
    """Raised when the operator aborts the interactive first-run setup."""


_PROVIDER_CATALOG: dict[str, dict[str, str | bool]] = {
    "ollama": {
        "label": "Ollama",
        "default_model": "llama3.1",
        "needs_api_key": False,
    },
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-5.6",
        "needs_api_key": True,
    },
    "anthropic": {
        "label": "Anthropic",
        "default_model": "claude-sonnet-4-6",
        "needs_api_key": True,
    },
    "minimax": {
        "label": "MiniMax",
        "default_model": "MiniMax-M3",
        "needs_api_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "needs_api_key": True,
    },
    "router": {
        "label": "Router",
        "default_model": "auto",
        "needs_api_key": False,
    },
}

_PROVIDER_DESCRIPTIONS: dict[str, str] = {
    "ollama": "Local free models via Ollama daemon",
    "openai": "OpenAI cloud models (GPT-4o, o3, etc.)",
    "anthropic": "Anthropic cloud models (Claude 3.7 Sonnet)",
    "minimax": "MiniMax cloud models (MiniMax-M3)",
    "openrouter": "OpenRouter (Free tier models & 300+ endpoints)",
    "router": "Multi-Provider Fallback Router (Auto failover: Ollama -> OpenRouter)",
}


def _prompt_line(in_stream: TextIO, out_stream: TextIO, prompt: str) -> str:
    """Read one line from ``in_stream`` after printing ``prompt`` to ``out_stream``.

    Strips the trailing newline. Returns an empty string on EOF.
    Raises :class:`_SetupAborted` on EOF.
    """

    out_stream.write(prompt)
    out_stream.flush()
    line = in_stream.readline()
    if not line:
        raise _SetupAborted
    return line.rstrip("\n").rstrip("\r")


def _prompt_choice(in_stream: TextIO, out_stream: TextIO, prompt: str, choices: list[str]) -> str:
    """Prompt until the operator picks one of ``choices`` (case-insensitive).

    Re-prompts on invalid input. Returns the canonical lowercase choice.
    """

    lowered = {c.lower(): c for c in choices}
    while True:
        raw = _prompt_line(in_stream, out_stream, prompt)
        if raw.strip().lower() in lowered:
            return lowered[raw.strip().lower()]
        out_stream.write(f"please choose one of: {', '.join(choices)}\n")
        out_stream.flush()


def _prompt_optional(
    in_stream: TextIO,
    out_stream: TextIO,
    prompt: str,
    *,
    default: str | None = None,
) -> str:
    """Prompt with optional default. Empty input -> default (or empty)."""

    suffix = f" [{default}]" if default else ""
    raw = _prompt_line(in_stream, out_stream, f"{prompt}{suffix}: ").strip()
    if not raw:
        return default or ""
    return raw


def _prompt_required(
    in_stream: TextIO,
    out_stream: TextIO,
    prompt: str,
    *,
    secret: bool = False,
    secret_reader: Callable[[str], str] | None = None,
) -> str:
    """Prompt until the operator supplies a non-empty value.

    When ``secret=True`` the value is read via ``secret_reader`` (defaults
    to :func:`getpass.getpass`) so it is never echoed to the terminal.
    """

    reader = secret_reader or (lambda p: getpass.getpass(p))
    while True:
        value = reader(prompt).strip()
        if value:
            return value
        out_stream.write("value cannot be empty\n")
        out_stream.flush()


def interactive_first_run_setup(
    stdin: TextIO,
    stdout: TextIO,
    *,
    secret_reader: Callable[[str], str] | None = None,
) -> dict[str, str] | None:
    """Prompt the operator through provider, API key, and model selection.

    Returns a dict of env-style variables (``AVO_PROVIDER``,
    ``AVO_<PROVIDER>_API_KEY``, ``AVO_MODEL``, ...) ready to be
    merged into the chat REPL environment. Returns ``None`` if the
    operator aborts (Ctrl+C / Ctrl+D).

    The function never persists secrets to disk. The returned dict lives
    only inside the running process so the API key disappears with the
    process unless the operator also exports it in their shell.
    """

    try:
        stdout.write("\n")
        stdout.write("Avo First-Time Setup\n")
        stdout.write("\n")
        stdout.write("No AI provider has been configured yet.\n")
        stdout.write("\n")
        stdout.write("Select your provider:\n")
        stdout.write("\n")
        for index, key in enumerate(_PROVIDER_CATALOG, start=1):
            label = str(_PROVIDER_CATALOG[key]["label"])
            desc = _PROVIDER_DESCRIPTIONS.get(key, "")
            if desc:
                stdout.write(f"  {index}. {label.ljust(12)} ({desc})\n")
            else:
                stdout.write(f"  {index}. {label}\n")
        stdout.write("\n")

        keys = list(_PROVIDER_CATALOG.keys())
        display_choices = [str(i) for i in range(1, len(keys) + 1)]
        chosen_display = _prompt_choice(
            stdin,
            stdout,
            f"Select provider [1-{len(keys)}]: ",
            display_choices,
        )
        provider_key = keys[int(chosen_display) - 1]
        spec = _PROVIDER_CATALOG[provider_key]
        provider_label = str(spec["label"])

        env: dict[str, str] = {"AVO_PROVIDER": provider_key}
        uppercase_key = provider_key.upper()

        if spec["needs_api_key"]:
            if provider_key == "openrouter":
                from avo.auth import get_stored_token

                stored_token = get_stored_token("openrouter")
                if stored_token:
                    stdout.write("Found stored OpenRouter token from `avo login`.\n")
                    use_stored = _prompt_optional(
                        stdin,
                        stdout,
                        "Use stored token? [Y/n]",
                        default="Y",
                    )
                    if use_stored.strip().lower() in ("y", "yes"):
                        env["AVO_OPENROUTER_API_KEY"] = stored_token

            if f"AVO_{uppercase_key}_API_KEY" not in env:
                api_key = _prompt_required(
                    stdin,
                    stdout,
                    "API Key: ",
                    secret=True,
                    secret_reader=secret_reader,
                )
                env[f"AVO_{uppercase_key}_API_KEY"] = api_key

        # Provider-specific optional tweaks. Each field is asked for with
        # an unambiguous prompt so the operator cannot paste a URL into
        # the wrong field.
        if provider_key == "minimax":
            stdout.write("\nMiniMax API style:\n")
            stdout.write("  1. Anthropic (default)\n")
            stdout.write("  2. OpenAI\n")
            style_choice = _prompt_choice(stdin, stdout, "Select API style [1-2]: ", ["1", "2"])
            env["AVO_MINIMAX_API_STYLE"] = "anthropic" if style_choice == "1" else "openai"
            base_url = _prompt_optional(
                stdin,
                stdout,
                "MiniMax base URL (optional)",
                default="https://api.minimax.io",
            ).strip()
            if base_url:
                env["AVO_MINIMAX_BASE_URL"] = base_url
        elif provider_key == "openai":
            base_url = _prompt_optional(
                stdin,
                stdout,
                "OpenAI base URL (optional)",
                default="https://api.openai.com/v1",
            ).strip()
            if base_url:
                env["AVO_OPENAI_BASE_URL"] = base_url
        elif provider_key == "ollama":
            base_url = _prompt_optional(
                stdin,
                stdout,
                "Ollama base URL (optional)",
                default="http://localhost:11434",
            ).strip()
            if base_url:
                env["AVO_OLLAMA_BASE_URL"] = base_url
        elif provider_key == "openrouter":
            base_url = _prompt_optional(
                stdin,
                stdout,
                "OpenRouter base URL (optional)",
                default="https://openrouter.ai/api/v1",
            ).strip()
            if base_url:
                env["AVO_OPENROUTER_BASE_URL"] = base_url
        elif provider_key == "router":
            stdout.write("\nMulti-Provider Fallback Router Setup\n")
            stdout.write("Avo will try each provider in order until one responds successfully.\n")
            chain = _prompt_optional(
                stdin,
                stdout,
                "Provider fallback chain (comma-separated)",
                default="ollama,openrouter",
            ).strip()
            env["AVO_ROUTER_PROVIDERS"] = chain or "ollama,openrouter"

            chain_providers = [
                p.strip().lower() for p in env["AVO_ROUTER_PROVIDERS"].split(",") if p.strip()
            ]
            if "openrouter" in chain_providers and not env.get("AVO_OPENROUTER_API_KEY"):
                from avo.auth import get_stored_token

                stored_or = get_stored_token("openrouter")
                if stored_or:
                    env["AVO_OPENROUTER_API_KEY"] = stored_or
                else:
                    stdout.write("\nOpenRouter requires an API key for fallback routing:\n")
                    api_key = _prompt_required(
                        stdin,
                        stdout,
                        "OpenRouter API Key: ",
                        secret=True,
                        secret_reader=secret_reader,
                    )
                    env["AVO_OPENROUTER_API_KEY"] = api_key

            models_chain = _prompt_optional(
                stdin,
                stdout,
                "Models fallback chain (comma-separated)",
                default="llama3.1,meta-llama/llama-3.3-70b-instruct:free",
            ).strip()
            env["AVO_ROUTER_MODELS"] = (
                models_chain or "llama3.1,meta-llama/llama-3.3-70b-instruct:free"
            )
            env["AVO_MODEL"] = "auto"

        if provider_key != "router":
            default_model = str(spec["default_model"])
            model = _prompt_optional(stdin, stdout, "Model", default=default_model).strip()
            env["AVO_MODEL"] = model or default_model

        stdout.write(f"\nProvider configured: {provider_label} ({env['AVO_MODEL']})\n")
        stdout.flush()

        # Offer to persist to the operator's shell rc file. Default NO
        # so we never silently modify a config file.
        from .chat_shell_rc import _offer_persist_to_shell_rc, persist_env_to_shell_rc

        if _offer_persist_to_shell_rc(stdin, stdout, env):
            try:
                rc_path = persist_env_to_shell_rc(env)
            except OSError as exc:
                stdout.write(f"warning: could not persist to shell rc: {exc}\n")
                stdout.flush()
            else:
                stdout.write(f"Saved to {rc_path}. New shells will see these variables.\n")
                stdout.flush()

        stdout.write("\nStarting Avo...\n\n")
        stdout.flush()
        return env
    except _SetupAborted:
        stdout.write("\nsetup aborted; no changes applied.\n")
        stdout.flush()
        return None
    except KeyboardInterrupt:
        stdout.write("\nsetup aborted (Ctrl+C); no changes applied.\n")
        stdout.flush()
        return None


__all__ = ["_SetupAborted", "interactive_first_run_setup"]
