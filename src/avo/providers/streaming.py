"""Streaming protocol for providers.

A streaming provider yields ``ModelChunk`` events instead of (or in
addition to) returning a final :class:`ModelResponse`. The runtime
falls back to ``generate`` when a provider only implements the
non-streaming API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from avo.models import ModelRequest, ModelResponse

from .base import ModelProvider


@dataclass(frozen=True)
class ModelChunk:
    """One chunk of streamed output.

    ``text`` is non-empty for incremental text deltas. ``thought``
    carries incremental reasoning tokens for models supporting thinking.
    ``finish_reason`` is set on the terminal chunk. ``tool_call_delta``
    carries partial tool-call JSON when the provider streams tool arguments.
    """

    text: str = ""
    thought: str = ""
    finish_reason: str | None = None
    tool_call_delta: dict[str, JsonValue] | None = None


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


async def collect_stream(
    provider: ModelProvider,
    request: ModelRequest,
) -> ModelResponse:
    """Run ``provider.stream`` (if available) and assemble a final response."""
    if isinstance(provider, StreamingModelProvider):
        text_parts: list[str] = []
        async for chunk in provider.stream(request):
            if chunk.text:
                text_parts.append(chunk.text)
        return ModelResponse(content="".join(text_parts))
    return await provider.generate(request)


__all__ = [
    "ModelChunk",
    "StreamingModelProvider",
    "ThinkingStreamParser",
    "collect_stream",
    "split_thinking",
]
