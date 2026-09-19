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
from typing import Any, TextIO

from avo.config import available_models


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
    "codex": {
        "label": "ChatGPT (Codex)",
        "default_model": "gpt-5.6-sol",
        "needs_api_key": False,
    },
    "gemini-cli": {
        "label": "Gemini CLI",
        "default_model": "gemini-2.5-pro",
        "needs_api_key": False,
    },
    "claude-account": {
        "label": "Claude Code",
        "default_model": "claude-sonnet-4-6",
        "needs_api_key": False,
    },
    "ollama-cloud": {
        "label": "Ollama Cloud",
        "default_model": "qwen3-coder:480b-cloud",
        "needs_api_key": True,
    },
}

_PROVIDER_DESCRIPTIONS: dict[str, str] = {
    "ollama": "Local free models via Ollama daemon",
    "openai": "OpenAI cloud models (GPT-4o, o3, etc.)",
    "anthropic": "Anthropic API or eligible account (access depends on quota)",
    "minimax": "MiniMax cloud models (MiniMax-M3)",
    "openrouter": "OpenRouter (Free tier models & 300+ endpoints)",
    "router": "Multi-Provider Fallback Router (Auto failover: Ollama -> OpenRouter)",
    "codex": "ChatGPT/Codex account (free or paid quota; browser login)",
    "gemini-cli": "Google account (free or paid quota; browser login)",
    "claude-account": "Claude Code (requires eligible plan or Anthropic API key)",
    "ollama-cloud": "Remote Ollama models (API key/device key; cloud quota)",
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


def _model_choices(provider_key: str, default: str) -> tuple[str, ...]:
    """Return the picker choices with the provider's onboarding default first."""

    aliases = {
        "gemini-cli": "gemini_cli",
        "claude-account": "anthropic",
    }
    catalog = available_models(aliases.get(provider_key, provider_key))
    return (default, *(model for model in catalog if model != default))


def _prompt_model(
    stdin: TextIO,
    stdout: TextIO,
    provider_key: str,
    *,
    default: str,
) -> str:
    """Pick a known model by number while retaining an expert custom-name escape hatch."""

    choices = _model_choices(provider_key, default)
    terminal = (
        hasattr(stdin, "isatty")
        and stdin.isatty()
        and hasattr(stdout, "isatty")
        and stdout.isatty()
    )
    if terminal and provider_key == "gemini-cli":
        try:
            from avo.providers.gemini_cli import discover_antigravity_models

            discovered = discover_antigravity_models()
        except Exception:
            discovered = ()
        if discovered:
            choices = tuple(item.model_id for item in discovered)
            if default not in choices:
                default = choices[0]

    if terminal:
        selected = _prompt_model_tui(stdin, stdout, choices, default=default)
        if selected is not None:
            return selected

    stdout.write("\nAvailable models (actual access depends on account/quota):\n")
    for index, model in enumerate(choices, start=1):
        suffix = " (recommended)" if model == default else ""
        stdout.write(f"  {index}. {model}{suffix}\n")
    stdout.flush()

    raw = _prompt_optional(
        stdin,
        stdout,
        "Select model number or enter a custom model name",
        default="1",
    ).strip()
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(choices):
            return choices[index - 1]
        stdout.write(f"model number must be between 1 and {len(choices)}; using {default}\n")
        stdout.flush()
        return default
    return raw or default


def _prompt_model_tui(
    stdin: TextIO,
    stdout: TextIO,
    choices: tuple[str, ...],
    *,
    default: str,
) -> str | None:
    """Open the onboarding model picker when Avo is running in a terminal."""

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.input import create_input
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.output import create_output
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    labels = {
        model: f"{index}. {model}{' (recommended)' if model == default else ''}"
        for index, model in enumerate(choices, start=1)
    }

    class ModelCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            query = document.text_before_cursor.lower()
            for model in choices:
                label = labels[model]
                if not query or query in label.lower():
                    yield Completion(
                        model,
                        start_position=-len(document.text_before_cursor),
                        display=label,
                    )

    bindings = KeyBindings()

    @bindings.add("down")
    def _next(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is None:
            buffer.start_completion(select_first=True)
        else:
            buffer.complete_next()

    @bindings.add("up")
    def _previous(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is None:
            buffer.start_completion(select_first=True)
        else:
            buffer.complete_previous()

    @bindings.add("escape")
    def _cancel(event: Any) -> None:
        event.app.exit(exception=KeyboardInterrupt)

    stdout.write("\nSelect a model · ↑/↓ choose · Enter select · type to search · Esc cancel\n")
    stdout.flush()
    session: Any = PromptSession(
        input=create_input(stdin),
        output=create_output(stdout),
        completer=ModelCompleter(),
        complete_while_typing=True,
        reserve_space_for_menu=min(8, max(1, len(choices))),
        erase_when_done=True,
        key_bindings=bindings,
        style=Style.from_dict(
            {
                "completion-menu.completion": "bg:#20242b #d8dee9",
                "completion-menu.completion.current": "bg:#42b883 #101418 bold",
                "scrollbar.background": "bg:#20242b",
                "scrollbar.button": "bg:#42b883",
            }
        ),
    )
    try:
        selected = session.prompt(
            "  > ",
            pre_run=lambda: session.default_buffer.start_completion(select_first=True),
        )
    except (EOFError, KeyboardInterrupt):
        stdout.write("\nUsing the recommended model.\n")
        return default
    return selected.strip() if selected.strip() in choices else default


def interactive_first_run_setup(
    stdin: TextIO,
    stdout: TextIO,
    *,
    secret_reader: Callable[[str], str] | None = None,
    vendor_login: Callable[[str], bool] | None = None,
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
        uppercase_key = provider_key.upper().replace("-", "_")

        store_key = {
            "anthropic": "claude",
            "openai": "codex",
            "gemini": "gemini",
            "codex": "codex",
            "gemini-cli": "gemini",
            "claude-account": "claude",
            "ollama-cloud": "ollama",
        }.get(provider_key, provider_key)

        if provider_key in ("codex", "gemini-cli", "claude-account"):
            env["AVO_ALLOW_SUBSCRIPTION"] = "1"
            from avo.oauth.store import get_credential

            stored = get_credential(store_key)
            if stored is None:
                if vendor_login is not None:
                    stdout.write(
                        f"\nOpening the official {provider_label} login in your browser...\n"
                    )
                    stdout.flush()
                    vendor_login("claude-code" if provider_key == "claude-account" else store_key)
                    stored = get_credential(store_key)
                if stored is None:
                    stdout.write(
                        f"\nNote: No stored {store_key} login found. "
                        f"Run 'avo login {store_key}' to authenticate.\n"
                    )

            if provider_key == "claude-account":
                env["AVO_PROVIDER"] = "claude-code"

        if spec["needs_api_key"]:
            from avo.oauth.store import Credential, get_credential, store_credential

            stored = get_credential(store_key)
            if stored is not None:
                acct = f" ({stored.account})" if stored.account else ""
                stdout.write(f"Found stored {stored.kind} login for {provider_label}{acct}.\n")
                reuse = _prompt_optional(
                    stdin,
                    stdout,
                    "Reuse stored login? [Y/n]",
                    default="Y",
                )
                if reuse.strip().lower() in ("y", "yes"):
                    if stored.kind == "oauth":
                        env["AVO_ALLOW_SUBSCRIPTION"] = "1"
                        if provider_key == "openai":
                            env["AVO_PROVIDER"] = "codex"
                        elif provider_key == "gemini":
                            env["AVO_PROVIDER"] = "gemini-cli"
                    elif stored.access_token:
                        env[f"AVO_{uppercase_key}_API_KEY"] = stored.access_token

            if f"AVO_{uppercase_key}_API_KEY" not in env and "AVO_ALLOW_SUBSCRIPTION" not in env:
                api_key = _prompt_required(
                    stdin,
                    stdout,
                    "API Key: ",
                    secret=True,
                    secret_reader=secret_reader,
                )
                env[f"AVO_{uppercase_key}_API_KEY"] = api_key

                if secret_reader is None:
                    save_opt = _prompt_optional(
                        stdin,
                        stdout,
                        "Save to ~/.config/avo/auth.json? [y/N]",
                        default="N",
                    )
                    if save_opt.strip().lower() in ("y", "yes"):
                        store_credential(
                            Credential(
                                provider=store_key,
                                kind="api_key",
                                access_token=api_key,
                            )
                        )

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
            model = _prompt_model(stdin, stdout, provider_key, default=default_model)
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
