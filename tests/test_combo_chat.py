"""Tests for REPL combo routing slash command and failover notices."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from avo import ModelRequest, ModelResponse
from avo.chat import ChatContext, build_chat_context
from avo.chat_commands import _run_slash
from avo.chat_render import SLASH_COMMANDS, _resolve_provider_label
from avo.chat_turn import _run_turn
from avo.combo.models import ComboProfile, ComboTier
from avo.combo.provider import ComboRouterProvider
from avo.combo.store import save_combo
from avo.exceptions import ProviderError
from avo.providers.base import ModelProvider


class _FailingProvider(ModelProvider):
    """Provider that raises rate limit error."""

    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise ProviderError("HTTP 429: Too Many Requests (Rate limit exceeded)")


class _SucceedingProvider(ModelProvider):
    """Provider that returns a successful completion."""

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="Hello from fallback tier!",
        )


@pytest.fixture
def chat_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatContext:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    db_path = tmp_path / "chat.db"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    environ = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "fake-model",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }
    return build_chat_context(
        database_path=db_path,
        workspace_root=workspace_root,
        environ=environ,
    )


def test_slash_commands_catalog_has_combo() -> None:
    found = any(name.startswith("/combo") for name, _ in SLASH_COMMANDS)
    assert found, "SLASH_COMMANDS must include /combo"


def test_resolve_provider_label_for_combo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    env = {"AVO_PROVIDER": "combo", "AVO_COMBO": "default"}
    prov, model_desc = _resolve_provider_label(env)
    assert prov == "combo"
    assert "default" in model_desc
    assert "subscription" in model_desc


async def test_slash_combo_when_not_active(chat_context: ChatContext) -> None:
    out = io.StringIO()
    err = io.StringIO()
    environ = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "fake-model",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }

    exited = await _run_slash(chat_context, ["/combo"], out, err, environ)
    assert not exited
    output = out.getvalue()
    assert "Combo routing is not active" in output
    assert "Available profiles:" in output
    assert "default" in output


async def test_slash_combo_switch_nonexistent(chat_context: ChatContext) -> None:
    out = io.StringIO()
    err = io.StringIO()
    environ = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "fake-model",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }

    exited = await _run_slash(chat_context, ["/combo", "non_existent_xyz"], out, err, environ)
    assert not exited
    assert "combo profile 'non_existent_xyz' not found" in err.getvalue()


async def test_slash_combo_status_and_switch(
    chat_context: ChatContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    profile = ComboProfile(
        name="local_duo",
        description="Two local models",
        tiers=[
            ComboTier(name="fast", provider="ollama", model="llama3.2"),
            ComboTier(name="smart", provider="ollama", model="qwen2.5"),
        ],
    )
    save_combo(profile)

    out = io.StringIO()
    err = io.StringIO()
    environ = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "fake-model",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }

    # Switch to local_duo
    exited = await _run_slash(chat_context, ["/combo", "local_duo"], out, err, environ)
    assert not exited
    assert "Switched to combo profile 'local_duo'" in out.getvalue()
    assert chat_context.provider_name == "combo"
    assert chat_context.model_name == "local_duo"
    assert isinstance(chat_context.runtime.provider, ComboRouterProvider)

    # Now inspect status
    out_status = io.StringIO()
    exited = await _run_slash(chat_context, ["/combo"], out_status, err, environ)
    assert not exited
    status_text = out_status.getvalue()
    assert "Active Combo Profile: local_duo" in status_text
    assert "Two local models" in status_text
    assert "fast" in status_text
    assert "smart" in status_text
    assert "HEALTHY" in status_text


async def test_run_turn_renders_failover_notice(chat_context: ChatContext) -> None:
    profile = ComboProfile(
        name="test_failover",
        tiers=[
            ComboTier(name="primary", provider="anthropic", model="claude-3-5-sonnet"),
            ComboTier(name="fallback", provider="ollama", model="llama3.2"),
        ],
    )
    tiers = [
        (profile.tiers[0], _FailingProvider()),
        (profile.tiers[1], _SucceedingProvider()),
    ]
    combo_provider = ComboRouterProvider(profile, tiers=tiers)
    chat_context.runtime.provider = combo_provider
    chat_context.provider_name = "combo"
    chat_context.model_name = "test_failover"

    out = io.StringIO()
    err = io.StringIO()

    await _run_turn(chat_context, "Hello assistant", out, err)

    output = out.getvalue()
    assert "Fallback: switched from 'primary' (anthropic) to 'fallback' (ollama)" in output
    assert "rate_limited_429" in output
    assert "Hello from fallback tier!" in output
