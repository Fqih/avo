"""Streaming protocol for providers.

A streaming provider yields ``ModelChunk`` events instead of (or in
addition to) returning a final :class:`ModelResponse`. The runtime
falls back to ``generate`` when a provider only implements the
non-streaming API.

The protocol is lossless: :class:`StreamAssembler` (driven by
:func:`collect_stream`) reconstructs the same :class:`ModelResponse`
that ``generate`` would return for the same provider output, including
token usage, the provider response id, and streamed tool calls.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from avo.exceptions import ProviderError
from avo.models import ModelRequest, ModelResponse, TokenUsage, ToolCall

from .base import ModelProvider


@dataclass(frozen=True)
class ModelChunk:
    """One chunk of streamed output.

    ``text`` is non-empty for incremental text deltas. ``thought``
    carries incremental reasoning tokens for models supporting thinking.
    ``finish_reason`` is set on the terminal chunk.

    ``tool_call_delta`` carries partial tool-call data in a NORMALIZED
    shape so a single provider-agnostic assembler can rebuild complete
    :class:`ToolCall` objects. Recognized keys:

    - ``index`` (int, default ``0``): parallel-call slot discriminator.
    - ``id`` (str, optional): tool-call id (first non-empty wins).
    - ``name`` (str, optional): tool name (first non-empty wins).
    - ``arguments`` (str, optional): incremental fragment of the JSON
      arguments string; fragments are concatenated in stream order.

    Provider parsers map their native fragment formats (OpenAI's nested
    ``function`` envelope, Anthropic's ``input_json_delta`` blocks) into
    this shape; :class:`StreamAssembler` merges them.

    ``usage`` carries provider token accounting when the transport
    exposes it (OpenAI's usage-only terminal chunk, Anthropic's
    ``message_start`` / ``message_delta`` usage). When multiple chunks
    carry usage, counters merge field-wise and the last non-zero value
    for each counter wins. ``response_id`` carries the provider's
    message id when the stream exposes one.
    """

    text: str = ""
    thought: str = ""
    finish_reason: str | None = None
    tool_call_delta: dict[str, JsonValue] | None = None
    usage: TokenUsage | None = None
    response_id: str | None = None


def split_thinking(content: str) -> tuple[str, str]:
    """Split <think>...</think> reasoning blocks from the main content.

    Returns ``(thought, answer)``. If no thinking tags exist, returns
    ``("", content)``.
    """
    if not content:
        return "", ""

    if "<think>" in content and "</think>" in content:
        prefix, rest = content.split("<think>", 1)
        thought, suffix = rest.split("</think>", 1)
        answer = (prefix + suffix).strip()
        return thought.strip(), answer

    if content.startswith("<think>"):
        thought = content[len("<think>") :].strip()
        return thought, ""

    return "", content


class ThinkingStreamParser:
    """Stateful stream filter that separates reasoning (<think>) from answer tokens.

    Supports chunk boundaries splitting opening or closing tags across chunks.
    """

    def __init__(self) -> None:
        self.in_think: bool = False
        self._buffer: str = ""

    def feed(self, chunk_text: str) -> list[tuple[str, str]]:
        """Feed a text chunk and return a list of (channel, text) tuples.

        channel is either "thought" or "content".
        """
        if not chunk_text:
            return []

        text = self._buffer + chunk_text
        self._buffer = ""
        results: list[tuple[str, str]] = []

        while text:
            if not self.in_think:
                if "<think>" in text:
                    idx = text.index("<think>")
                    content_part = text[:idx]
                    if content_part:
                        results.append(("content", content_part))
                    self.in_think = True
                    text = text[idx + len("<think>") :]
                else:
                    partial_match = False
                    for i in range(1, len("<think>")):
                        if text.endswith("<think>"[:i]):
                            self._buffer = text[-i:]
                            content_part = text[:-i]
                            if content_part:
                                results.append(("content", content_part))
                            partial_match = True
                            break
                    if partial_match:
                        break
                    results.append(("content", text))
                    text = ""
            else:
                if "</think>" in text:
                    idx = text.index("</think>")
                    thought_part = text[:idx]
                    if thought_part:
                        results.append(("thought", thought_part))
                    self.in_think = False
                    text = text[idx + len("</think>") :]
                else:
                    partial_match = False
                    for i in range(1, len("</think>")):
                        if text.endswith("</think>"[:i]):
                            self._buffer = text[-i:]
                            thought_part = text[:-i]
                            if thought_part:
                                results.append(("thought", thought_part))
                            partial_match = True
                            break
                    if partial_match:
                        break
                    results.append(("thought", text))
                    text = ""

        return results

    def flush(self) -> list[tuple[str, str]]:
        """Flush any remaining buffered characters at stream termination."""
        if not self._buffer:
            return []
        channel = "thought" if self.in_think else "content"
        buffered = self._buffer
        self._buffer = ""
        return [(channel, buffered)]


@runtime_checkable
class StreamingModelProvider(ModelProvider, Protocol):
    """A provider that can stream output via :meth:`stream`."""

    def stream(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelChunk]:  # pragma: no cover - protocol
        ...


def _merge_usage(existing: TokenUsage | None, incoming: TokenUsage) -> TokenUsage:
    """Field-wise merge; a zero incoming counter preserves the known value."""
    if existing is None:
        return incoming
    return TokenUsage(
        input_tokens=incoming.input_tokens or existing.input_tokens,
        output_tokens=incoming.output_tokens or existing.output_tokens,
    )


class StreamAssembler:
    """Merge :class:`ModelChunk` events into a final :class:`ModelResponse`.

    The assembled decision mirrors each provider's ``generate`` mapping:
    an assembled tool call wins over interleaved text (OpenAI checks
    ``message.tool_calls`` first; Anthropic returns the first ``tool_use``
    block). Only the lowest call index is used because the runtime model
    carries a single tool call per response.
    """

    def __init__(self) -> None:
        self._text: list[str] = []
        self._usage: TokenUsage | None = None
        self._response_id: str | None = None
        self._calls: dict[int, dict[str, str]] = {}

    def feed(self, chunk: ModelChunk) -> None:
        """Absorb one stream chunk into the assembly."""
        if chunk.text:
            self._text.append(chunk.text)
        if chunk.usage is not None:
            self._usage = _merge_usage(self._usage, chunk.usage)
        if chunk.response_id and self._response_id is None:
            self._response_id = chunk.response_id
        delta = chunk.tool_call_delta
        if delta is None:
            return
        raw_index = delta.get("index", 0)
        index = raw_index if isinstance(raw_index, int) else 0
        slot = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        for key in ("id", "name"):
            value = delta.get(key)
            if isinstance(value, str) and value and not slot[key]:
                slot[key] = value
        fragment = delta.get("arguments")
        if isinstance(fragment, str):
            slot["arguments"] += fragment

    def build(self) -> ModelResponse:
        """Return the final response; raise on an un-assemblable tool call."""
        if self._calls:
            call = self._assemble_tool_call()
            if self._response_id is not None:
                return ModelResponse(
                    tool_call=call,
                    usage=self._usage,
                    response_id=self._response_id,
                )
            return ModelResponse(tool_call=call, usage=self._usage)
        content = "".join(self._text)
        if self._response_id is not None:
            return ModelResponse(
                content=content,
                usage=self._usage,
                response_id=self._response_id,
            )
        return ModelResponse(content=content, usage=self._usage)

    def _assemble_tool_call(self) -> ToolCall:
        slot = self._calls[min(self._calls)]
        name = slot["name"]
        if not name:
            raise ProviderError(
                "Streamed tool call is missing a name.",
                retryable=False,
            )
        raw_arguments = slot["arguments"].strip() or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"Streamed tool call arguments are not valid JSON: {exc}",
                retryable=False,
            ) from exc
        if not isinstance(arguments, dict):
            raise ProviderError(
                "Streamed tool call arguments must decode to an object.",
                retryable=False,
            )
        if slot["id"]:
            return ToolCall(tool_call_id=slot["id"], name=name, arguments=arguments)
        return ToolCall(name=name, arguments=arguments)


def response_to_chunks(response: ModelResponse) -> list[ModelChunk]:
    """Decompose a complete response into a lossless chunk sequence.

    Used by routers collapsing a non-streaming route's ``generate``
    result into stream events, and by :class:`FakeProvider` for its
    streaming exemplar; :func:`collect_stream` rebuilds the exact same
    response from these chunks.
    """
    chunks: list[ModelChunk] = []
    if response.content:
        chunks.append(ModelChunk(text=response.content))
    finish_reason = "stop"
    if response.tool_call is not None:
        call = response.tool_call
        chunks.append(
            ModelChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call.tool_call_id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, sort_keys=True),
                }
            )
        )
        finish_reason = "tool_use"
    chunks.append(
        ModelChunk(
            usage=response.usage,
            response_id=response.response_id,
            finish_reason=finish_reason,
        )
    )
    return chunks


async def collect_stream(
    provider: ModelProvider,
    request: ModelRequest,
) -> ModelResponse:
    """Run ``provider.stream`` (if available) and assemble a final response.

    Lossless: the returned response carries content, usage, the provider
    response id, and tool calls exactly as ``generate`` would for the
    same provider output.
    """
    if isinstance(provider, StreamingModelProvider):
        assembler = StreamAssembler()
        async for chunk in provider.stream(request):
            assembler.feed(chunk)
        return assembler.build()
    return await provider.generate(request)


__all__ = [
    "ModelChunk",
    "StreamAssembler",
    "StreamingModelProvider",
    "ThinkingStreamParser",
    "collect_stream",
    "response_to_chunks",
    "split_thinking",
]
