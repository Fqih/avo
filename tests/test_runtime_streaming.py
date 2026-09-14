"""Runtime streaming-hook tests (task 3).

The runtime gains an observational ``stream_callback``; when set and the
provider supports streaming, the model call is consumed through
``provider.stream()`` and assembled losslessly. Events, decisions and
``RunResult`` must be identical to the non-stream path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import JsonValue

from avo import ModelRequest, ModelResponse, TokenUsage
from avo.exceptions import ProviderError
from avo.providers.fake import FakeProvider
from avo.providers.streaming import ModelChunk, response_to_chunks
from avo.runtime import AgentRuntime
from avo.storage.memory import InMemoryEventStore


class _FragmentingStreamProvider(FakeProvider):
    """FakeProvider that streams its scripted answer in small text deltas."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        response = await self.generate(request)
        for chunk in response_to_chunks(response):
            if chunk.text:
                text = chunk.text
                for start in range(0, len(text), 4):
                    yield ModelChunk(text=text[start : start + 4])
            else:
                yield chunk


class _NoStreamProvider:
    """Plain provider without a ``stream`` attribute."""

    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        self.calls += 1
        return self._response


class _FlakyStreamProvider:
    """First stream dies mid-tokens; second stream delivers the answer."""

    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self.stream_attempts = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        return self._response

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.stream_attempts += 1
        if self.stream_attempts == 1:
            yield ModelChunk(text="partial ans")
            raise ProviderError("connection reset mid-stream", retryable=True)
        for chunk in response_to_chunks(self._response):
            yield chunk


class _TruncatedToolCallProvider:
    """Stream that ends after a half-written tool-call fragment."""

    async def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        raise AssertionError("generate should not be called")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield ModelChunk(tool_call_delta={"index": 0, "id": "c1", "name": "f"})
        yield ModelChunk(tool_call_delta={"index": 0, "arguments": '{"q":'})


def _scripted() -> list[ModelResponse]:
    return [
        ModelResponse(
            content="hello there you",
            usage=TokenUsage(input_tokens=3, output_tokens=5),
            response_id="sr-1",
        )
    ]


async def test_stream_callback_receives_deltas_and_run_is_identical() -> None:
    deltas: list[str] = []
    store_a = InMemoryEventStore()
    store_b = InMemoryEventStore()
    runtime_a = AgentRuntime(
        provider=_FragmentingStreamProvider(_scripted()),
        event_store=store_a,
        stream_callback=deltas.append,
    )
    runtime_b = AgentRuntime(
        provider=_FragmentingStreamProvider(_scripted()),
        event_store=store_b,
    )

    result_a = await runtime_a.run("hi", run_id="run-eq")
    result_b = await runtime_b.run("hi", run_id="run-eq")

    assert len(deltas) >= 2
    assert "".join(deltas) == "hello there you"
    assert result_a.model_dump() == result_b.model_dump()

    events_a = await store_a.get_events("run-eq")
    events_b = await store_b.get_events("run-eq")
    assert [event.event_type for event in events_a] == [event.event_type for event in events_b]

    def comparable(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        value = dict(payload)
        # Wall-clock durations and random checkpoint ids legitimately differ
        # between runs; everything persisted about the decision must not.
        value.pop("duration_ms", None)
        value.pop("checkpoint_id", None)
        request = value.get("request")
        if isinstance(request, dict):
            request = dict(request)
            request.pop("request_id", None)
            value["request"] = request
        return value

    assert [comparable(event.payload) for event in events_a] == [
        comparable(event.payload) for event in events_b
    ]


async def test_callback_set_on_non_streaming_provider_falls_back_to_generate() -> None:
    deltas: list[str] = []
    provider = _NoStreamProvider(
        ModelResponse(content="plain", usage=TokenUsage(input_tokens=1, output_tokens=1))
    )
    runtime = AgentRuntime(
        provider=provider,  # deliberately protocol-incompatible: no stream attr
        event_store=InMemoryEventStore(),
        stream_callback=deltas.append,
    )

    result = await runtime.run("hi", run_id="run-ns")

    assert result.output == "plain"
    assert result.status.value == "completed"
    assert deltas == []
    assert provider.calls == 1


async def test_mid_stream_failure_notifies_interrupt_and_retry_completes() -> None:
    deltas: list[str] = []
    notices: list[bool] = []
    provider = _FlakyStreamProvider(
        ModelResponse(
            content="the final answer",
            usage=TokenUsage(input_tokens=2, output_tokens=4),
            response_id="sr-2",
        )
    )
    runtime = AgentRuntime(
        provider=provider,
        event_store=InMemoryEventStore(),
        stream_callback=deltas.append,
        stream_interrupt_callback=lambda: notices.append(True),
    )

    result = await runtime.run("hi", run_id="run-flaky")

    assert result.output == "the final answer"
    assert result.status.value == "completed"
    assert len(notices) == 1
    assert "".join(deltas).startswith("partial ans")


async def test_truncated_tool_call_stream_fails_run_without_crashing() -> None:
    deltas: list[str] = []
    runtime = AgentRuntime(
        provider=_TruncatedToolCallProvider(),
        event_store=InMemoryEventStore(),
        stream_callback=deltas.append,
    )

    result = await runtime.run("hi", run_id="run-trunc")

    assert result.status.value == "failed"
    assert result.error is not None
    assert "tool call" in result.error.lower()
