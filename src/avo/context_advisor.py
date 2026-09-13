"""Context window capacity estimator and compaction advisor.

Monitors token usage across chat sessions and warns when approaching
the model's maximum context window limit, advising the operator to
trigger `/compact`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from avo.storage.conversations import ConversationTurn


_DEFAULT_FALLBACK_LIMIT = 8_192

# Known model context window limits (conservative defaults)
_MODEL_CONTEXT_LIMITS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"gemini", re.IGNORECASE), 1_000_000),
    (re.compile(r"claude-3", re.IGNORECASE), 200_000),
    (re.compile(r"(gpt-4o|o1|o3)", re.IGNORECASE), 128_000),
    (re.compile(r"llama-?3\.[123]", re.IGNORECASE), 128_000),
    (re.compile(r"llama-?3", re.IGNORECASE), 8_192),
    (re.compile(r"deepseek", re.IGNORECASE), 64_000),
    (re.compile(r"qwen-?2\.5", re.IGNORECASE), 32_768),
    (re.compile(r"mistral", re.IGNORECASE), 32_768),
]


def get_model_context_limit(
    model_name: str | None,
    environ: dict[str, str] | None = None,
) -> int:
    """Return the estimated context limit in tokens for ``model_name``.

    Respects ``AVO_CONTEXT_WINDOW_LIMIT`` environment override if set.
    """
    env = environ if environ is not None else dict(os.environ)
    override = env.get("AVO_CONTEXT_WINDOW_LIMIT", "").strip()
    if override.isdigit():
        return max(1_000, int(override))

    if not model_name:
        return _DEFAULT_FALLBACK_LIMIT

    for pattern, limit in _MODEL_CONTEXT_LIMITS:
        if pattern.search(model_name):
            return limit

    return _DEFAULT_FALLBACK_LIMIT


def estimate_text_tokens(text: str) -> int:
    """Estimate token count from raw text using character heuristic."""
    if not text:
        return 0
    # ~4 characters per token average in English & code
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class ContextAdvisorReport:
    """Analysis of session context usage against model capacity."""

    estimated_tokens: int
    context_limit: int
    usage_percent: float
    is_warning: bool
    is_critical: bool
    warning_threshold: float
    advice_message: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "context_limit": self.context_limit,
            "usage_percent": round(self.usage_percent, 1),
            "is_warning": self.is_warning,
            "is_critical": self.is_critical,
            "advice_message": self.advice_message,
        }


def evaluate_session_context(
    turns: Sequence[ConversationTurn],
    model_name: str | None,
    *,
    last_turn_tokens: int | None = None,
    environ: dict[str, str] | None = None,
) -> ContextAdvisorReport:
    """Assess whether a session's turn history risks exceeding context window."""
    env = environ if environ is not None else dict(os.environ)
    limit = get_model_context_limit(model_name, env)

    threshold_raw = env.get("AVO_CONTEXT_WARNING_THRESHOLD", "0.80").strip()
    try:
        warning_threshold = float(threshold_raw)
    except ValueError:
        warning_threshold = 0.80

    # Calculate token volume: either use explicit last_turn_tokens if available
    # or aggregate estimated tokens from turn contents
    if last_turn_tokens is not None and last_turn_tokens > 0:
        total_tokens = last_turn_tokens
    else:
        total_chars = sum(len(turn.content) for turn in turns)
        total_tokens = estimate_text_tokens("a" * total_chars)

    usage_percent = (total_tokens / limit) * 100.0 if limit > 0 else 0.0
    is_warning = (usage_percent / 100.0) >= warning_threshold
    is_critical = usage_percent >= 92.0

    advice_message: str | None = None
    if is_critical:
        advice_message = (
            f"CRITICAL: Session context is at {usage_percent:.1f}% capacity "
            f"({total_tokens:,} / {limit:,} tokens). "
            f"Run '/compact' immediately to summarize older turns and prevent overflow."
        )
    elif is_warning:
        advice_message = (
            f"Notice: Session context reached {usage_percent:.1f}% capacity "
            f"({total_tokens:,} / {limit:,} tokens). "
            f"Consider running '/compact' to summarize earlier turns."
        )

    return ContextAdvisorReport(
        estimated_tokens=total_tokens,
        context_limit=limit,
        usage_percent=usage_percent,
        is_warning=is_warning,
        is_critical=is_critical,
        warning_threshold=warning_threshold,
        advice_message=advice_message,
    )


__all__ = [
    "ContextAdvisorReport",
    "estimate_text_tokens",
    "evaluate_session_context",
    "get_model_context_limit",
]
