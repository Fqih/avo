"""Tests for the streaming provider protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from avo.exceptions import ProviderError
from avo.models import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.providers.base import ModelProvider
from avo.providers.streaming import (
    ModelChunk,
    StreamingModelProvider,
    collect_stream,
    response_to_chunks,
)


class _StreamingProvider(StreamingModelProvider, ModelProvider):
    """Provider that yields three chunks then terminates."""

    def __init__(self, chunks: list[ModelChunk]) -> None:
        self._chunks = chunks
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(content="fallback")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.calls += 1
        for chunk in self._chunks:
            yield chunk


class _PlainProvider(ModelProvider):
    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return self._response


def _request() -> ModelRequest:
    return ModelRequest(
        run_id="run-1",
        step=1,
        messages=[{"role": "user", "content": "hi"}],
    )


async def test_collect_stream_assembles_text() -> None:
    provider = _StreamingProvider(
        [
            ModelChunk(text="hello "),
            ModelChunk(text="world"),
            ModelChunk(finish_reason="stop"),
        ]
    )
    response = await collect_stream(provider, _request())
    assert response.content == "hello world"
    assert provider.calls == 1


async def test_collect_stream_falls_back_to_generate() -> None:
    plain = _PlainProvider(ModelResponse(content="from generate"))
    response = await collect_stream(plain, _request())
    assert response.content == "from generate"
    assert plain.calls == 1


async def test_streaming_provider_satisfies_protocol() -> None:
    provider = _StreamingProvider([])
    assert isinstance(provider, StreamingModelProvider)
    assert isinstance(provider, ModelProvider)


async def test_collect_stream_handles_empty_stream() -> None:
    provider = _StreamingProvider([ModelChunk(finish_reason="stop")])
    response = await collect_stream(provider, _request())
    assert response.content == ""


# ---------------------------------------------------------------------------
# Lossless assembly (usage, response_id, normalized tool-call deltas)
# ---------------------------------------------------------------------------


async def test_collect_stream_carries_usage_and_response_id() -> None:
    provider = _StreamingProvider(
        [
            ModelChunk(text="hello "),
            ModelChunk(response_id="resp-77", usage=TokenUsage(input_tokens=10, output_tokens=3)),
            ModelChunk(text="world"),
            # Field-wise merge: a later chunk only updates output_tokens.
            ModelChunk(usage=TokenUsage(input_tokens=0, output_tokens=25)),
            ModelChunk(finish_reason="stop"),
        ]
    )
    response = await collect_stream(provider, _request())
    assert response.content == "hello world"
    assert response.usage == TokenUsage(input_tokens=10, output_tokens=25)
    assert response.response_id == "resp-77"


async def test_collect_stream_assembles_tool_call_from_normalized_deltas() -> None:
    provider = _StreamingProvider(
        [
            ModelChunk(tool_call_delta={"index": 0, "id": "call_1", "name": "search"}),
            ModelChunk(tool_call_delta={"index": 0, "arguments": '{"q":'}),
            ModelChunk(tool_call_delta={"index": 0, "arguments": '"hi"}'}),
            ModelChunk(
                finish_reason="tool_calls", usage=TokenUsage(input_tokens=5, output_tokens=7)
            ),
        ]
    )
    response = await collect_stream(provider, _request())
    assert response.content is None
    assert response.tool_call is not None
    assert response.tool_call.tool_call_id == "call_1"
    assert response.tool_call.name == "search"
    assert response.tool_call.arguments == {"q": "hi"}
    assert response.usage == TokenUsage(input_tokens=5, output_tokens=7)


async def test_collect_stream_tool_call_wins_over_partial_text() -> None:
    # OpenAI-compatible streams may interleave content deltas and tool-call
    # fragments; parse_openai_response() prefers tool_calls, so streaming
    # assembly must produce the same decision.
    provider = _StreamingProvider(
        [
            ModelChunk(text="Sure, "),
            ModelChunk(tool_call_delta={"index": 0, "id": "c1", "name": "f", "arguments": "{}"}),
        ]
    )
    response = await collect_stream(provider, _request())
    assert response.content is None
    assert response.tool_call is not None
    assert response.tool_call.name == "f"


async def test_collect_stream_uses_first_tool_call_index() -> None:
    provider = _StreamingProvider(
        [
            ModelChunk(
                tool_call_delta={"index": 1, "id": "second", "name": "b", "arguments": "{}"}
            ),
            ModelChunk(tool_call_delta={"index": 0, "id": "first", "name": "a", "arguments": "{}"}),
        ]
    )
    response = await collect_stream(provider, _request())
    assert response.tool_call is not None
    assert response.tool_call.tool_call_id == "first"


async def test_collect_stream_rejects_incomplete_tool_call_arguments() -> None:
    provider = _StreamingProvider(
        [ModelChunk(tool_call_delta={"index": 0, "id": "c", "name": "f", "arguments": '{"q":'})]
    )
    with pytest.raises(ProviderError, match="not valid JSON"):
        await collect_stream(provider, _request())


async def test_collect_stream_rejects_nameless_tool_call() -> None:
    provider = _StreamingProvider(
        [ModelChunk(tool_call_delta={"index": 0, "id": "c", "arguments": "{}"})]
    )
    with pytest.raises(ProviderError, match="missing a name"):
        await collect_stream(provider, _request())


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(content="plain answer", usage=TokenUsage(input_tokens=1, output_tokens=2)),
        ModelResponse(content=""),
        ModelResponse(
            tool_call=ToolCall(tool_call_id="call_9", name="tool", arguments={"k": "v"}),
            usage=TokenUsage(input_tokens=3, output_tokens=4),
        ),
        ModelResponse(content="no usage"),
    ],
)
async def test_response_to_chunks_roundtrips_losslessly(response: ModelResponse) -> None:
    rebuilt = await collect_stream(
        _StreamingProvider(response_to_chunks(response)),
        _request(),
    )
    assert rebuilt.model_dump() == response.model_dump()
