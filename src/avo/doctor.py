"""``avo doctor`` — verify AVO_ setup without an HTTP call.

Reads ``os.environ`` (or an injected mapping) and reports which
AVO_* variables are present, which are missing, and what the
resulting provider endpoint would be. Never sends a request to the
provider; use :mod:`avo.chat` for an actual smoke test.

The doctor is intentionally synchronous and dependency-free so it works
in any shell where ``avo`` is installed, even with no network
access.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import IO, Any

from avo.config import _PROVIDER_NAMES, build_provider_from_env

_PROVIDER_LABELS = {
    "ollama": "Ollama",
    "ollama-cloud": "Ollama Cloud",
    "openai": "OpenAI-compatible",
    "anthropic": "Anthropic",
    "minimax": "MiniMax",
    "groq": "Groq",
    "cerebras": "Cerebras",
    "openrouter": "OpenRouter",
    "gemini": "Google Gemini",
    "codex": "ChatGPT Codex account",
    "gemini_cli": "Google Gemini CLI account",
    "gemini-cli": "Google Gemini CLI account",
    "router": "Multi-Provider Router",
    "combo": "Multi-Tier Combo Router",
}

_REQUIRED_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "ollama": ("AVO_PROVIDER", "AVO_MODEL"),
    "ollama-cloud": ("AVO_PROVIDER", "AVO_MODEL", "AVO_OLLAMA_CLOUD_API_KEY"),
    "openai": ("AVO_PROVIDER", "AVO_MODEL", "AVO_OPENAI_API_KEY"),
    "anthropic": ("AVO_PROVIDER", "AVO_MODEL", "AVO_ANTHROPIC_API_KEY"),
    "minimax": ("AVO_PROVIDER", "AVO_MODEL", "AVO_MINIMAX_API_KEY"),
    "groq": ("AVO_PROVIDER", "AVO_MODEL", "AVO_GROQ_API_KEY"),
    "cerebras": ("AVO_PROVIDER", "AVO_MODEL", "AVO_CEREBRAS_API_KEY"),
    "openrouter": ("AVO_PROVIDER", "AVO_MODEL", "AVO_OPENROUTER_API_KEY"),
    "gemini": ("AVO_PROVIDER", "AVO_MODEL", "AVO_GEMINI_API_KEY"),
    "codex": ("AVO_PROVIDER", "AVO_MODEL"),
    "gemini_cli": ("AVO_PROVIDER", "AVO_MODEL"),
    "gemini-cli": ("AVO_PROVIDER", "AVO_MODEL"),
    "router": ("AVO_PROVIDER",),
    "combo": ("AVO_PROVIDER",),
}


@dataclass(frozen=True)
class DoctorReport:
    """Structured result of one ``avo doctor`` invocation."""

    provider: str | None
    model: str | None
    base_url: str | None
    endpoint: str | None
    api_style: str | None
    has_api_key: bool
    missing_vars: tuple[str, ...]
    config_error: str | None
    extra_vars: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when every required variable is set and provider config is buildable."""

        return not self.missing_vars and self.config_error is None


def _provider_for(env: Mapping[str, str]) -> str | None:
    raw = env.get("AVO_PROVIDER", "").strip().lower()
    if raw in _PROVIDER_NAMES:
        return raw
    return None


def _endpoint_for(env: Mapping[str, str], provider: str) -> tuple[str | None, str | None]:
    """Return ``(endpoint, api_style)`` by introspecting each provider's config class."""

    fallback_model = env.get("AVO_MODEL", "x")

    if provider in ("ollama", "ollama-cloud"):
        from avo.providers.ollama import OllamaConfig

        try:
            cloud_env = dict(env)
            if provider == "ollama-cloud":
                cloud_env.setdefault("AVO_OLLAMA_BASE_URL", "https://ollama.com")
            ollama_cfg: Any = OllamaConfig.from_avo_env(cloud_env, fallback_model=fallback_model)
        except (ValueError, KeyError):
            return None, None
        return ollama_cfg.endpoint, None

    if provider == "minimax":
        from avo.providers.minimax import MiniMaxConfig

        try:
            minimax_cfg: Any = MiniMaxConfig.from_avo_env(env, fallback_model=fallback_model)
        except (ValueError, KeyError):
            return None, env.get("AVO_MINIMAX_API_STYLE", "anthropic")
        return minimax_cfg.endpoint, minimax_cfg.api_style

    if provider == "anthropic":
        from avo.providers.anthropic import AnthropicConfig

        try:
            anthropic_cfg: Any = AnthropicConfig.from_avo_env(env, fallback_model=fallback_model)
        except (ValueError, KeyError):
            return None, None
        return anthropic_cfg.endpoint, None

    if provider == "openai":
        from avo.providers.openai_compatible import OpenAICompatibleConfig

        try:
            openai_cfg: Any = OpenAICompatibleConfig.from_avo_env(
                env, fallback_model=fallback_model
            )
        except (ValueError, KeyError):
            return None, None
        return openai_cfg.endpoint, None

    if provider == "groq":
        from avo.providers.groq import GroqConfig

        try:
            groq_cfg: Any = GroqConfig.from_avo_env(env, fallback_model=fallback_model)
        except (ValueError, KeyError):
            return None, None
        return groq_cfg.endpoint, None

    if provider == "cerebras":
        from avo.providers.cerebras import CerebrasConfig

        try:
            cerebras_cfg: Any = CerebrasConfig.from_avo_env(env, fallback_model=fallback_model)
        except (ValueError, KeyError):
            return None, None
        return cerebras_cfg.endpoint, None

    if provider == "openrouter":
        from avo.providers.openrouter import OpenRouterConfig

        try:
            openrouter_cfg: Any = OpenRouterConfig.from_avo_env(env, fallback_model=fallback_model)
        except (ValueError, KeyError):
            return None, None
        return openrouter_cfg.endpoint, None

    if provider == "codex":
        from avo.providers.codex import CodexConfig

        try:
            codex_cfg: Any = CodexConfig.from_avo_env(env, fallback_model=fallback_model)
        except Exception:
            return None, None
        return codex_cfg.endpoint, None

    if provider in ("gemini_cli", "gemini-cli"):
        from avo.providers.gemini_cli import GeminiCliConfig

        try:
            gemini_cli_cfg: Any = GeminiCliConfig.from_avo_env(env, fallback_model=fallback_model)
        except Exception:
            return None, None
        return gemini_cli_cfg.endpoint, None

    if provider == "gemini":
        from avo.providers.gemini import GeminiConfig

        try:
            gemini_cfg: Any = GeminiConfig.from_avo_env(env, fallback_model=fallback_model)
        except (ValueError, KeyError):
            return None, None
        return gemini_cfg.endpoint, None

    if provider == "router":
        chain = (
            env.get("AVO_ROUTER_CHAIN", "").strip()
            or env.get("AVO_ROUTER_PROVIDERS", "").strip()
            or "auto"
        )
        return f"router://{chain}", "fallback"

    if provider == "combo":
        combo_name = (
            env.get("AVO_COMBO", "").strip() or env.get("AVO_MODEL", "").strip() or "default"
        )
        return f"combo://{combo_name}", "tiered_failover"

    return None, None


def run_doctor(environ: Mapping[str, str] | None = None) -> DoctorReport:
    """Inspect ``environ`` (defaults to ``os.environ``) and return a report.

    Never raises; surfaces every failure as a field on :class:`DoctorReport`.
    """

    env_dict = dict(os.environ if environ is None else environ)
    if environ is None and ("AVO_PROVIDER" not in env_dict or not env_dict["AVO_PROVIDER"].strip()):
        try:
            from avo.cli_setup import load_global_avo_config

            for k, v in load_global_avo_config().items():
                env_dict.setdefault(k, v)
        except Exception:
            pass
    env: Mapping[str, str] = env_dict

    provider = _provider_for(env)
    model = env.get("AVO_MODEL", "").strip() or None
    base_url: str | None = None
    endpoint: str | None = None
    api_style: str | None = None
    has_api_key = False
    missing: list[str] = []
    config_error: str | None = None

    if provider is None:
        missing.append("AVO_PROVIDER")
        missing.extend(_REQUIRED_BY_PROVIDER["ollama"][1:])
    else:
        from avo.auth import get_stored_token

        required = _REQUIRED_BY_PROVIDER[provider]
        for var in required:
            if var == "AVO_OPENROUTER_API_KEY" and (
                env.get("OPENROUTER_API_KEY", "").strip() or get_stored_token("openrouter")
            ):
                continue
            if var == "AVO_OLLAMA_CLOUD_API_KEY" and get_stored_token("ollama"):
                continue
            if not env.get(var, "").strip():
                missing.append(var)

        api_key_var = (
            "AVO_OLLAMA_CLOUD_API_KEY"
            if provider == "ollama-cloud"
            else f"AVO_{provider.upper()}_API_KEY"
        )
        has_api_key = bool(
            env.get(api_key_var, "").strip()
            or (provider == "openrouter" and env.get("OPENROUTER_API_KEY", "").strip())
            or (provider == "openrouter" and get_stored_token("openrouter"))
            or (provider == "ollama-cloud" and get_stored_token("ollama"))
        )

        base_url_key = f"AVO_{provider.upper()}_BASE_URL"
        base_url = env.get(base_url_key, "").strip() or None

        endpoint, api_style = _endpoint_for(env, provider)

        if not missing:
            try:
                build_provider_from_env(env)
            except Exception as exc:
                config_error = str(exc)

    extra = tuple(sorted(k for k in env if k.startswith("AVO_") and k not in set(missing)))

    return DoctorReport(
        provider=provider,
        model=model,
        base_url=base_url,
        endpoint=endpoint,
        api_style=api_style,
        has_api_key=has_api_key,
        missing_vars=tuple(missing),
        config_error=config_error,
        extra_vars=extra,
    )


def render_report(report: DoctorReport, *, out: IO[str]) -> None:
    """Print a human-readable report to ``out`` without leaking secrets."""

    if report.provider is None:
        out.write("AVO provider: (unset)\n")
        out.write("  set AVO_PROVIDER to one of: " + ", ".join(_PROVIDER_NAMES) + "\n")
    else:
        label = _PROVIDER_LABELS.get(report.provider, report.provider)
        out.write(f"AVO provider: {label}\n")

    if report.model:
        out.write(f"AVO model: {report.model}\n")
    else:
        out.write("AVO model: (unset)\n")

    if report.base_url:
        out.write(f"base URL: {report.base_url}\n")
    elif report.provider is not None:
        out.write("base URL: (provider default)\n")

    if report.api_style:
        out.write(f"API style: {report.api_style}\n")

    if report.endpoint:
        out.write(f"endpoint: {report.endpoint}\n")

    out.write(f"API key configured: {'yes' if report.has_api_key else 'no'}\n")

    if report.missing_vars:
        out.write("missing variables:\n")
        for var in report.missing_vars:
            out.write(f"  - {var}\n")

    if report.config_error:
        out.write(f"config error: {report.config_error}\n")

    from avo.oauth.store import load_all_credentials

    try:
        stored_creds = load_all_credentials()
        if stored_creds:
            out.write("\nstored credentials (auth.json):\n")
            for _key, cred in sorted(stored_creds.items()):
                acct = f" ({cred.account})" if cred.account else ""
                exp = ""
                if cred.expires_at:
                    exp = f", expires {cred.expires_at.strftime('%Y-%m-%d %H:%M')}"
                out.write(f"  - {cred.provider}: {cred.kind}{acct}{exp}\n")
    except Exception:
        pass

    if report.ok:
        out.write("\nResult: OK. Provider config is buildable.\n")
        out.write(
            "Note: this only checks the configuration; it does not call "
            "the provider. Run `avo chat` for an end-to-end smoke test.\n"
        )
    else:
        out.write("\nResult: NOT READY. Fix the issues above, then retry.\n")

    out.flush()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, run doctor, return exit status.

    When called from another entry point (e.g. the ``avo`` CLI
    dispatcher) pass an empty ``argv`` so the parser does not re-read
    ``sys.argv`` and reject the parent command's tail.
    """

    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="avo doctor",
        description="Verify AVO_ provider configuration without HTTP.",
    )
    parser.parse_args(argv if argv is not None else sys.argv[1:])
    report = run_doctor()
    render_report(report, out=sys.stdout)
    return 0 if report.ok else 1


__all__ = [
    "DoctorReport",
    "main",
    "render_report",
    "run_doctor",
]
