"""HTTP-mock tests for live provider modules.

Each provider is constructed with an injected async client so no real HTTP
traffic is generated. The fake client returns canned JSON responses or
raises synthetic transport / status errors, exercising the same code paths
the real httpx client would.
"""

from __future__ import annotations

from typing import Any

import pytest

from avo import ModelRequest, ToolMetadata
from avo.exceptions import ProviderError
from avo.models import TokenUsage
from avo.providers.anthropic import AnthropicConfig, AnthropicProvider
from avo.providers.minimax import MiniMaxConfig, MiniMaxProvider
from avo.providers.ollama import OllamaConfig, OllamaProvider
from avo.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)


def _request(step: int = 1) -> ModelRequest:
    return ModelRequest(
        run_id="run-1",
        step=step,
        messages=[{"role": "user", "content": "hi"}],
        tools=[ToolMetadata(name="echo", description="echo", input_schema={"type": "object"})],
    )


class FakeClient:
    """Minimal async httpx-shaped client stand-in."""

    def __init__(
        self,
        *,
        json_payload: Any = None,
        status_code: int = 200,
        text: str = "",
        transport_error: Exception | None = None,
    ) -> None:
        self.json_payload = json_payload
        self.status_code = status_code
        self.text = text
        self.transport_error = transport_error
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,  # noqa: ASYNC109 - mirrors the httpx client signature
    ) -> Any:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if self.transport_error is not None:
            raise self.transport_error
        return _Response(self.status_code, self.text, self.json_payload)

    async def aclose(self) -> None:
        self.closed = True


class _Response:
    def __init__(self, status_code: int, text: str, json_payload: Any) -> None:
        self.status_code = status_code
        self.text = text
        self._json_payload = json_payload

    def json(self) -> Any:
        return self._json_payload


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_parses_text_response_with_usage() -> None:
    client = FakeClient(
        json_payload={
            "model": "llama3.1",
            "message": {"role": "assistant", "content": "hello there"},
            "prompt_eval_count": 12,
            "eval_count": 7,
        }
    )
    config = OllamaConfig(model="llama3.1")
    provider = OllamaProvider(config, request_timeout_seconds=1.0, client=client)

    response = await provider.generate(_request())

    assert response.content == "hello there"
    assert response.usage == TokenUsage(input_tokens=12, output_tokens=7)
    assert client.calls and client.calls[0]["url"].endswith("/api/chat")


@pytest.mark.asyncio
async def test_ollama_parses_tool_call_response() -> None:
    client = FakeClient(
        json_payload={
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "echo", "arguments": {"text": "ping"}},
                    }
                ],
            }
        }
    )
    provider = OllamaProvider(OllamaConfig(model="llama3.1"), client=client)

    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.tool_call.name == "echo"
    assert response.tool_call.arguments == {"text": "ping"}
    # Usage missing → left as None so the runtime can flag accounting.
    assert response.usage is None


@pytest.mark.asyncio
async def test_ollama_raises_provider_error_on_http_500() -> None:
    client = FakeClient(status_code=500, text="boom")
    provider = OllamaProvider(OllamaConfig(model="llama3.1"), client=client)

    with pytest.raises(ProviderError, match="status 500") as exc:
        await provider.generate(_request())
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_ollama_raises_provider_error_on_transport_failure() -> None:
    import httpx

    client = FakeClient(transport_error=httpx.ConnectError("connect reset"))
    provider = OllamaProvider(OllamaConfig(model="llama3.1"), client=client)

    with pytest.raises(ProviderError, match="Ollama transport failure"):
        await provider.generate(_request())


# ---------------------------------------------------------------------------
# MiniMax (anthropic style)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minimax_anthropic_parses_text_response() -> None:
    client = FakeClient(
        json_payload={
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 3, "output_tokens": 4},
        }
    )
    config = MiniMaxConfig.model_construct(
        model="MiniMax-M3",
        base_url="https://api.minimax.io",
        api_style="anthropic",
    )
    config._avo_api_key = "key-1"
    provider = MiniMaxProvider(config, request_timeout_seconds=1.0, client=client)

    response = await provider.generate(_request())

    assert response.content == "ok"
    assert response.usage == TokenUsage(input_tokens=3, output_tokens=4)
    sent_headers = client.calls[0]["headers"]
    assert sent_headers["x-api-key"] == "key-1"
    assert sent_headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_minimax_anthropic_parses_tool_use() -> None:
    client = FakeClient(
        json_payload={
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-7",
                    "name": "echo",
                    "input": {"text": "ping"},
                }
            ]
        }
    )
    config = MiniMaxConfig.model_construct(
        model="MiniMax-M3",
        base_url="https://api.minimax.io",
        api_style="anthropic",
    )
    config._avo_api_key = "key-1"
    provider = MiniMaxProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.tool_call.tool_call_id == "call-7"
    assert response.tool_call.arguments == {"text": "ping"}


@pytest.mark.asyncio
async def test_minimax_openai_style_uses_bearer() -> None:
    client = FakeClient(
        json_payload={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )
    config = MiniMaxConfig.model_construct(
        model="MiniMax-M3",
        base_url="https://api.minimax.io",
        api_style="openai",
    )
    config._avo_api_key = "key-1"
    provider = MiniMaxProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.content == "ok"
    sent_headers = client.calls[0]["headers"]
    assert sent_headers["Authorization"] == "Bearer key-1"


# ---------------------------------------------------------------------------
# MiniMaxConfig.endpoint (regression: no double-suffix when full URL given)
# ---------------------------------------------------------------------------


def _minimax_config(base_url: str, api_style: str = "anthropic") -> MiniMaxConfig:
    """Construct a MiniMaxConfig without re-running validators."""

    config = MiniMaxConfig.model_construct(
        model="MiniMax-M3",
        base_url=base_url,
        api_style=api_style,  # type: ignore[arg-type]
    )
    config._avo_api_key = "k"
    return config


def test_minimax_endpoint_anthropic_bare_host_appends_suffix() -> None:
    config = _minimax_config("https://api.minimax.io", "anthropic")
    assert config.endpoint == "https://api.minimax.io/anthropic/v1/messages"


def test_minimax_endpoint_anthropic_full_path_is_returned_verbatim() -> None:
    config = _minimax_config("https://api.minimax.io/anthropic", "anthropic")
    assert config.endpoint == "https://api.minimax.io/anthropic/v1/messages"


def test_minimax_endpoint_anthropic_full_messages_path_no_double_suffix() -> None:
    config = _minimax_config("https://api.minimax.io/anthropic/v1/messages", "anthropic")
    assert config.endpoint == "https://api.minimax.io/anthropic/v1/messages"


def test_minimax_endpoint_openai_bare_host_appends_suffix() -> None:
    config = _minimax_config("https://api.minimax.io", "openai")
    assert config.endpoint == "https://api.minimax.io/v1/chat/completions"


def test_minimax_endpoint_openai_full_chat_path_no_double_suffix() -> None:
    config = _minimax_config("https://api.minimax.io/v1/chat/completions", "openai")
    assert config.endpoint == "https://api.minimax.io/v1/chat/completions"


def test_minimax_endpoint_strips_trailing_slash() -> None:
    config = _minimax_config("https://api.minimax.io/", "anthropic")
    assert config.endpoint == "https://api.minimax.io/anthropic/v1/messages"


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_parses_text_and_usage() -> None:
    client = FakeClient(
        json_payload={
            "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 9, "output_tokens": 5},
        }
    )
    config = AnthropicConfig(model="claude-sonnet-4-6")
    config._api_key = "ant-key"
    provider = AnthropicProvider(config, request_timeout_seconds=1.0, client=client)

    response = await provider.generate(_request())

    assert response.content == "done"
    assert response.usage == TokenUsage(input_tokens=9, output_tokens=5)
    sent_headers = client.calls[0]["headers"]
    assert sent_headers["x-api-key"] == "ant-key"


@pytest.mark.asyncio
async def test_anthropic_adopts_provider_response_id() -> None:
    client = FakeClient(
        json_payload={
            "id": "msg_9f2c",
            "content": [{"type": "text", "text": "done"}],
        }
    )
    config = AnthropicConfig(model="claude-sonnet-4-6")
    config._api_key = "ant-key"
    provider = AnthropicProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.response_id == "msg_9f2c"


@pytest.mark.asyncio
async def test_anthropic_adopts_response_id_on_tool_use_block() -> None:
    client = FakeClient(
        json_payload={
            "id": "msg_tool",
            "content": [
                {
                    "type": "tool_use",
                    "id": "ant-call",
                    "name": "echo",
                    "input": {"text": "ping"},
                }
            ],
        }
    )
    config = AnthropicConfig(model="claude-sonnet-4-6")
    config._api_key = "ant-key"
    provider = AnthropicProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.response_id == "msg_tool"


@pytest.mark.asyncio
async def test_anthropic_missing_usage_leaves_none() -> None:
    client = FakeClient(json_payload={"content": [{"type": "text", "text": "no usage"}]})
    config = AnthropicConfig(model="claude-sonnet-4-6")
    config._api_key = "ant-key"
    provider = AnthropicProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.content == "no usage"
    assert response.usage is None


@pytest.mark.asyncio
async def test_anthropic_handles_tool_use_block() -> None:
    client = FakeClient(
        json_payload={
            "content": [
                {
                    "type": "tool_use",
                    "id": "ant-call",
                    "name": "echo",
                    "input": {"text": "ping"},
                }
            ]
        }
    )
    config = AnthropicConfig(model="claude-sonnet-4-6")
    config._api_key = "ant-key"
    provider = AnthropicProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.tool_call.name == "echo"
    assert response.tool_call.arguments == {"text": "ping"}


@pytest.mark.asyncio
async def test_anthropic_invalid_response_raises() -> None:
    client = FakeClient(json_payload={"content": "not-a-list"})
    config = AnthropicConfig(model="claude-sonnet-4-6")
    config._api_key = "ant-key"
    provider = AnthropicProvider(config, client=client)

    with pytest.raises(ProviderError, match="Invalid Anthropic response"):
        await provider.generate(_request())


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_compatible_parses_text_response() -> None:
    client = FakeClient(
        json_payload={
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }
    )
    config = OpenAICompatibleConfig(model="gpt-4o-mini")
    config._api_key = "sk-test"
    provider = OpenAICompatibleProvider(config, request_timeout_seconds=1.0, client=client)

    response = await provider.generate(_request())

    assert response.content == "hello"
    assert response.usage == TokenUsage(input_tokens=2, output_tokens=3)
    sent_headers = client.calls[0]["headers"]
    assert sent_headers["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_openai_compatible_adopts_provider_response_id() -> None:
    client = FakeClient(
        json_payload={
            "id": "chatcmpl-42",
            "choices": [{"message": {"content": "hello"}}],
        }
    )
    config = OpenAICompatibleConfig(model="gpt-4o-mini")
    config._api_key = "sk-test"
    provider = OpenAICompatibleProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.response_id == "chatcmpl-42"


@pytest.mark.asyncio
async def test_openai_compatible_adopts_response_id_on_tool_call() -> None:
    client = FakeClient(
        json_payload={
            "id": "chatcmpl-tool",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "echo", "arguments": '{"text":"ping"}'},
                            }
                        ]
                    }
                }
            ],
        }
    )
    config = OpenAICompatibleConfig(model="gpt-4o-mini")
    config._api_key = "sk-test"
    provider = OpenAICompatibleProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.tool_call.name == "echo"
    assert response.response_id == "chatcmpl-tool"


@pytest.mark.asyncio
async def test_openai_compatible_keeps_generated_response_id_without_payload_id() -> None:
    client = FakeClient(json_payload={"choices": [{"message": {"content": "ok"}}]})
    config = OpenAICompatibleConfig(model="gpt-4o-mini")
    config._api_key = "sk-test"
    provider = OpenAICompatibleProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.response_id


@pytest.mark.asyncio
async def test_openai_compatible_missing_usage() -> None:
    client = FakeClient(json_payload={"choices": [{"message": {"content": "ok"}}]})
    config = OpenAICompatibleConfig(model="gpt-4o-mini")
    config._api_key = "sk-test"
    provider = OpenAICompatibleProvider(config, client=client)

    response = await provider.generate(_request())

    assert response.content == "ok"
    assert response.usage is None


@pytest.mark.asyncio
async def test_openai_compatible_429_is_retryable() -> None:
    client = FakeClient(status_code=429, text="rate limited")
    config = OpenAICompatibleConfig(model="gpt-4o-mini")
    config._api_key = "sk-test"
    provider = OpenAICompatibleProvider(config, client=client)

    with pytest.raises(ProviderError) as exc:
        await provider.generate(_request())
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_aclose_only_when_owned() -> None:
    config = OllamaConfig(model="llama3.1")

    # Injected client: provider must NOT close it; caller owns it.
    injected = FakeClient()
    injected_provider = OllamaProvider(config, client=injected)
    await injected_provider.aclose()
    assert injected.closed is False

    # Owned client: provider creates and closes it.
    owned_provider = OllamaProvider(config, client=FakeClient())
    await owned_provider.aclose()
    assert owned_provider._client is not None
