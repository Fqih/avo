"""Tests for OpenRouter provider adapter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from avo.config import build_provider_from_env, default_model
from avo.exceptions import ProviderError
from avo.models import ModelRequest
from avo.providers.openrouter import OpenRouterConfig, OpenRouterProvider


class _StubClient:
    """Captures the request that would be sent over the wire."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        status_code: int = 200,
        text: str = "",
        chunks: list[bytes] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or {}
        self.status_code = status_code
        self.text = text
        self.chunks = chunks or []
        self.error = error
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_payload: dict[str, Any] | None = None

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,  # noqa: ASYNC109 - mirrors the httpx client signature
    ) -> Any:
        del timeout
        self.last_url = url
        self.last_headers = headers
        self.last_payload = json
        if self.error:
            raise self.error
        return _StubResponse(self.response, self.status_code, self.text)

    def stream(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,
    ) -> Any:
        del timeout
        self.last_url = url
        self.last_headers = headers
        self.last_payload = json
        return _StubStreamContext(self.chunks, self.status_code)

    async def aclose(self) -> None:
        pass


class _StubResponse:
    def __init__(self, body: dict[str, Any], status_code: int = 200, text: str = "") -> None:
        self._body = body
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._body


class _StubStreamContext:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self.chunks = chunks
        self.status_code = status_code

    async def __aenter__(self) -> _StubStreamResponse:
        return _StubStreamResponse(self.chunks, self.status_code)

    async def __aexit__(self, *args: Any) -> None:
        pass


class _StubStreamResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.text = ""

    async def aiter_bytes(self) -> Any:
        for c in self._chunks:
            yield c


def _sample_payload() -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello from openrouter"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def test_openrouter_config_requires_api_key() -> None:
    env: dict[str, str] = {"AVO_OPENROUTER_MODEL": "meta-llama/llama-3.3-70b-instruct:free"}
    with pytest.raises(ValueError, match="is required when AVO_PROVIDER=openrouter"):
        OpenRouterConfig.from_avo_env(env, fallback_model="meta-llama/llama-3.3-70b-instruct:free")


def test_openrouter_config_reads_env_and_overrides() -> None:
    env = {
        "AVO_OPENROUTER_API_KEY": "sk-or-test-key",
        "AVO_OPENROUTER_BASE_URL": "https://custom.openrouter.test/v1/",
        "AVO_OPENROUTER_MODEL": "deepseek/deepseek-r1:free",
        "AVO_OPENROUTER_SITE_URL": "https://mycoolapp.org",
        "AVO_OPENROUTER_APP_NAME": "MyCoolApp",
    }
    config = OpenRouterConfig.from_avo_env(env, fallback_model="fallback")
    assert config.base_url == "https://custom.openrouter.test/v1"
    assert config.model == "deepseek/deepseek-r1:free"
    assert config.site_url == "https://mycoolapp.org"
    assert config.app_name == "MyCoolApp"
    headers = config.headers()
    assert headers["Authorization"] == "Bearer sk-or-test-key"
    assert headers["HTTP-Referer"] == "https://mycoolapp.org"
    assert headers["X-Title"] == "MyCoolApp"


def test_openrouter_provider_generate_success() -> None:
    env = {
        "AVO_OPENROUTER_API_KEY": "sk-or-test",
        "AVO_OPENROUTER_MODEL": "meta-llama/llama-3.3-70b-instruct:free",
    }
    config = OpenRouterConfig.from_avo_env(env, fallback_model="x")
    client = _StubClient(_sample_payload())
    provider = OpenRouterProvider(config, client=client)  # type: ignore[arg-type]

    request = ModelRequest(run_id="r1", step=1, messages=[{"role": "user", "content": "hi"}])
    response = asyncio.run(provider.generate(request))

    assert response.content == "hello from openrouter"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert client.last_url == "https://openrouter.ai/api/v1/chat/completions"
    assert client.last_headers is not None
    assert client.last_headers["Authorization"] == "Bearer sk-or-test"


def test_openrouter_provider_stream_success() -> None:
    env = {
        "AVO_OPENROUTER_API_KEY": "sk-or-test",
        "AVO_OPENROUTER_MODEL": "meta-llama/llama-3.3-70b-instruct:free",
    }
    config = OpenRouterConfig.from_avo_env(env, fallback_model="x")
    sse_data = (
        b'data: {"choices":[{"delta":{"content":"chunk1"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"chunk2"},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    client = _StubClient(chunks=[sse_data])
    provider = OpenRouterProvider(config, client=client)  # type: ignore[arg-type]

    request = ModelRequest(run_id="r2", step=1, messages=[])

    async def run_stream() -> list[str]:
        chunks: list[str] = []
        async for chunk in provider.stream(request):
            if chunk.text:
                chunks.append(chunk.text)
        return chunks

    result = asyncio.run(run_stream())
    assert result == ["chunk1", "chunk2"]


def test_openrouter_provider_handles_error() -> None:
    env = {
        "AVO_OPENROUTER_API_KEY": "sk-or-test",
        "AVO_OPENROUTER_MODEL": "meta-llama/llama-3.3-70b-instruct:free",
    }
    config = OpenRouterConfig.from_avo_env(env, fallback_model="x")
    client = _StubClient(status_code=401, text="Unauthorized key")
    provider = OpenRouterProvider(config, client=client)  # type: ignore[arg-type]

    request = ModelRequest(run_id="r3", step=1, messages=[])
    with pytest.raises(ProviderError, match="openrouter API error 401"):
        asyncio.run(provider.generate(request))


def test_openrouter_in_config_build_provider_from_env() -> None:
    env = {
        "AVO_PROVIDER": "openrouter",
        "AVO_MODEL": "meta-llama/llama-3.3-70b-instruct:free",
        "AVO_OPENROUTER_API_KEY": "sk-or-secret",
    }
    provider = build_provider_from_env(env)
    assert isinstance(provider, OpenRouterProvider)
    assert provider.model == "meta-llama/llama-3.3-70b-instruct:free"
    assert default_model("openrouter") == "meta-llama/llama-3.3-70b-instruct:free"
