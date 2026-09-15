"""Direct tests for the shared parser-injected SSE streaming helper.

``avo.providers.http_common.stream_sse_chunks`` carries the open →
status-gate → iterate control flow that every SSE transport repeats;
the per-line interpretation is injected so OpenAI-style and Gemini-style
streams share one implementation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from avo.exceptions import ProviderError
from avo.providers.http_common import stream_sse_chunks
from avo.providers.streaming import ModelChunk


class _Response:
    def __init__(self, status_code: int, chunks: list[bytes], text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self._chunks = chunks

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            for chunk in self._chunks:
                yield chunk

        return _gen()

    async def aclose(self) -> None:
        return None


class _Client:
    def __init__(
        self,
        status_code: int = 200,
        body: list[bytes] | None = None,
        text: str = "",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = _Response(status_code, body or [], text)

    async def post(self, url: str, **kwargs: Any) -> Any:
        raise AssertionError("stream_sse_chunks must not POST")

    async def aclose(self) -> None:
        return None

    def stream(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        response = self._response

        class _Ctx:
            async def __aenter__(self) -> _Response:
                return response

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


async def test_stream_sse_chunks_yields_parsed_chunks_per_line() -> None:
    client = _Client(body=[b"data: A\n\ndata: B\n\n"])
    seen: list[str] = []

    def parse(line: str) -> list[ModelChunk]:
        seen.append(line)
        return [ModelChunk(text=line.lower()), ModelChunk(text=line.lower() + "!")]

    chunks = [
        chunk
        async for chunk in stream_sse_chunks(
            client,
            "https://example.test/stream",
            {"h": "1"},
            {"payload": True},
            5.0,
            transport_name="Test",
            parse_line=parse,
        )
    ]

    assert seen == ["A", "B"]
    assert [chunk.text for chunk in chunks] == ["a", "a!", "b", "b!"]
    assert client.calls[0]["url"] == "https://example.test/stream"


async def test_stream_sse_chunks_non_2xx_raises_retryable_flags() -> None:
    for status, retryable in ((429, True), (500, True), (404, False), (400, False)):
        client = _Client(status_code=status, body=[], text="boom")
        with pytest.raises(ProviderError) as excinfo:
            [
                chunk
                async for chunk in stream_sse_chunks(
                    client,
                    "ep",
                    {},
                    {},
                    5.0,
                    transport_name="Test",
                    parse_line=lambda line: [],
                )
            ]
        assert excinfo.value.retryable is retryable
        assert f"status {status}" in str(excinfo.value)
