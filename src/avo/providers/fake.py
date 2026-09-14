"""Deterministic scripted provider for tests, examples, and replayable demos."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from avo.exceptions import FakeProviderExhaustedError, ProviderError
from avo.models import ModelRequest, ModelResponse, TokenUsage
from avo.providers.streaming import ModelChunk, response_to_chunks

ScriptItem = ModelResponse | Mapping[str, Any] | Exception


class FakeProvider:
    """Consume scripted responses and errors in a deterministic order."""

    def __init__(
        self,
        responses: Sequence[ScriptItem],
        *,
        repeat_last: bool = False,
    ) -> None:
        if repeat_last and not responses:
            raise ValueError("repeat_last requires at least one scripted response")
        self._script = list(responses)
        self._cursor = 0
        self._repeat_last = repeat_last
        self.requests: list[ModelRequest] = []

    @property
    def cursor(self) -> int:
        """Return the next scripted response index."""

        return self._cursor

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Return the next response or raise the next scripted exception."""

        self.requests.append(request.model_copy(deep=True))
        if self._cursor >= len(self._script):
            if not self._repeat_last:
                raise FakeProviderExhaustedError(
                    "FakeProvider script is exhausted; add another response or set "
                    "repeat_last=True."
                )
            item = self._script[-1]
        else:
            item = self._script[self._cursor]
            self._cursor += 1

        if isinstance(item, Exception):
            raise item
        response = ModelResponse.model_validate(item)
        if response.usage is None:
            response = response.model_copy(update={"usage": TokenUsage()})
        return response

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Replay the scripted response as a lossless chunk sequence.

        Consumes the script exactly like :meth:`generate` (same cursor
        and error semantics) and decomposes the result via
        :func:`response_to_chunks`, so ``collect_stream`` over
        ``stream`` is guaranteed to equal ``generate``.
        """

        response = await self.generate(request)
        for chunk in response_to_chunks(response):
            yield chunk

    def reset(self) -> None:
        """Reset replay to the first scripted item and clear captured requests."""

        self._cursor = 0
        self.requests.clear()

    def snapshot_state(self) -> dict[str, JsonValue]:
        """Serialize the script and cursor for deterministic resume."""

        script: list[JsonValue] = []
        for item in self._script:
            if isinstance(item, ModelResponse):
                script.append(
                    {
                        "kind": "response",
                        "value": item.model_dump(mode="json"),
                    }
                )
            elif isinstance(item, Exception):
                script.append(
                    {
                        "kind": "error",
                        "message": str(item),
                        "error_type": type(item).__name__,
                    }
                )
            else:
                script.append({"kind": "mapping", "value": dict(item)})
        return {
            "provider_type": "fake",
            "cursor": self._cursor,
            "repeat_last": self._repeat_last,
            "script": script,
        }

    def restore_state(self, state: dict[str, JsonValue]) -> None:
        """Restore a snapshot produced by snapshot_state."""

        if state.get("provider_type") != "fake":
            raise ValueError("FakeProvider cannot restore a snapshot from another provider type.")
        raw_script = state.get("script")
        if not isinstance(raw_script, list):
            raise ValueError("FakeProvider snapshot is missing a script list.")

        restored: list[ScriptItem] = []
        for raw in raw_script:
            if not isinstance(raw, dict):
                raise ValueError("FakeProvider snapshot contains an invalid script entry.")
            kind = raw.get("kind")
            if kind == "response":
                restored.append(ModelResponse.model_validate(raw.get("value")))
            elif kind == "mapping":
                value = raw.get("value")
                if not isinstance(value, dict):
                    raise ValueError("FakeProvider mapping entry must contain an object value.")
                restored.append(value)
            elif kind == "error":
                restored.append(ProviderError(str(raw.get("message", "scripted provider error"))))
            else:
                raise ValueError(f"Unknown FakeProvider script entry kind: {kind!r}.")

        cursor = state.get("cursor")
        repeat_last = state.get("repeat_last")
        if not isinstance(cursor, int) or cursor < 0:
            raise ValueError("FakeProvider snapshot cursor must be a non-negative integer.")
        if not isinstance(repeat_last, bool):
            raise ValueError("FakeProvider snapshot repeat_last must be a boolean.")
        if cursor > len(restored):
            raise ValueError("FakeProvider snapshot cursor exceeds its script length.")
        if repeat_last and not restored:
            raise ValueError("FakeProvider repeat_last snapshot cannot have an empty script.")

        self._script = restored
        self._cursor = cursor
        self._repeat_last = repeat_last

    @classmethod
    def from_snapshot(cls, state: dict[str, JsonValue]) -> FakeProvider:
        """Construct a fake provider from persisted checkpoint metadata."""

        provider = cls([])
        provider.restore_state(state)
        return provider
