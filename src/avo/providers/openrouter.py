"""OpenRouter chat-completions provider.

OpenRouter provides access to hundreds of models (including free models)
via the OpenAI ``/chat/completions`` format at
``https://openrouter.ai/api/v1``.

Configuration:
- ``AVO_OPENROUTER_API_KEY`` or ``OPENROUTER_API_KEY`` — OpenRouter API key.
- ``AVO_OPENROUTER_BASE_URL`` — override the default base URL.
- ``AVO_OPENROUTER_MODEL`` or ``AVO_MODEL`` — model identifier.
- ``AVO_OPENROUTER_SITE_URL`` / ``AVO_OPENROUTER_APP_NAME`` — optional OpenRouter
  leaderboard metadata.
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

try:  # pragma: no cover
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_SITE_URL = "https://github.com/Fqih/avo"
_DEFAULT_APP_NAME = "Avo Agent Runtime"


class OpenRouterConfig(BaseModel):
    """Endpoint and credential configuration for OpenRouter."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: str
    base_url: str = _DEFAULT_BASE_URL
    site_url: str = _DEFAULT_SITE_URL
    app_name: str = _DEFAULT_APP_NAME

    _api_key: str | None = PrivateAttr(default=None)

    @classmethod
    def from_avo_env(
        cls,
        environ: Mapping[str, str],
        *,
        fallback_model: str,
    ) -> OpenRouterConfig:
        api_key = (
            environ.get("AVO_OPENROUTER_API_KEY", "").strip()
            or environ.get("OPENROUTER_API_KEY", "").strip()
        )
        if not api_key:
            raise ValueError(
                "AVO_OPENROUTER_API_KEY or OPENROUTER_API_KEY is required "
                "when AVO_PROVIDER=openrouter"
            )
        model = environ.get("AVO_OPENROUTER_MODEL", "").strip() or fallback_model
        base_url = (environ.get("AVO_OPENROUTER_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip(
            "/"
        )
        site_url = environ.get("AVO_OPENROUTER_SITE_URL", "").strip() or _DEFAULT_SITE_URL
        app_name = environ.get("AVO_OPENROUTER_APP_NAME", "").strip() or _DEFAULT_APP_NAME

        config = cls(model=model, base_url=base_url, site_url=site_url, app_name=app_name)
        config._api_key = api_key
        return config

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key or ''}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.app_name,
        }


class OpenRouterProvider:
    """An async ``ModelProvider`` and ``StreamingModelProvider`` for OpenRouter."""

    name = "openrouter"

    def __init__(
        self,
        config: OpenRouterConfig,
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
        else:
            self._client = None

    def _require_client(self) -> _AsyncHTTPClient:
        if self._client is None:
            if httpx is None:
                raise ProviderError(
                    "openrouter provider requires httpx; install with pip install 'avo[providers]'"
                )
            self._client = httpx.AsyncClient(timeout=self._request_timeout_seconds)  # type: ignore[assignment]
        assert self._client is not None
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        client = self._require_client()
        payload = build_openai_payload(
            request=request,
            model=self.model,
            max_completion_tokens=self._max_completion_tokens,
        )
        try:
            response = await client.post(
                self._config.endpoint,
                headers=self._config.headers(),
                json=payload,
                timeout=self._request_timeout_seconds,
            )
        except Exception as exc:
            msg = redact_text(str(exc))
            raise ProviderError(f"openrouter request failed: {msg}") from exc

        status = int(response.status_code)
        if status >= 400:
            msg = redact_text(str(getattr(response, "text", "")))
            raise ProviderError(
                f"openrouter API error {status}: {msg}",
                retryable=status == 429 or status >= 500,
            )

        try:
            data: dict[str, Any] = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(f"openrouter returned non-JSON payload: {exc}") from exc

        return parse_openai_response(data)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        client = self._require_client()
        payload = build_openai_payload(
            request=request,
            model=self.model,
            max_completion_tokens=self._max_completion_tokens,
            stream=True,
        )
        async for chunk in stream_openai_chunks(
            client,
            self._config.endpoint,
            self._config.headers(),
            payload,
            self._request_timeout_seconds,
            transport_name="OpenRouter",
        ):
            yield chunk
