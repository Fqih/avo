from __future__ import annotations

from avo.provider_catalog import get_provider_option, provider_options


def test_catalog_groups_local_cloud_account_and_api_choices() -> None:
    options = {option.key: option for option in provider_options()}

    assert options["ollama"].group == "local"
    assert options["ollama-cloud"].group == "cloud"
    assert options["codex"].auth_kind == "vendor_account"
    assert options["gemini-cli"].auth_kind == "vendor_account"
    assert options["openai"].auth_kind == "api_key"


def test_web_and_cli_account_routes_are_distinct() -> None:
    options = {option.key: option for option in provider_options()}

    assert options["claude-web"].auth_kind == "browser_only"
    assert options["claude-code"].credential_key == "claude"
    assert options["chatgpt-web"].auth_kind == "browser_only"
    assert options["codex"].credential_key == "codex"


def test_account_options_describe_quota_without_claiming_paid_access() -> None:
    options = {option.key: option for option in provider_options()}

    assert "subscription required" not in options["codex"].access_note.lower()
    assert "free" in options["codex"].access_note.lower()
    assert "quota" in options["gemini-cli"].access_note.lower()
    assert "eligible" in options["claude-code"].access_note.lower()


def test_provider_option_resolves_canonical_key_and_rejects_unknown() -> None:
    assert get_provider_option("gemini_cli").key == "gemini-cli"
    assert get_provider_option("chatgpt").key == "chatgpt-web"

    try:
        get_provider_option("does-not-exist")
    except KeyError as exc:
        assert "does-not-exist" in str(exc)
    else:
        raise AssertionError("unknown provider should raise KeyError")
