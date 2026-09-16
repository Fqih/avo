"""Tests for ``avo doctor``."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from avo.doctor import render_report, run_doctor


def test_doctor_empty_env_reports_missing_provider() -> None:
    report = run_doctor({})
    assert report.provider is None
    assert "AVO_PROVIDER" in report.missing_vars
    assert report.ok is False
    assert report.config_error is None


def test_doctor_unknown_provider_reports_missing() -> None:
    report = run_doctor({"AVO_PROVIDER": "azure", "AVO_MODEL": "x"})
    assert report.provider is None
    assert "AVO_PROVIDER" in report.missing_vars
    assert report.ok is False


def test_doctor_ollama_only_requires_provider_and_model() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "ollama",
            "AVO_MODEL": "llama3.1",
        }
    )
    assert report.provider == "ollama"
    assert report.model == "llama3.1"
    assert report.has_api_key is False
    assert report.missing_vars == ()
    assert report.ok is True
    assert report.endpoint is not None
    assert report.endpoint.endswith("/api/chat")


def test_doctor_minimax_requires_api_key() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "minimax",
            "AVO_MODEL": "MiniMax-M3",
        }
    )
    assert report.provider == "minimax"
    assert report.missing_vars == ("AVO_MINIMAX_API_KEY",)
    assert report.ok is False


def test_doctor_minimax_full_url_is_not_double_suffixed() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "minimax",
            "AVO_MODEL": "MiniMax-M3",
            "AVO_MINIMAX_API_KEY": "k",
            "AVO_MINIMAX_BASE_URL": "https://api.minimax.io/anthropic",
            "AVO_MINIMAX_API_STYLE": "anthropic",
        }
    )
    assert report.ok is True
    assert report.endpoint == "https://api.minimax.io/anthropic/v1/messages"
    assert report.api_style == "anthropic"
    assert report.has_api_key is True


def test_doctor_anthropic_requires_api_key() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "anthropic",
            "AVO_MODEL": "claude-sonnet-4-6",
        }
    )
    assert report.missing_vars == ("AVO_ANTHROPIC_API_KEY",)
    assert report.ok is False


def test_doctor_openai_requires_api_key() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "openai",
            "AVO_MODEL": "gpt-5.6",
        }
    )
    assert report.missing_vars == ("AVO_OPENAI_API_KEY",)
    assert report.ok is False


def test_doctor_full_anthropic_setup_ok() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "anthropic",
            "AVO_MODEL": "claude-sonnet-4-6",
            "AVO_ANTHROPIC_API_KEY": "ant-key",
        }
    )
    assert report.ok is True
    assert report.endpoint is not None


def test_render_report_ok_prints_endpoint_and_no_missing() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "ollama",
            "AVO_MODEL": "llama3.1",
        }
    )
    out = io.StringIO()
    render_report(report, out=out)
    text = out.getvalue()
    assert "Result: OK." in text
    assert "missing variables" not in text


def test_render_report_not_ok_lists_missing() -> None:
    report = run_doctor({"AVO_PROVIDER": "minimax", "AVO_MODEL": "x"})
    out = io.StringIO()
    render_report(report, out=out)
    text = out.getvalue()
    assert "AVO_MINIMAX_API_KEY" in text
    assert "Result: NOT READY." in text


def test_render_report_redacts_api_key() -> None:
    secret = "sk-THISISTOPSECRET-1234"
    report = run_doctor(
        {
            "AVO_PROVIDER": "openai",
            "AVO_MODEL": "gpt-5.6",
            "AVO_OPENAI_API_KEY": secret,
        }
    )
    out = io.StringIO()
    render_report(report, out=out)
    text = out.getvalue()
    assert secret not in text


def test_run_doctor_openrouter_ok() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "openrouter",
            "AVO_MODEL": "meta-llama/llama-3.3-70b-instruct:free",
            "AVO_OPENROUTER_API_KEY": "sk-or-test",
        }
    )
    assert report.ok
    assert report.provider == "openrouter"
    assert report.endpoint == "https://openrouter.ai/api/v1/chat/completions"
    assert report.has_api_key


def test_run_doctor_router_ok() -> None:
    report = run_doctor(
        {
            "AVO_PROVIDER": "router",
            "AVO_ROUTER_CHAIN": "ollama,openrouter",
        }
    )
    assert report.ok
    assert report.provider == "router"
    assert report.endpoint == "router://ollama,openrouter"


def test_doctor_renders_stored_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from avo.oauth.store import Credential, store_credential

    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    secret_token = "secret-token-xyz-123"
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token=secret_token,
            account="fqih@example.com",
            subscription=True,
        )
    )

    report = run_doctor({"AVO_PROVIDER": "ollama", "AVO_MODEL": "llama3.1"})
    out = io.StringIO()
    render_report(report, out=out)
    text = out.getvalue()

    assert "stored credentials (auth.json):" in text
    assert "claude: oauth (fqih@example.com)" in text
    assert secret_token not in text


def test_doctor_codex_subscription_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from avo.oauth.store import Credential, store_credential

    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    store_credential(
        Credential(
            provider="codex",
            kind="oauth",
            access_token="tok",
            subscription=True,
        )
    )
    report = run_doctor(
        {
            "AVO_PROVIDER": "codex",
            "AVO_MODEL": "gpt-5.6-sol",
            "AVO_ALLOW_SUBSCRIPTION": "1",
        }
    )
    assert report.ok
    assert report.provider == "codex"
    assert report.endpoint == "https://chatgpt.com/backend-api/codex/responses"


def test_doctor_combo_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from avo.combo.models import ComboProfile, ComboTier
    from avo.combo.store import save_combo

    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    profile = ComboProfile(
        name="local_duo",
        tiers=[
            ComboTier(name="t1", provider="ollama", model="llama3.2"),
            ComboTier(name="t2", provider="ollama", model="qwen2.5"),
        ],
    )
    save_combo(profile)

    report = run_doctor(
        {
            "AVO_PROVIDER": "combo",
            "AVO_COMBO": "local_duo",
        }
    )
    assert report.ok
    assert report.provider == "combo"
    assert report.endpoint == "combo://local_duo"
    assert report.api_style == "tiered_failover"
