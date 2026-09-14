"""Shared helpers for OpenAI-compatible live model providers."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import JsonValue

from avo import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.exceptions import ProviderError
from avo.providers.streaming import ModelChunk


@runtime_checkable
class _StreamResponse(Protocol):
    """Minimal streaming response surface used by :class:`_AsyncHTTPClient.stream`."""

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def aiter_bytes(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class _StreamContext(Protocol):
    """Async context manager yielding a :class:`_StreamResponse`."""

    async def __aenter__(self) -> _StreamResponse: ...

    async def __aexit__(self, *args: Any) -> None: ...


@runtime_checkable
class _AsyncHTTPClient(Protocol):
    """Minimal async client surface used by every HTTP-backed provider.

    ``post`` covers non-streaming calls; ``stream`` opens a streaming
    response as an async context manager. The matching ``httpx`` shape
    is ``httpx.AsyncClient``. Implementations only need to satisfy the
    signatures below; types are :mod:`typing` ``Protocol`` so mypy treats
    the duck-typed client as compatible.
    """

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,  # noqa: ASYNC109 - mirrors the httpx client signature
    ) -> Any: ...

    def stream(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None,
    ) -> _StreamContext: ...

    async def aclose(self) -> None: ...


def json_safe_content(value: object) -> str:
    """Return text unchanged and encode other values as deterministic JSON."""

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def redact_text(value: str) -> str:
    """Remove common API credentials from provider-controlled text."""

    redacted = re.sub(
        r"(?i)(authorization|x-api-key)(\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
    )
    return re.sub(
        r"\b(sk-[A-Za-z0-9_-]{8,}|sk-cp-[A-Za-z0-9_-]{8,})\b",
        "[REDACTED]",
        redacted,
    )


def build_openai_payload(
    model: str,
    request: ModelRequest,
    max_completion_tokens: int,
    *,
    stream: bool = False,
) -> dict[str, object]:
    """Convert a Avo model request to an OpenAI-compatible payload."""

    messages = [_openai_message(message) for message in request.messages]
    tools = [
        {
            "type": "function",
            "function": {
                "name": metadata.name,
                "description": metadata.description,
                "parameters": metadata.input_schema,
            },
        }
        for metadata in request.tools
    ]
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "max_tokens": max_completion_tokens,
        "max_completion_tokens": max_completion_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if request.cache:
        from .prompt_cache import cache_key_for_request

        payload["prompt_cache_key"] = cache_key_for_request(
            run_id=request.run_id,
            step=request.step,
            messages=request.messages,
        )
    return payload


def parse_openai_response(payload: object) -> ModelResponse:
    """Parse one non-streaming OpenAI-compatible response."""

    try:
        response = _require_dict(payload)
        choices = response["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("choices must be a non-empty list")
        choice = _require_dict(choices[0])
        message = _require_dict(choice["message"])
        usage = _parse_usage(response.get("usage"))

        tool_calls = message.get("tool_calls")
        if tool_calls is not None:
            tool_call = _parse_tool_call(tool_calls)
            return ModelResponse(tool_call=tool_call, usage=usage)

        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        return ModelResponse(content=content, usage=usage)
    except ProviderError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        detail = redact_text(str(exc))
        raise ProviderError(
            f"Invalid OpenAI-compatible response: {detail}",
            retryable=False,
        ) from exc


def parse_openai_stream_payload(payload: str) -> ModelChunk | None:
    """Parse one ``data: {...}`` SSE line from an OpenAI-compatible stream.

    Accepts the raw payload either with or without the leading
    ``data:`` prefix. The ``[DONE]`` sentinel yields ``None`` so
    callers can stop cleanly. Empty lines and unknown shapes are
    skipped (returns ``None``).
    """

    line = payload.strip()
    if line.startswith("data:"):
        line = line[len("data:") :].strip()
    if not line or line == "[DONE]":
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    raw_id = event.get("id")
    response_id = raw_id if isinstance(raw_id, str) and raw_id else None
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        usage = event.get("usage")
        if isinstance(usage, dict):
            parsed = _parse_usage(usage)
            if parsed is not None:
                # Some providers emit usage only on the terminal chunk.
                return ModelChunk(usage=parsed, response_id=response_id)
        return None
    choice = _require_dict(choices[0])
    delta = choice.get("delta")
    text: str = ""
    tool_call_delta: dict[str, JsonValue] | None = None
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            text = content
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            tool_call_delta = _normalize_openai_tool_delta(_require_dict(tool_calls[0]))
    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason:
        return ModelChunk(
            text=text,
            finish_reason=finish_reason,
            tool_call_delta=tool_call_delta,
            response_id=response_id,
        )
    if text or tool_call_delta is not None:
        return ModelChunk(text=text, tool_call_delta=tool_call_delta, response_id=response_id)
    return None


def _normalize_openai_tool_delta(raw: dict[str, Any]) -> dict[str, JsonValue]:
    """Map one raw OpenAI ``tool_calls`` fragment to the normalized delta shape.

    The normalized shape is documented on :class:`ModelChunk`: ``index``,
    optional ``id``, ``name`` and an ``arguments`` JSON-string fragment.
    """
    normalized: dict[str, JsonValue] = {}
    index = raw.get("index")
    normalized["index"] = index if isinstance(index, int) else 0
    call_id = raw.get("id")
    if isinstance(call_id, str) and call_id:
        normalized["id"] = call_id
    function = raw.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            normalized["name"] = name
        fragment = function.get("arguments")
        if isinstance(fragment, str) and fragment:
            normalized["arguments"] = fragment
    return normalized


async def iter_sse_lines(byte_iter: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Yield SSE ``data:`` payload strings from an async byte iterator.

    Splits on ``\\n\\n`` event boundaries; trims the optional ``data:``
    prefix. Lines without a ``data:`` prefix are skipped, mirroring the
    heartbeat / comment conventions used by every major SSE endpoint.
    """

    buffer = ""
    async for chunk in byte_iter:
        if not chunk:
            continue
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            event, buffer = buffer.split("\n\n", 1)
            for line in event.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(":"):
                    continue
                if stripped.startswith("data:"):
                    yield stripped[len("data:") :].strip()


async def iter_anthropic_sse_events(
    byte_iter: AsyncIterator[bytes],
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event_type, data_dict)`` pairs from Anthropic SSE streams.

    Anthropic streams use ``event: <name>\\ndata: <json>`` pairs inside
    ``\\n\\n``-separated blocks. ``message_start`` carries the message id,
    ``content_block_delta`` carries text deltas, ``content_block_start``
    announces a tool_use block, ``message_delta`` carries the final
    stop_reason, and ``message_stop`` marks the terminal frame.
    """

    buffer = ""
    current_event: str | None = None
    data_lines: list[str] = []
    async for chunk in byte_iter:
        if not chunk:
            continue
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            event_block, buffer = buffer.split("\n\n", 1)
            current_event = None
            data_lines = []
            for line in event_block.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(":"):
                    continue
                if stripped.startswith("event:"):
                    current_event = stripped[len("event:") :].strip()
                elif stripped.startswith("data:"):
                    data_lines.append(stripped[len("data:") :].strip())
            if current_event and data_lines:
                try:
                    payload = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield current_event, payload


def parse_anthropic_stream_event(
    event_type: str,
    payload: dict[str, Any],
) -> ModelChunk | None:
    """Translate one Anthropic SSE event into a :class:`ModelChunk`.

    Returns ``None`` for event types that carry no usable data (``ping``,
    ``message_start`` without id or usage, ``content_block_start`` with
    non-tool blocks, etc.) so callers can simply ``async for`` and skip.
    ``message_start`` carries the message id and input-token usage;
    ``content_block_start`` announces tool blocks (id/name mapped into
    the normalized tool-call delta shape); ``message_delta`` carries the
    final stop reason and cumulative output-token usage.
    """

    if event_type == "message_start":
        message = payload.get("message")
        if not isinstance(message, dict):
            return None
        raw_id = message.get("id")
        response_id = raw_id if isinstance(raw_id, str) and raw_id else None
        usage = _parse_anthropic_stream_usage(message.get("usage"))
        if response_id is None and usage is None:
            return None
        return ModelChunk(usage=usage, response_id=response_id)
    if event_type == "content_block_start":
        block = payload.get("content_block")
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            return None
        normalized: dict[str, JsonValue] = {"index": _stream_block_index(payload)}
        block_id = block.get("id")
        if isinstance(block_id, str) and block_id:
            normalized["id"] = block_id
        block_name = block.get("name")
        if isinstance(block_name, str) and block_name:
            normalized["name"] = block_name
        return ModelChunk(tool_call_delta=normalized)
    if event_type == "content_block_delta":
        delta = payload.get("delta")
        if not isinstance(delta, dict):
            return None
        if delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                return ModelChunk(text=text)
        if delta.get("type") == "input_json_delta":
            partial = delta.get("partial_json")
            if isinstance(partial, str) and partial:
                return ModelChunk(
                    tool_call_delta={
                        "index": _stream_block_index(payload),
                        "arguments": partial,
                    }
                )
        return None
    if event_type == "message_delta":
        delta = payload.get("delta")
        finish_reason: str | None = None
        if isinstance(delta, dict):
            stop = delta.get("stop_reason")
            if isinstance(stop, str) and stop:
                finish_reason = stop
        usage = _parse_anthropic_stream_usage(payload.get("usage"))
        if finish_reason or usage:
            return ModelChunk(finish_reason=finish_reason, usage=usage)
        return None
    if event_type == "message_stop":
        return ModelChunk(finish_reason="stop")
    return None


def _stream_block_index(payload: dict[str, Any]) -> int:
    raw = payload.get("index")
    return raw if isinstance(raw, int) else 0


def _parse_anthropic_stream_usage(value: object) -> TokenUsage | None:
    """Parse partial or full Anthropic usage; zero-filled absent counters.

    Anthropic splits usage across events (``message_start`` carries
    input tokens, ``message_delta`` carries cumulative output tokens);
    :class:`StreamAssembler` merges the partial records field-wise.
    """
    if not isinstance(value, dict):
        return None
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    if not isinstance(input_tokens, int) and not isinstance(output_tokens, int):
        return None
    return TokenUsage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
        output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
    )


async def stream_openai_chunks(
    client: _AsyncHTTPClient,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout: float,  # noqa: ASYNC109 - mirrors the httpx client signature
    *,
    transport_name: str = "OpenAI",
) -> AsyncIterator[ModelChunk]:
    """Open a streaming POST and yield :class:`ModelChunk` events.

    Used by every OpenAI-compatible provider (openai, groq, cerebras,
    minimax-openai). Surfaces ``ProviderError`` for non-2xx responses so
    callers can let the runtime decide whether to retry.
    """

    stream_ctx = client.stream(
        endpoint,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    async with stream_ctx as response:
        status = int(response.status_code)
        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError(
                f"{transport_name} stream failed with status {status}: {detail}",
                retryable=status == 429 or status >= 500,
            )
        async for line in iter_sse_lines(response.aiter_bytes()):
            chunk = parse_openai_stream_payload(line)
            if chunk is not None:
                yield chunk


async def stream_anthropic_chunks(
    client: _AsyncHTTPClient,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,  # noqa: ASYNC109 - mirrors the httpx client signature
    *,
    transport_name: str = "Anthropic",
) -> AsyncIterator[ModelChunk]:
    """Open an Anthropic-style streaming POST and yield :class:`ModelChunk`."""

    stream_ctx = client.stream(
        endpoint,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    async with stream_ctx as response:
        status = int(response.status_code)
        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError(
                f"{transport_name} stream failed with status {status}: {detail}",
                retryable=status == 429 or status >= 500,
            )
        async for event_type, event_payload in iter_anthropic_sse_events(response.aiter_bytes()):
            chunk = parse_anthropic_stream_event(event_type, event_payload)
            if chunk is not None:
                yield chunk


def _openai_message(message: dict[str, Any]) -> dict[str, object]:
    role = message["role"]
    if role == "assistant" and "tool_call" in message:
        tool_call = _require_dict(message["tool_call"])
        arguments = tool_call["arguments"]
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tool_call["tool_call_id"],
                    "type": "function",
                    "function": {
                        "name": tool_call["name"],
                        "arguments": json.dumps(arguments, sort_keys=True),
                    },
                }
            ],
        }
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message["tool_call_id"],
            "content": json_safe_content(message["content"]),
        }
    content = message["content"]
    if isinstance(content, list):
        # Translate typed content blocks (text/image from
        # avo.content_blocks) into the OpenAI ``content_part`` shape:
        # images become ``image_url`` data URLs.
        translated: list[dict[str, object]] = []
        for raw_block in content:
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, Any], raw_block)
            block_type = block.get("type")
            if block_type == "text":
                translated.append({"type": "text", "text": str(block.get("text", ""))})
            elif block_type == "image":
                raw_source = block.get("source")
                source = raw_source if isinstance(raw_source, dict) else {}
                media_type = str(source.get("media_type", "image/png"))
                data = str(source.get("data", ""))
                translated.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    }
                )
        return {"role": role, "content": translated}
    return {"role": role, "content": content}


def _parse_tool_call(value: object) -> ToolCall:
    if not isinstance(value, list) or not value:
        raise ValueError("tool_calls must be a non-empty list")
    raw_call = _require_dict(value[0])
    function = _require_dict(raw_call["function"])
    raw_arguments = function["arguments"]
    if not isinstance(raw_arguments, str):
        raise ValueError("tool call arguments must be a JSON string")
    arguments = json.loads(raw_arguments)
    if not isinstance(arguments, dict):
        raise ValueError("tool call arguments must decode to an object")
    return ToolCall(
        tool_call_id=raw_call["id"],
        name=function["name"],
        arguments=arguments,
    )


def _parse_usage(value: object) -> TokenUsage | None:
    if value is None:
        return None
    usage = _require_dict(value)
    return TokenUsage(
        input_tokens=usage["prompt_tokens"],
        output_tokens=usage["completion_tokens"],
    )


def _require_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("expected an object")
    return value
