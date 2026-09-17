from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from avo import ModelRequest, ToolMetadata
from avo.auth import AuthError
from avo.exceptions import ProviderError
from avo.oauth.store import Credential, store_credential
from avo.providers.anthropic import AnthropicConfig, AnthropicProvider


class _Response:
    def __init__(self, status_code: int, text: str, json_payload: Any) -> None:
        self.status_code = status_code
        self.text = text
        self._json = json_payload

    def json(self) -> Any:
        return self._json


class FakeClient:
    def __init__(self, responses: list[_Response] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,  # noqa: ASYNC109
    ) -> Any:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if not self.responses:
            return _Response(200, "", {"content": [{"type": "text", "text": "ok"}]})
        return self.responses.pop(0)


def _request() -> ModelRequest:
    return ModelRequest(
        run_id="run-test",
        step=1,
        messages=[{"role": "user", "content": "hi"}],
        tools=[ToolMetadata(name="echo", description="echo", input_schema={"type": "object"})],
    )


@pytest.fixture
def store_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))


def test_anthropic_config_oauth_endpoint_and_headers() -> None:
    config = AnthropicConfig.oauth(model="claude-sonnet-4-6")
    assert config.auth_mode == "oauth"
    assert config.endpoint == "https://api.anthropic.com/v1/messages?beta=true"

    headers = config.request_headers("my-access-token")
    assert headers["Authorization"] == "Bearer my-access-token"
    assert "x-api-key" not in headers
    assert "claude-code-20250219" in headers["anthropic-beta"]
    assert headers["anthropic-version"] == "2023-06-01"


def test_anthropic_config_api_key_mode_regression() -> None:
    config = AnthropicConfig(model="claude-sonnet-4-6")
    config._api_key = "ant-key"
    assert config.auth_mode == "api_key"
    assert config.endpoint == "https://api.anthropic.com/v1/messages"

    headers = config.headers()
    assert headers["x-api-key"] == "ant-key"
    assert "Authorization" not in headers


def test_from_avo_env_api_key_wins(store_dir: None) -> None:
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="oauth-token",
            subscription=True,
        )
    )
    env = {"AVO_ANTHROPIC_API_KEY": "env-key", "AVO_ALLOW_SUBSCRIPTION": "1"}
    config = AnthropicConfig.from_avo_env(env, fallback_model="claude-sonnet-4-6")
    assert config.auth_mode == "api_key"
    assert config._api_key == "env-key"


def test_from_avo_env_stored_oauth_disabled_raises(store_dir: None) -> None:
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="oauth-token",
            subscription=True,
        )
    )
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        AnthropicConfig.from_avo_env(
            {"AVO_ALLOW_SUBSCRIPTION": "0"}, fallback_model="claude-sonnet-4-6"
        )


def test_from_avo_env_stored_oauth_explicitly_allowed(store_dir: None) -> None:
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="oauth-token",
            subscription=True,
        )
    )
    config = AnthropicConfig.from_avo_env(
        {"AVO_ALLOW_SUBSCRIPTION": "1"}, fallback_model="claude-sonnet-4-6"
    )
    assert config.auth_mode == "oauth"
    assert config._api_key == "oauth-token"


@pytest.mark.asyncio
async def test_anthropic_provider_oauth_request_uses_token_provider() -> None:
    client = FakeClient()
    config = AnthropicConfig.oauth(model="claude-sonnet-4-6")

    async def token_provider() -> str:
        return "dynamic-at"

    provider = AnthropicProvider(config, client=client, token_provider=token_provider)
    response = await provider.generate(_request())

    assert response.content == "ok"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages?beta=true"
    assert call["headers"]["Authorization"] == "Bearer dynamic-at"
    assert "x-api-key" not in call["headers"]
    assert "claude-code-20250219" in call["headers"]["anthropic-beta"]


@pytest.mark.asyncio
async def test_anthropic_provider_oauth_401_reactive_refresh(
    store_dir: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="old-token",
            refresh_token="valid-refresh",
            obtained_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=5),
            subscription=True,
        )
    )
    # Mock flows.request_token so refresh succeeds
    monkeypatch.setattr(
        "avo.oauth.flows.request_token",
        lambda entry, form: {"access_token": "new-token", "expires_in": 3600},
    )

    client = FakeClient(
        [
            _Response(401, "unauthorized", {"error": "token_expired"}),
            _Response(200, "", {"content": [{"type": "text", "text": "recovered"}]}),
        ]
    )
    config = AnthropicConfig.oauth(model="claude-sonnet-4-6")
    provider = AnthropicProvider(config, client=client)

    response = await provider.generate(_request())
    assert response.content == "recovered"
    assert len(client.calls) == 2
    assert client.calls[0]["headers"]["Authorization"] == "Bearer old-token"
    assert client.calls[1]["headers"]["Authorization"] == "Bearer new-token"


@pytest.mark.asyncio
async def test_anthropic_provider_oauth_401_second_failure_raises(
    store_dir: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="old-token",
            refresh_token="valid-refresh",
            obtained_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=5),
            subscription=True,
        )
    )
    monkeypatch.setattr(
        "avo.oauth.flows.request_token",
        lambda entry, form: {"access_token": "new-token", "expires_in": 3600},
    )

    client = FakeClient(
        [
            _Response(401, "unauthorized", {"error": "token_expired"}),
            _Response(401, "unauthorized", {"error": "still_bad"}),
        ]
    )
    config = AnthropicConfig.oauth(model="claude-sonnet-4-6")
    provider = AnthropicProvider(config, client=client)

    with pytest.raises(ProviderError, match="status 401"):
        await provider.generate(_request())
