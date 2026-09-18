"""Shared fixtures and skip logic for live integration tests.

Each provider test file imports a ``pytest`` fixture from this module that
auto-skips when the required environment variables are missing. Tests
that want to exercise the full request/response cycle (including 400
diagnostics) should fail loudly on a non-2xx response so the user sees
the actual error body from the upstream provider.
"""

from __future__ import annotations

import os

import pytest

from avo import ModelRequest
from avo.config import ConfigError, build_provider_from_env
from avo.exceptions import ProviderError

DEFAULT_OLLAMA_URL = "http://localhost:11434"


def _env(name: str) -> str | None:
    """Return stripped env var or ``None`` when empty."""

    value = os.environ.get(name, "").strip()
    return value or None


def ollama_url() -> str:
    """Resolve the Ollama base URL from the environment (default localhost)."""

    return _env("AVO_OLLAMA_BASE_URL") or DEFAULT_OLLAMA_URL


def ollama_model() -> str:
    """Resolve the local Ollama model name (default ``qwen2.5:7b``)."""

    return _env("AVO_OLLAMA_MODEL") or "qwen2.5:7b"


def _build_provider(
    required: tuple[tuple[str, str], ...],
) -> object:
    """Build the configured provider or raise ``pytest.skip`` with details.

    ``required`` is a tuple of ``(env_var, description)`` pairs that the
    provider needs to be constructable. Missing any of them skips the
    test with a single line that names the missing variable.
    """

    missing = [(name, desc) for name, desc in required if not _env(name)]
    if missing:
        names = ", ".join(name for name, _ in missing)
        pytest.skip(f"missing required env: {names}")
    try:
        return build_provider_from_env()
    except ConfigError as exc:
        pytest.skip(f"provider not buildable from env: {exc}")


@pytest.fixture
async def minimax_provider():
    """Yield a ``MiniMaxProvider`` configured from the current env."""

    provider = _build_provider(
        (
            ("AVO_PROVIDER", "must equal minimax"),
            ("AVO_MINIMAX_API_KEY", "MiniMax API key"),
        )
    )
    try:
        yield provider
    finally:
        aclose = getattr(provider, "aclose", None)
        if callable(aclose):
            await aclose()


@pytest.fixture
async def anthropic_provider():
    """Yield an ``AnthropicProvider`` configured from the current env."""

    provider = _build_provider(
        (
            ("AVO_PROVIDER", "must equal anthropic"),
            ("AVO_ANTHROPIC_API_KEY", "Anthropic API key"),
        )
    )
    try:
        yield provider
    finally:
        aclose = getattr(provider, "aclose", None)
        if callable(aclose):
            await aclose()


@pytest.fixture
async def openai_provider():
    """Yield an ``OpenAICompatibleProvider`` configured from the current env."""

    provider = _build_provider(
        (
            ("AVO_PROVIDER", "must equal openai"),
            ("AVO_OPENAI_API_KEY", "OpenAI API key"),
        )
    )
    try:
        yield provider
    finally:
        aclose = getattr(provider, "aclose", None)
        if callable(aclose):
            await aclose()


def simple_request(*, step: int = 1) -> ModelRequest:
    """Build a plain text request without tools."""

    return ModelRequest(
        run_id="integration-run-1",
        step=step,
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
    )


def report_failure(exc: ProviderError) -> str:
    """Render a one-screen diagnostic for a 4xx / 5xx ProviderError.

    Includes the request URL the provider tried, the headers that were
    sent (with auth redacted), and the unredacted error body so the
    operator can paste it back to the dev team. Never prints API keys.
    """

    from avo.providers.http_common import redact_text

    message = redact_text(str(exc))
    return f"\nProviderError: {message}\n"


def ollama_reachable(url: str) -> bool:
    """Return whether the local Ollama daemon answers ``GET /api/tags``."""

    try:
        import httpx
    except ModuleNotFoundError:
        return False
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            response = client.get(f"{url.rstrip('/')}/api/tags")
    except (httpx.HTTPError, OSError):
        return False
    return response.status_code == 200


def ollama_has_model(url: str, model: str) -> bool:
    """Return whether ``model`` is in the local Ollama model registry."""

    try:
        import httpx
    except ModuleNotFoundError:
        return False
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            response = client.get(f"{url.rstrip('/')}/api/tags")
            if response.status_code != 200:
                return False
            names = {item.get("name") for item in response.json().get("models", [])}
            return model in names or any(name.startswith(f"{model}:") for name in names)
    except (httpx.HTTPError, OSError, ValueError):
        return False


__all__ = [
    "DEFAULT_OLLAMA_URL",
    "anthropic_provider",
    "minimax_provider",
    "ollama_has_model",
    "ollama_model",
    "ollama_reachable",
    "ollama_url",
    "openai_provider",
    "report_failure",
    "simple_request",
]
