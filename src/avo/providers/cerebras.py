"""Cerebras Inference chat-completions provider.

Cerebras exposes an OpenAI-compatible endpoint at
``https://api.cerebras.ai/v1``. This provider reuses the shared
helpers from :mod:`avo.providers.http_common`; only the base URL and
configuration namespace differ from OpenAI.

Configuration:

- ``AVO_CEREBRAS_API_KEY`` — Cerebras API key (required).
- ``AVO_CEREBRAS_BASE_URL`` — override the default base URL.
- ``AVO_CEREBRAS_MODEL`` — default model identifier.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

from avo import ModelRequest, ModelResponse
from avo.exceptions import ProviderError
from avo.providers.streaming import ModelChunk

from .http_common import (
    _AsyncHTTPClient,
    build_openai_payload,
    parse_openai_response,
    redact_text,
    stream_openai_chunks,
)

try:  # pragma: no cover - exercised indirectly by the optional dependency
    import httpx
except ModuleNotFoundError:  # pragma: no cover - httpx is optional at import time
    httpx = None  # type: ignore[assignment]


_DEFAULT_BASE_URL = "https://api.cerebras.ai/v1"


class CerebrasConfig(BaseModel):
    """Endpoint and credential configuration for Cerebras Inference."""

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
    ) -> CerebrasConfig:
        api_key = environ.get("AVO_CEREBRAS_API_KEY", "").strip()
        if not api_key:
            raise ValueError("AVO_CEREBRAS_API_KEY is required when AVO_PROVIDER=cerebras")
        model = environ.get("AVO_CEREBRAS_MODEL", "").strip() or fallback_model
        base_url = (environ.get("AVO_CEREBRAS_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip(
            "/"
        )
        config = cls(model=model, base_url=base_url)
        config._api_key = api_key
        return config

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key or ''}",
            "Content-Type": "application/json",
        }


class CerebrasProvider:
    """An async ``ModelProvider`` for the Cerebras Inference API."""

    name = "cerebras"

    def __init__(
        self,
        config: CerebrasConfig,
        max_completion_tokens: int = 1024,
        request_timeout_seconds: float = 30.0,
        *,
        client: _AsyncHTTPClient | None = None,
    ) -> None:
        self._config = config
        self._max_completion_tokens = max_completion_tokens
        self._request_timeout_seconds = request_timeout_seconds
        self.model = config.model
        self._owns_client = client is None
        if client is not None:
            self._client: _AsyncHTTPClient | None = client
        elif httpx is not None:
            self._client = httpx.AsyncClient(timeout=request_timeout_seconds)  # type: ignore[assignment]
        else:  # pragma: no cover - requires missing httpx
            self._client = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "CerebrasProvider requires httpx or an injected client",
                retryable=False,
            )
        payload = build_openai_payload(
            self._config.model,
            request,
            self._max_completion_tokens,
        )
        raw = await self._post(payload)
        return parse_openai_response(raw)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Yield :class:`ModelChunk` events from the Cerebras SSE stream."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "CerebrasProvider requires httpx or an injected client",
                retryable=False,
            )
        if not hasattr(self._client, "stream"):
            response = await self.generate(request)
            yield ModelChunk(text=response.content or "")
            return
        payload = build_openai_payload(
            self._config.model,
            request,
            self._max_completion_tokens,
            stream=True,
        )
        async for chunk in stream_openai_chunks(
            self._client,
            self._config.endpoint,
            self._config.headers(),
            payload,
            self._request_timeout_seconds,
            transport_name="Cerebras",
        ):
            yield chunk

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
                f"Cerebras transport failure: {redact_text(str(exc))}",
                retryable=True,
            ) from exc

        status = int(response.status_code)
        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError.from_status(
                status,
                f"Cerebras request failed with status {status}: {detail}",
            )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"Cerebras returned an unparsable response body: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


__all__ = ["CerebrasConfig", "CerebrasProvider"]
