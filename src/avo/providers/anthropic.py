"""Anthropic Messages API provider.

The provider targets ``POST /v1/messages`` with the ``x-api-key`` and
``anthropic-version`` headers, parses ``tool_use`` content blocks into
:class:`ToolCall` instances, and surfaces ``usage.input_tokens`` /
``usage.output_tokens`` as :class:`TokenUsage`. Missing usage fields leave
``ModelResponse.usage`` as ``None`` so the runtime can mark
``token_accounting_available`` correctly instead of inventing zeros.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

from avo import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.exceptions import ProviderError
from avo.providers.streaming import ModelChunk, response_to_chunks

from .http_common import (
    _AsyncHTTPClient,
    json_safe_content,
    redact_text,
    stream_anthropic_chunks,
)
from .prompt_cache import annotate_anthropic_messages, compute_breakpoints

try:  # pragma: no cover - exercised indirectly by the optional dependency
    import httpx
except ModuleNotFoundError:  # pragma: no cover - httpx is optional at import time
    httpx = None  # type: ignore[assignment]


_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicConfig(BaseModel):
    """Endpoint and credential configuration for the Anthropic Messages API."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: str
    base_url: str = _DEFAULT_BASE_URL

    _api_key: str | None = PrivateAttr(default=None)

    @classmethod
    def from_avo_env(
        cls,
        environ: Mapping[str, str],
        *,
        fallback_model: str,
    ) -> AnthropicConfig:
        """Build config from the AVO_ANTHROPIC_* environment variables."""

        api_key = environ.get("AVO_ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise ValueError("AVO_ANTHROPIC_API_KEY is required when AVO_PROVIDER=anthropic")

        model = environ.get("AVO_ANTHROPIC_MODEL", "").strip() or (fallback_model or _DEFAULT_MODEL)
        base_url = (environ.get("AVO_ANTHROPIC_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip(
            "/"
        )

        config = cls(model=model, base_url=base_url)
        config._api_key = api_key
        return config

    @property
    def endpoint(self) -> str:
        """Return the absolute Messages API URL."""

        return f"{self.base_url}/v1/messages"

    def headers(self) -> dict[str, str]:
        """Anthropic auth, version, and content-type headers."""

        return {
            "x-api-key": self._api_key or "",
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }


class AnthropicProvider:
    """An async ``ModelProvider`` for the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        config: AnthropicConfig,
        max_completion_tokens: int = 1024,
        request_timeout_seconds: float = 30.0,
        *,
        client: _AsyncHTTPClient | None = None,
    ) -> None:
        self._config = config
        self._max_completion_tokens = max_completion_tokens
        self._request_timeout_seconds = request_timeout_seconds
        self._owns_client = client is None
        if client is not None:
            self._client: _AsyncHTTPClient | None = client
        elif httpx is not None:
            self._client = httpx.AsyncClient(timeout=request_timeout_seconds)  # type: ignore[assignment]
        else:  # pragma: no cover - only when httpx is not installed
            self._client = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one final answer or tool-call decision via Anthropic."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "AnthropicProvider requires httpx or an injected client",
                retryable=False,
            )

        payload = self._build_payload(request)
        raw = await self._post(payload)
        return self._parse_response(raw)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Yield :class:`ModelChunk` events from the Anthropic SSE stream."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "AnthropicProvider requires httpx or an injected client",
                retryable=False,
            )
        if not hasattr(self._client, "stream"):
            response = await self.generate(request)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        payload = self._build_payload(request)
        payload["stream"] = True
        async for chunk in stream_anthropic_chunks(
            self._client,
            self._config.endpoint,
            self._config.headers(),
            payload,
            self._request_timeout_seconds,
            transport_name="Anthropic",
        ):
            yield chunk

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for message in request.messages:
            role = message["role"]
            if role == "system":
                content = message.get("content")
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            anthropic_messages.append(_anthropic_message(message))

        if request.cache:
            breakpoints = compute_breakpoints(
                anthropic_messages,
                prefix_override=request.cache_prefix_messages,
            )
            annotate_anthropic_messages(anthropic_messages, breakpoints)

        payload: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._max_completion_tokens,
            "messages": anthropic_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        tools = [
            {
                "name": metadata.name,
                "description": metadata.description,
                "input_schema": metadata.input_schema,
            }
            for metadata in request.tools
        ]
        if tools:
            payload["tools"] = tools
        return payload

    async def _post(self, payload: dict[str, Any]) -> Any:
        assert self._client is not None
        transport_errors: tuple[type[BaseException], ...] = (
            (httpx.HTTPError,) if httpx is not None else ()
        )
        try:
            response = await self._client.post(
                self._config.endpoint,
                headers=self._config.headers(),
                json=payload,
                timeout=self._request_timeout_seconds,
            )
        except transport_errors as exc:
            raise ProviderError(
                f"Anthropic transport failure: {redact_text(str(exc))}",
                retryable=True,
            ) from exc

        status = int(response.status_code)
        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError(
                f"Anthropic request failed with status {status}: {detail}",
                retryable=status == 429 or status >= 500,
            )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"Anthropic returned an unparsable response body: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    @staticmethod
    def _parse_response(payload: object) -> ModelResponse:
        try:
            if not isinstance(payload, dict):
                raise TypeError("expected an object")
            blocks = payload["content"]
            if not isinstance(blocks, list):
                raise ValueError("content must be a list of blocks")

            usage = AnthropicProvider._parse_usage(payload.get("usage"))
            # Adopt the provider's message id when present; the model's
            # default_factory only fires when it is absent.
            id_kwargs: dict[str, str] = {}
            raw_id = payload.get("id")
            if isinstance(raw_id, str) and raw_id:
                id_kwargs["response_id"] = raw_id

            text_parts: list[str] = []
            for block in blocks:
                if not isinstance(block, dict):
                    raise ValueError("content blocks must be objects")
                if block.get("type") == "tool_use":
                    arguments = block["input"]
                    if not isinstance(arguments, dict):
                        raise ValueError("tool_use input must be an object")
                    tool_call = ToolCall(
                        tool_call_id=block["id"],
                        name=block["name"],
                        arguments=arguments,
                    )
                    return ModelResponse(tool_call=tool_call, usage=usage, **id_kwargs)
                if block.get("type") == "text":
                    text = block["text"]
                    if not isinstance(text, str):
                        raise ValueError("text block must contain a string")
                    text_parts.append(text)

            if not text_parts:
                raise ValueError("response contained no text or tool_use blocks")
            return ModelResponse(content="".join(text_parts), usage=usage, **id_kwargs)
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"Invalid Anthropic response: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    @staticmethod
    def _parse_usage(value: object) -> TokenUsage | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("usage must be an object")
        input_tokens = value.get("input_tokens")
        output_tokens = value.get("output_tokens")
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            return None
        return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    async def aclose(self) -> None:
        """Close the underlying client when this provider created it."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()


def _anthropic_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message["role"]
    if role == "assistant" and "tool_call" in message:
        tool_call = message["tool_call"]
        if not isinstance(tool_call, dict):
            raise ValueError("assistant tool_call must be an object")
        return {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_call["tool_call_id"],
                    "name": tool_call["name"],
                    "input": tool_call["arguments"],
                }
            ],
        }
    if role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": json_safe_content(message.get("content")),
                }
            ],
        }
    content = message.get("content")
    if isinstance(content, list):
        # Already a list of typed blocks (text/image from
        # avo.content_blocks). Pass through verbatim — Anthropic
        # accepts this shape natively.
        return {"role": role, "content": content}
    return {"role": role, "content": content}


__all__ = ["AnthropicConfig", "AnthropicProvider"]
