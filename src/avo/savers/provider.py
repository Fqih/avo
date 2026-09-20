"""SaverProvider: request-time token savers as a provider decorator (spec §4).

Wraps any :class:`~avo.providers.base.ModelProvider` (streaming or
not) and, per request, injects the preset's prompt addendum (before
the pipeline, so back-reference indices match what the model sees),
then runs the deterministic compression pipeline. The caller's
request object is never rewritten; originals stay in the event log —
compression is request-time only. Opt-in by construction: without a
preset doing anything, the inner provider sees the exact same
request object it would have seen unwrapped.

Emits ``EventType.SAVER_APPLIED`` via ``event_callback``, mirroring
the combo ``ROUTE_FAILOVER`` precedent.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from avo.models import ModelRequest, ModelResponse
from avo.providers.base import ModelProvider
from avo.providers.streaming import (
    ModelChunk,
    StreamingModelProvider,
    response_to_chunks,
)
from avo.savers.pipeline import run_pipeline
from avo.savers.presets import SaverPreset, builtin_skill_body
from avo.savers.stages import Messages, estimate_messages

_LOG = logging.getLogger("avo.savers.provider")

SaverEventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
SaverNotifier = Callable[[str], None]

_ADDENDUM_PREFIX = "Respond per this style guide:\n"


class SaverProvider(StreamingModelProvider):
    """Decorator applying the configured saver preset before delegation."""

    name = "saver"

    def __init__(
        self,
        inner: ModelProvider,
        preset: SaverPreset,
        *,
        event_callback: SaverEventCallback | None = None,
        notifier: SaverNotifier | None = None,
    ) -> None:
        self.inner = inner
        self.preset = preset
        self._event_callback = event_callback
        self._notifier = notifier
        inner_model = getattr(inner, "model", None)
        self.model = f"saver({preset.name}:{inner_model or 'unknown'})"
        self._addendum: str | None = None
        if preset.skill_name is not None:
            self._addendum = _ADDENDUM_PREFIX + builtin_skill_body(preset.skill_name)

    def _prepare(self, request: ModelRequest) -> tuple[ModelRequest, dict[str, Any] | None]:
        original: Messages = request.messages
        messages = original
        addendum = False
        if self._addendum is not None:
            already = any(
                isinstance(message, dict)
                and message.get("role") == "system"
                and message.get("content") == self._addendum
                for message in messages
            )
            if not already:
                insert_at = 0
                while insert_at < len(messages) and messages[insert_at].get("role") == "system":
                    insert_at += 1
                messages = [
                    *messages[:insert_at],
                    {"role": "system", "content": self._addendum},
                    *messages[insert_at:],
                ]
                addendum = True
        applied: list[str] = []
        if self.preset.pipeline is not None:
            result = run_pipeline(messages, self.preset.pipeline)
            applied = list(result.stages_applied)
            messages = result.messages
        if not applied and not addendum:
            return request, None
        tokens_before = estimate_messages(original)
        tokens_after = estimate_messages(messages)
        saved = (
            round((tokens_before - tokens_after) / tokens_before * 100, 1) if tokens_before else 0.0
        )
        payload: dict[str, Any] = {
            "preset": self.preset.name,
            "stages_applied": applied,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "saved_percent": saved,
            "message_count": len(messages),
            "addendum": addendum,
        }
        return request.model_copy(update={"messages": messages}), payload

    async def _emit(self, payload: dict[str, Any]) -> None:
        if self._event_callback is not None:
            try:
                result = self._event_callback(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # pragma: no cover
                _LOG.debug("saver event callback error: %s", exc)
        if self._notifier is not None:
            notice = (
                f"⛁ Saver {payload['preset']}: {payload['tokens_before']}→"
                f"{payload['tokens_after']} est tokens"
            )
            try:
                self._notifier(notice)
            except Exception as exc:  # pragma: no cover
                _LOG.debug("saver notifier error: %s", exc)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Compress the request, delegate, return the inner response."""
        prepared, payload = self._prepare(request)
        if payload is not None:
            await self._emit(payload)
        return await self.inner.generate(prepared)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Stream from the inner provider over the compressed request."""
        prepared, payload = self._prepare(request)
        if payload is not None:
            await self._emit(payload)
        if isinstance(self.inner, StreamingModelProvider):
            async for chunk in self.inner.stream(prepared):
                yield chunk
            return
        response = await self.inner.generate(prepared)
        for chunk in response_to_chunks(response):
            yield chunk

    async def aclose(self) -> None:
        """Close the inner provider's client session if it has one."""
        aclose_fn = getattr(self.inner, "aclose", None)
        if callable(aclose_fn):
            try:
                await aclose_fn()
            except Exception as exc:  # pragma: no cover
                _LOG.debug("error closing inner provider %r: %s", self.inner, exc)


__all__ = ["SaverProvider"]
