from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from avo.oauth.registry import OAUTH, OAuthEntry


@pytest.mark.parametrize("key", ["claude", "codex", "gemini"])
def test_entries_present_and_https(key: str) -> None:
    entry = OAUTH[key]
    assert isinstance(entry, OAuthEntry)
    for url in (entry.authorize_url, entry.token_url):
        assert url.startswith("https://")
    assert entry.code_challenge_method == "S256"


def test_claude_constants_match_upstream() -> None:
    e = OAUTH["claude"]
    assert e.client_id == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert e.authorize_url == "https://claude.ai/oauth/authorize"
    assert e.token_url == "https://api.anthropic.com/v1/oauth/token"
    assert e.exchange_encoding == "json" and e.refresh_lead_seconds == 14400


def test_entry_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        OAUTH["claude"].client_id = "x"  # type: ignore[misc]
