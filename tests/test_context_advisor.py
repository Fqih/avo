"""Tests for the context window advisor module."""

from __future__ import annotations

from datetime import UTC, datetime

from avo.context_advisor import (
    estimate_text_tokens,
    evaluate_session_context,
    get_model_context_limit,
)
from avo.storage.conversations import ConversationTurn


def _make_turn(seq: int, role: str, content: str) -> ConversationTurn:
    return ConversationTurn(
        session_id="test-session",
        sequence=seq,
        role=role,
        content=content,
        metadata={},
        created_at=datetime.now(UTC),
    )


def test_get_model_context_limit_known_models() -> None:
    assert get_model_context_limit("claude-3-5-sonnet") == 200_000
    assert get_model_context_limit("gpt-4o-mini") == 128_000
    assert get_model_context_limit("o1-preview") == 128_000
    assert get_model_context_limit("llama3.1:8b") == 128_000
    assert get_model_context_limit("llama3:8b") == 8_192
    assert get_model_context_limit("deepseek-coder:33b") == 64_000
    assert get_model_context_limit("qwen2.5:7b") == 32_768
    assert get_model_context_limit("gemini-1.5-pro") == 1_000_000
    assert get_model_context_limit("unknown-custom-model") == 8_192
    assert get_model_context_limit(None) == 8_192


def test_get_model_context_limit_env_override() -> None:
    env = {"AVO_CONTEXT_WINDOW_LIMIT": "16384"}
    assert get_model_context_limit("claude-3-5-sonnet", environ=env) == 16_384


def test_estimate_text_tokens() -> None:
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("hi") == 1
    # 400 chars ~ 100 tokens
    assert estimate_text_tokens("a" * 400) == 100


def test_evaluate_session_context_safe() -> None:
    turns = [
        _make_turn(1, "user", "Hello"),
        _make_turn(2, "assistant", "Hi there!"),
    ]
    report = evaluate_session_context(turns, "llama3:8b")
    assert report.context_limit == 8_192
    assert report.is_warning is False
    assert report.is_critical is False
    assert report.advice_message is None
    data = report.to_dict()
    assert data["is_warning"] is False


def test_evaluate_session_context_warning_and_critical() -> None:
    # 8,192 limit: 80% is 6,554 tokens (~26,216 chars)
    big_content = "x" * 28_000
    turns = [_make_turn(1, "user", big_content)]
    report = evaluate_session_context(turns, "llama3:8b")
    assert report.is_warning is True
    assert report.advice_message is not None
    assert "/compact" in report.advice_message

    # Critical threshold (>92% of 8,192 limit is 7,536 tokens = ~30,144 chars)
    huge_content = "x" * 32_000
    turns_crit = [_make_turn(1, "user", huge_content)]
    report_crit = evaluate_session_context(turns_crit, "llama3:8b")
    assert report_crit.is_critical is True
    assert "CRITICAL:" in (report_crit.advice_message or "")


def test_evaluate_session_context_explicit_tokens() -> None:
    turns = [_make_turn(1, "user", "small")]
    # Force 7000 tokens on 8192 model -> >85%
    report = evaluate_session_context(turns, "llama3:8b", last_turn_tokens=7_000)
    assert report.estimated_tokens == 7_000
    assert report.is_warning is True
