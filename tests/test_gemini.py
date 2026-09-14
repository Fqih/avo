"""HTTP-mock tests for the Google Gemini native provider.

The provider is constructed with an injected async client so no real HTTP
traffic is generated (same seam as ``tests/test_providers_http.py`` and
``tests/test_openrouter.py``). Covers request mapping, response parsing,
SSE streaming, error statuses, and env wiring — fully offline.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from avo import ModelRequest, ToolMetadata
from avo.config import build_provider_from_env
from avo.exceptions import ProviderError
from avo.models import TokenUsage
from avo.providers.gemini import GeminiConfig, GeminiProvider
from avo.providers.streaming import collect_stream


def _request(step: int = 1) -> ModelRequest:
    return ModelRequest(
        run_id="run-1",
        step=step,
        messages=[{"role": "user", "content": "hi"}],
        tools=[ToolMetadata(name="echo", description="echo", input_schema={"type": "object"})],
    )


class _Response:
    def __init__(self, status_code: int, text: str, json_payload: Any) -> None:
        self.status_code = status_code
        self.text = text
        self._json_payload = json_payload

    def json(self) -> Any:
        return self._json_payload


class _StreamResponse:
    def __init__(self, chunks: list[bytes], status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self._chunks = chunks

    async def aiter_bytes(self) -> Any:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        pass


class _StreamContext:
    def __init__(self, response: _StreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _StreamResponse:
        return self._response

    async def __aexit__(self, *args: Any) -> None:
        pass


class FakeClient:
    """Async httpx-shaped client stub for both post and stream paths."""

    def __init__(
        self,
        *,
        json_payload: Any = None,
        status_code: int = 200,
        text: str = "",
        transport_error: Exception | None = None,
        stream_chunks: list[bytes] | None = None,
        stream_status_code: int = 200,
    ) -> None:
        self.json_payload = json_payload
        self.status_code = status_code
        self.text = text
        self.transport_error = transport_error
        self.stream_chunks = stream_chunks or []
        self.stream_status_code = stream_status_code
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,  # noqa: ASYNC109 - mirrors the httpx client signature
    ) -> Any:
        del timeout
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.transport_error is not None:
            raise self.transport_error
        return _Response(self.status_code, self.text, self.json_payload)

    def stream(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,
    ) -> Any:
        del timeout
        self.stream_calls.append({"url": url, "headers": headers, "json": json})
        return _StreamContext(
            _StreamResponse(
                self.stream_chunks,
                self.stream_status_code,
                text=self.text,
            )
        )

    async def aclose(self) -> None:
        self.closed = True


def _config(**overrides: Any) -> GeminiConfig:
    config = GeminiConfig(**overrides)
    config._api_key = "gm-key"
    return config


# ---------------------------------------------------------------------------
# Configuration / env wiring
# ---------------------------------------------------------------------------


def test_gemini_config_requires_api_key() -> None:
    with pytest.raises(ValueError, match="AVO_GEMINI_API_KEY is required"):
        GeminiConfig.from_avo_env({}, fallback_model="gemini-2.5-pro")


def test_gemini_config_env_precedence() -> None:
    config = GeminiConfig.from_avo_env(
        {
            "AVO_GEMINI_API_KEY": "  key-1  ",
            "AVO_GEMINI_MODEL": "gemini-2.5-flash",
            "AVO_GEMINI_BASE_URL": "https://proxy.example/v1beta/",
        },
        fallback_model="gemini-2.5-pro",
    )
    assert config.model == "gemini-2.5-flash"
    assert config.base_url == "https://proxy.example/v1beta"
    assert config._api_key == "key-1"


def test_gemini_config_falls_back_to_default_model() -> None:
    config = GeminiConfig.from_avo_env({"AVO_GEMINI_API_KEY": "k"}, fallback_model="")
    assert config.model == "gemini-2.5-pro"
    assert config.endpoint == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
    )
    assert config.stream_endpoint == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-pro:streamGenerateContent?alt=sse"
    )


def test_gemini_headers_use_goog_api_key() -> None:
    config = _config(model="gemini-2.5-pro")
    assert config.headers() == {
        "x-goog-api-key": "gm-key",
        "Content-Type": "application/json",
    }


def test_build_provider_gemini_requires_key() -> None:
    with pytest.raises(ValueError, match="AVO_GEMINI_API_KEY"):
        build_provider_from_env({"AVO_PROVIDER": "gemini", "AVO_MODEL": "gemini-2.5-pro"})


def test_build_provider_gemini_built() -> None:
    provider = build_provider_from_env(
        {
            "AVO_PROVIDER": "gemini",
            "AVO_MODEL": "gemini-2.5-pro",
            "AVO_GEMINI_API_KEY": "test-key",
        }
    )
    assert isinstance(provider, GeminiProvider)
    assert provider._config.model == "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Request mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_maps_roles_and_system_instruction() -> None:
    client = FakeClient(json_payload={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    request = ModelRequest(
        run_id="run-1",
        step=1,
        messages=[
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"},
        ],
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    await provider.generate(request)

    payload = client.calls[0]["json"]
    assert payload["systemInstruction"] == {"parts": [{"text": "be terse"}]}
    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "hello"}]},
        {"role": "user", "parts": [{"text": "again"}]},
    ]
    assert client.calls[0]["url"].endswith(":generateContent")
    assert client.calls[0]["headers"]["x-goog-api-key"] == "gm-key"


@pytest.mark.asyncio
async def test_gemini_maps_tools_to_function_declarations() -> None:
    client = FakeClient(json_payload={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    await provider.generate(_request())

    payload = client.calls[0]["json"]
    assert payload["tools"] == [
        {
            "functionDeclarations": [
                {"name": "echo", "description": "echo", "parameters": {"type": "object"}}
            ]
        }
    ]
    assert payload["generationConfig"] == {"maxOutputTokens": 1024}


@pytest.mark.asyncio
async def test_gemini_maps_tool_call_and_tool_result_round_trip() -> None:
    client = FakeClient(json_payload={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    request = ModelRequest(
        run_id="run-1",
        step=2,
        messages=[
            {"role": "user", "content": "ping"},
            {
                "role": "assistant",
                "content": "",
                "tool_call": {
                    "tool_call_id": "call-1",
                    "name": "echo",
                    "arguments": {"text": "ping"},
                },
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "echo",
                "content": {"success": True, "output": "pong"},
            },
        ],
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    await provider.generate(request)

    contents = client.calls[0]["json"]["contents"]
    assert contents[1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "echo", "args": {"text": "ping"}}}],
    }
    assert contents[2] == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "name": "echo",
                    "response": {"success": True, "output": "pong"},
                }
            }
        ],
    }


@pytest.mark.asyncio
async def test_gemini_wraps_non_object_tool_result() -> None:
    client = FakeClient(json_payload={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    request = ModelRequest(
        run_id="run-1",
        step=2,
        messages=[
            {"role": "user", "content": "ping"},
            {"role": "tool", "tool_call_id": "call-1", "name": "echo", "content": "pong"},
        ],
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    await provider.generate(request)

    part = client.calls[0]["json"]["contents"][1]["parts"][0]
    assert part["functionResponse"]["response"] == {"result": "pong"}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_parses_text_and_usage() -> None:
    client = FakeClient(
        json_payload={
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": "hel"}, {"text": "lo"}]}}
            ],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4},
        }
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    response = await provider.generate(_request())

    assert response.content == "hello"
    assert response.usage == TokenUsage(input_tokens=11, output_tokens=4)


@pytest.mark.asyncio
async def test_gemini_adopts_provider_response_id() -> None:
    client = FakeClient(
        json_payload={
            "responseId": "resp-7",
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        }
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    response = await provider.generate(_request())

    assert response.response_id == "resp-7"


@pytest.mark.asyncio
async def test_gemini_adopts_response_id_on_function_call() -> None:
    client = FakeClient(
        json_payload={
            "responseId": "resp-8",
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "echo", "args": {"text": "ping"}}}]
                    }
                }
            ],
        }
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.response_id == "resp-8"


@pytest.mark.asyncio
async def test_gemini_keeps_generated_response_id_without_payload_id() -> None:
    client = FakeClient(json_payload={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    response = await provider.generate(_request())

    assert response.response_id


@pytest.mark.asyncio
async def test_gemini_parses_function_call() -> None:
    client = FakeClient(
        json_payload={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"functionCall": {"name": "echo", "args": {"text": "ping"}}}],
                    }
                }
            ]
        }
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.tool_call.name == "echo"
    assert response.tool_call.arguments == {"text": "ping"}
    assert response.tool_call.tool_call_id
    # Usage missing → left as None so the runtime can flag accounting.
    assert response.usage is None


@pytest.mark.asyncio
async def test_gemini_ignores_thought_only_parts_for_final_text() -> None:
    client = FakeClient(
        json_payload={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "reasoning", "thought": True},
                            {"text": "answer"},
                        ]
                    }
                }
            ]
        }
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    response = await provider.generate(_request())

    assert response.content == "answer"


@pytest.mark.asyncio
async def test_gemini_invalid_response_shapes_raise() -> None:
    bad_payloads: list[Any] = [
        {},
        {"candidates": []},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": "not-a-list"},
    ]
    for payload in bad_payloads:
        client = FakeClient(json_payload=payload)
        provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)
        with pytest.raises(ProviderError, match="Invalid Gemini response"):
            await provider.generate(_request())


@pytest.mark.asyncio
async def test_gemini_error_status_maps_to_provider_error() -> None:
    client = FakeClient(status_code=500, text="internal")
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)
    with pytest.raises(ProviderError, match="status 500") as exc:
        await provider.generate(_request())
    assert exc.value.retryable is True

    client = FakeClient(status_code=400, text="bad request")
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)
    with pytest.raises(ProviderError, match="status 400") as exc:
        await provider.generate(_request())
    assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_gemini_429_is_retryable() -> None:
    client = FakeClient(status_code=429, text="rate limited")
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(_request())
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_gemini_transport_failure_raises() -> None:
    import httpx

    client = FakeClient(transport_error=httpx.ConnectError("connect reset"))
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)
    with pytest.raises(ProviderError, match="Gemini transport failure"):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_gemini_unparsable_body_raises() -> None:
    class _BadJsonResponse:
        status_code = 200
        text = ""

        def json(self) -> Any:
            raise ValueError("not json")

    class _BadJsonClient(FakeClient):
        async def post(  # type: ignore[override]
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
            timeout: float | None,  # noqa: ASYNC109 - mirrors the httpx client signature
        ) -> Any:
            del url, headers, json, timeout
            return _BadJsonResponse()

    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=_BadJsonClient())
    with pytest.raises(ProviderError, match="unparsable"):
        await provider.generate(_request())


# ---------------------------------------------------------------------------
# Streaming (streamGenerateContent?alt=sse)
# ---------------------------------------------------------------------------


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


@pytest.mark.asyncio
async def test_gemini_stream_yields_text_then_finish() -> None:
    client = FakeClient(
        stream_chunks=[
            _sse({"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]}),
            _sse({"candidates": [{"content": {"parts": [{"text": "lo"}]}}]}),
            _sse(
                {
                    "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
                }
            ),
        ]
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert client.stream_calls[0]["url"].endswith(":streamGenerateContent?alt=sse")
    assert "".join(chunk.text for chunk in chunks) == "Hello"
    assert chunks[-1].finish_reason == "STOP"
    assert chunks[-1].usage == TokenUsage(input_tokens=5, output_tokens=2)


@pytest.mark.asyncio
async def test_gemini_stream_tool_call_delta_is_normalized() -> None:
    client = FakeClient(
        stream_chunks=[
            _sse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "echo",
                                            "args": {"text": "ping"},
                                        }
                                    }
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ]
                }
            ),
        ]
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    response = await collect_stream(provider, _request())

    assert response.tool_call is not None
    assert response.tool_call.name == "echo"
    assert response.tool_call.arguments == {"text": "ping"}


@pytest.mark.asyncio
async def test_gemini_stream_thought_parts_use_thought_channel() -> None:
    client = FakeClient(
        stream_chunks=[
            _sse({"candidates": [{"content": {"parts": [{"text": "hm", "thought": True}]}}]}),
            _sse({"candidates": [{"content": {"parts": [{"text": "yes"}]}}]}),
        ]
    )
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert chunks[0].thought == "hm"
    assert chunks[0].text == ""
    assert chunks[1].text == "yes"


@pytest.mark.asyncio
async def test_gemini_stream_non_2xx_raises() -> None:
    client = FakeClient(stream_status_code=503, text="unavailable")
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)
    with pytest.raises(ProviderError, match="Gemini stream failed with status 503") as exc:
        async for _ in provider.stream(_request()):
            pass
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_gemini_stream_falls_back_to_generate_without_stream_support() -> None:
    """A post-only client (no .stream) must degrade to generate chunks."""

    class _PostOnlyClient:
        def __init__(self) -> None:
            self._response = _Response(
                200,
                "",
                {
                    "candidates": [{"content": {"parts": [{"text": "done"}]}}],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
                },
            )

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
            timeout: float | None,  # noqa: ASYNC109 - mirrors the httpx client signature
        ) -> Any:
            del url, headers, json, timeout
            return self._response

        async def aclose(self) -> None:
            pass

    client = _PostOnlyClient()
    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=client)

    chunks = [chunk async for chunk in provider.stream(_request())]
    assert chunks[0].text == "done"
    assert chunks[-1].usage == TokenUsage(input_tokens=3, output_tokens=1)


@pytest.mark.asyncio
async def test_gemini_collect_stream_matches_generate() -> None:
    payload: dict[str, Any] = {
        "responseId": "resp-42",
        "candidates": [{"content": {"parts": [{"text": "same"}]}}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
    }
    generated = await GeminiProvider(
        _config(model="gemini-2.5-pro"), client=FakeClient(json_payload=payload)
    ).generate(_request())
    streamed = await collect_stream(
        GeminiProvider(
            _config(model="gemini-2.5-pro"), client=FakeClient(stream_chunks=[_sse(payload)])
        ),
        _request(),
    )
    assert streamed.content == generated.content == "same"
    assert streamed.usage == generated.usage
    # Both paths must adopt the provider id, not mint divergent local UUIDs.
    assert generated.response_id == "resp-42"
    assert streamed.response_id == generated.response_id


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_gemini_streaming_protocol_membership() -> None:
    from avo.providers.streaming import StreamingModelProvider

    provider = GeminiProvider(_config(model="gemini-2.5-pro"), client=FakeClient())
    assert isinstance(provider, StreamingModelProvider)


def test_gemini_aclose_only_when_owned() -> None:
    config = _config(model="gemini-2.5-pro")

    injected = FakeClient()
    asyncio.run(GeminiProvider(config, client=injected).aclose())
    assert injected.closed is False
