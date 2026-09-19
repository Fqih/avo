"""Tests for the ``web_fetch`` tool.

The real httpx client is never used in the suite — every test injects
a small fake client so the runtime stays offline.
"""

from __future__ import annotations

from typing import Any

import pytest

from avo.app_tools.web_fetch import (
    WebFetchArguments,
    WebFetchError,
    _fetch_with_client,
    web_fetch_tool,
)


def _public_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


class _FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status_code: int = 200,
        content: bytes = b"",
        content_type: str = "text/plain",
        encoding: str | None = "utf-8",
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}
        self.encoding = encoding


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, *, timeout: float, follow_redirects: bool) -> _FakeResponse:  # noqa: ASYNC109
        self.calls.append({"url": url, "timeout": timeout, "follow_redirects": follow_redirects})
        return self._response


async def test_fetch_returns_decoded_body() -> None:
    response = _FakeResponse(
        url="https://example.com/",
        content=b"hello world",
        content_type="text/plain",
    )
    client = _FakeClient(response)
    result = await _fetch_with_client(
        client,
        WebFetchArguments(url="https://example.com/"),
        resolver=_public_resolver,
    )
    assert result["body"] == "hello world"
    assert result["status_code"] == 200
    assert result["url"] == "https://example.com/"
    assert result["content_type"] == "text/plain"
    assert result["truncated"] is False


async def test_fetch_truncates_oversized_body() -> None:
    big = b"x" * 1024
    response = _FakeResponse(url="https://example.com/big", content=big)
    client = _FakeClient(response)
    result = await _fetch_with_client(
        client,
        WebFetchArguments(url="https://example.com/big", max_bytes=128),
        resolver=_public_resolver,
    )
    assert len(result["body"]) == 128  # type: ignore[arg-type]
    assert result["truncated"] is True
    assert result["bytes_read"] == 128


async def test_fetch_rejects_file_scheme() -> None:
    client = _FakeClient(_FakeResponse(url="file:///etc/passwd"))
    with pytest.raises(WebFetchError, match="only supports"):
        await _fetch_with_client(
            client,
            WebFetchArguments(url="file:///etc/passwd"),
        )


async def test_fetch_rejects_data_scheme() -> None:
    client = _FakeClient(_FakeResponse(url="data:text/plain,hello"))
    with pytest.raises(WebFetchError, match="only supports"):
        await _fetch_with_client(
            client,
            WebFetchArguments(url="data:text/plain,hello"),
        )


async def test_fetch_rejects_missing_host() -> None:
    client = _FakeClient(_FakeResponse(url="https://"))
    with pytest.raises(WebFetchError, match="missing a host"):
        await _fetch_with_client(
            client,
            WebFetchArguments(url="https://"),
        )


async def test_fetch_passes_timeout_to_client() -> None:
    client = _FakeClient(_FakeResponse(url="https://x/"))
    await _fetch_with_client(
        client,
        WebFetchArguments(url="https://x/", timeout_seconds=3.5),
        resolver=_public_resolver,
    )
    assert client.calls[0]["timeout"] == 3.5


async def test_fetch_handles_missing_encoding_header() -> None:
    response = _FakeResponse(url="https://x/", content=b"hi", encoding=None)
    client = _FakeClient(response)
    result = await _fetch_with_client(
        client,
        WebFetchArguments(url="https://x/"),
        resolver=_public_resolver,
    )
    assert result["body"] == "hi"


async def test_fetch_handles_invalid_encoding_header() -> None:
    response = _FakeResponse(url="https://x/", content=b"hi", encoding="garbage")
    client = _FakeClient(response)
    result = await _fetch_with_client(
        client,
        WebFetchArguments(url="https://x/"),
        resolver=_public_resolver,
    )
    assert result["body"] == "hi"


async def test_fetch_tool_metadata_describes_contract() -> None:
    tool = web_fetch_tool()
    metadata = tool.metadata
    assert metadata.name == "web_fetch"
    assert metadata.input_schema["properties"]["url"]
    assert metadata.input_schema["properties"]["max_bytes"]


def test_fetch_arguments_reject_zero_max_bytes() -> None:
    with pytest.raises(ValueError):
        WebFetchArguments(url="https://x/", max_bytes=0)


def test_fetch_arguments_reject_missing_url() -> None:
    with pytest.raises(ValueError):
        WebFetchArguments(url="")
