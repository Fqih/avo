"""SaverProvider decorator: compression, addendum, events, delegation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from avo.models import ModelRequest, ModelResponse
from avo.providers.streaming import ModelChunk, response_to_chunks
from avo.savers.pipeline import PipelineConfig
from avo.savers.presets import SaverPreset, resolve_saver
from avo.savers.provider import SaverProvider

PRETTY = '{\n  "a": 1\n}'


def _req(messages: list[dict[str, Any]]) -> ModelRequest:
    return ModelRequest(run_id="run-1", step=1, messages=messages)


def _sample() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "t1", "content": PRETTY},
        {"role": "tool", "tool_call_id": "t2", "content": PRETTY},
    ]


class SpyProvider:
    """Records the exact request objects it receives."""

    name = "spy"
    model = "spy-model"

    def __init__(self) -> None:
        self.seen: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.seen.append(request)
        return ModelResponse(content="ok")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        response = await self.generate(request)
        for chunk in response_to_chunks(response):
            yield chunk


class PlainProvider:
    """Non-streaming inner provider (generate only)."""

    name = "plain"

    async def generate(self, _request: ModelRequest) -> ModelResponse:
        return ModelResponse(content="plain-answer")


@pytest.mark.asyncio
async def test_generate_compresses_tool_results() -> None:
    inner = SpyProvider()
    provider = SaverProvider(inner, resolve_saver("compact") or _fail())
    request = _req(_sample())
    response = await provider.generate(request)
    assert response.content == "ok"

    sent = inner.seen[0].messages
    assert sent[0] == {"role": "system", "content": "persona"}
    assert sent[2]["content"] == '{"a":1}'
    assert sent[3]["content"] == "[same as message #2]"
    assert sent[3]["tool_call_id"] == "t2"
    # the caller's request object is never rewritten
    assert request.messages[2]["content"] == PRETTY


def _fail() -> Any:
    raise AssertionError("preset missing")


@pytest.mark.asyncio
async def test_passthrough_when_preset_has_no_work() -> None:
    inner = SpyProvider()
    bare = SaverPreset(name="bare", description="nothing configured")
    request = _req(_sample())
    events: list[dict[str, Any]] = []
    provider = SaverProvider(inner, bare, event_callback=events.append)
    await provider.generate(request)
    assert inner.seen[0] is request
    assert events == []


@pytest.mark.asyncio
async def test_terse_injects_addendum_at_index_one() -> None:
    inner = SpyProvider()
    provider = SaverProvider(inner, resolve_saver("terse") or _fail())
    await provider.generate(_req(_sample()))
    sent = inner.seen[0].messages
    assert len(sent) == 5
    assert sent[1]["role"] == "system"
    assert str(sent[1]["content"]).startswith("Respond per this style guide:")
    assert "Terse output" in str(sent[1]["content"])
    assert sent[2] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_terse_precedes_user_when_request_has_no_system_message() -> None:
    inner = SpyProvider()
    provider = SaverProvider(inner, resolve_saver("terse") or _fail())
    await provider.generate(_req([{"role": "user", "content": "hi"}]))

    sent = inner.seen[0].messages
    assert sent[0]["role"] == "system"
    assert sent[1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_addendum_is_idempotent() -> None:
    inner = SpyProvider()
    provider = SaverProvider(inner, resolve_saver("terse") or _fail())
    await provider.generate(_req(_sample()))
    first_out = inner.seen[0]
    await provider.generate(first_out)  # caller re-sends the already-injected history
    second_out = inner.seen[1]
    assert len(second_out.messages) == 5
    addenda = [m for m in second_out.messages if "style guide" in str(m.get("content"))]
    assert len(addenda) == 1


@pytest.mark.asyncio
async def test_model_and_name_shape() -> None:
    provider = SaverProvider(SpyProvider(), resolve_saver("compact") or _fail())
    assert provider.name == "saver"
    assert provider.model == "saver(compact:spy-model)"
    naked = SaverProvider(PlainProvider(), resolve_saver("terse") or _fail())
    assert naked.model == "saver(terse:unknown)"


@pytest.mark.asyncio
async def test_event_payload_for_compact() -> None:
    events: list[dict[str, Any]] = []
    provider = SaverProvider(
        SpyProvider(), resolve_saver("compact") or _fail(), event_callback=events.append
    )
    await provider.generate(_req(_sample()))
    assert len(events) == 1
    payload = events[0]
    assert payload["preset"] == "compact"
    assert payload["stages_applied"] == ["json_minify", "dedupe_tool_results"]
    assert payload["tokens_before"] >= payload["tokens_after"] > 0
    assert payload["saved_percent"] >= 0
    assert payload["message_count"] == 4
    assert payload["addendum"] is False


@pytest.mark.asyncio
async def test_event_payload_addendum_only() -> None:
    events: list[dict[str, Any]] = []
    provider = SaverProvider(
        SpyProvider(), resolve_saver("terse") or _fail(), event_callback=events.append
    )
    await provider.generate(_req(_sample()))
    payload = events[0]
    assert payload["addendum"] is True
    assert payload["stages_applied"] == []
    # the addendum itself costs tokens — reported honestly (percent may be negative)
    assert payload["tokens_after"] > payload["tokens_before"]
    assert payload["saved_percent"] < 0
    assert payload["message_count"] == 5


@pytest.mark.asyncio
async def test_awaitable_event_callback_supported() -> None:
    events: list[dict[str, Any]] = []

    async def cb(payload: dict[str, Any]) -> None:
        events.append(payload)

    provider = SaverProvider(SpyProvider(), resolve_saver("compact") or _fail(), event_callback=cb)
    await provider.generate(_req(_sample()))
    assert len(events) == 1


@pytest.mark.asyncio
async def test_event_callback_errors_do_not_break_requests() -> None:
    def cb(_payload: dict[str, Any]) -> None:
        msg = "sink down"
        raise RuntimeError(msg)

    provider = SaverProvider(SpyProvider(), resolve_saver("compact") or _fail(), event_callback=cb)
    response = await provider.generate(_req(_sample()))
    assert response.content == "ok"


@pytest.mark.asyncio
async def test_stream_delegates_compressed_request() -> None:
    inner = SpyProvider()
    provider = SaverProvider(inner, resolve_saver("compact") or _fail())
    chunks = [chunk async for chunk in provider.stream(_req(_sample()))]
    assert chunks
    sent = inner.seen[0].messages
    assert sent[3]["content"] == "[same as message #2]"


@pytest.mark.asyncio
async def test_stream_degrades_for_non_streaming_inner() -> None:
    provider = SaverProvider(PlainProvider(), resolve_saver("compact") or _fail())
    parts: list[str] = []
    async for chunk in provider.stream(_req([{"role": "user", "content": "x"}])):
        parts.append(chunk.text)
    assert "plain-answer" in "".join(parts)


@pytest.mark.asyncio
async def test_preset_pipeline_and_addendum_combined() -> None:
    full = SaverPreset(
        name="mix",
        description="both",
        skill_name="caveman-terse",
        pipeline=PipelineConfig(),
    )
    inner = SpyProvider()
    provider = SaverProvider(inner, full)
    await provider.generate(_req(_sample()))
    sent = inner.seen[0].messages
    assert "style guide" in str(sent[1]["content"])
    assert sent[4]["content"] == "[same as message #3]"
