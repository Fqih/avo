"""Offline Ollama provider tests, including legacy JSON tool-call recovery."""

from __future__ import annotations

import json
from typing import Any

import pytest

from avo import ModelRequest, ToolMetadata
from avo.providers.ollama import OllamaConfig, OllamaProvider


class _Response:
    status_code = 200
    text = ""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def json(self) -> dict[str, Any]:
        return self.payload


class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def post(
        self,
        _url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,  # noqa: ASYNC109 - mirrors the httpx client signature
    ) -> _Response:
        del headers, json, timeout
        return _Response(self.payload)

    async def aclose(self) -> None:
        pass


class _BlankResponse:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        raise json.JSONDecodeError("empty response", "", 0)


class _SequenceClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def post(
        self,
        _url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,  # noqa: ASYNC109 - mirrors the httpx client signature
    ) -> Any:
        del headers, json, timeout
        self.calls += 1
        return self.responses.pop(0)

    async def aclose(self) -> None:
        pass


def _request() -> ModelRequest:
    return ModelRequest(
        run_id="run-1",
        step=1,
        messages=[{"role": "user", "content": "inspect the workspace"}],
        tools=[
            ToolMetadata(
                name="workspace_map",
                description="List workspace files.",
                input_schema={"type": "object"},
            )
        ],
    )


@pytest.mark.asyncio
async def test_recovers_registered_json_tool_call_from_ollama_content() -> None:
    provider = OllamaProvider(
        OllamaConfig(model="qwen2.5-coder:7b"),
        client=_Client(
            {
                "message": {
                    "role": "assistant",
                    "content": '{"name":"workspace_map","arguments":{"max_entries":5}}',
                }
            }
        ),
    )
    response = await provider.generate(_request())

    assert response.tool_call is not None
    assert response.tool_call.name == "workspace_map"
    assert response.tool_call.arguments == {"max_entries": 5}
    assert response.content is None


@pytest.mark.asyncio
async def test_keeps_unregistered_json_content_as_a_normal_answer() -> None:
    provider = OllamaProvider(
        OllamaConfig(model="qwen2.5-coder:7b"),
        client=_Client(
            {
                "message": {
                    "role": "assistant",
                    "content": '{"name":"delete_everything","arguments":{}}',
                }
            }
        ),
    )
    response = await provider.generate(_request())

    assert response.tool_call is None
    assert response.content == '{"name":"delete_everything","arguments":{}}'


@pytest.mark.asyncio
async def test_retries_once_when_ollama_returns_an_empty_success_body() -> None:
    client = _SequenceClient(
        [
            _BlankResponse(),
            _Response({"message": {"role": "assistant", "content": "recovered"}}),
        ]
    )
    provider = OllamaProvider(OllamaConfig(model="qwen2.5-coder:7b"), client=client)

    response = await provider.generate(_request())

    assert response.content == "recovered"
    assert client.calls == 2
