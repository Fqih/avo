"""Live provider for OpenAI's real chat-completions API."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, PrivateAttr

from avo import ModelRequest, ModelResponse
from avo.exceptions import ProviderError

from .http_common import build_openai_payload, parse_openai_response, redact_text

try:  # pragma: no cover - exercised indirectly by the optional dependency
    import httpx
except ModuleNotFoundError:  # pragma: no cover - httpx is optional at import time
    httpx = None  # type: ignore[assignment]

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class _AsyncHTTPClient(Protocol):
    """The minimal async client surface used by :class:`OpenAIProvider`."""

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float | None = ...,  # noqa: ASYNC109 - mirrors the httpx client signature
    ) -> Any: ...

    async def aclose(self) -> None: ...


class OpenAIConfig(BaseModel):
    """Public endpoint metadata with the OpenAI API key stored privately."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: str
    base_url: str = _DEFAULT_BASE_URL

    _api_key: str | None = PrivateAttr(default=None)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> OpenAIConfig:
        """Build real OpenAI configuration from environment variables."""

        env: Mapping[str, str] = os.environ if environ is None else environ
        try:
            model = env["OPENAI_MODEL"]
            api_key = env["OPENAI_API_KEY"]
        except KeyError as exc:
            raise ValueError(
                f"Missing required OpenAI environment variable: {exc.args[0]}"
            ) from exc

        base_url = env.get("OPENAI_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
        config = cls(model=model, base_url=base_url)
        config._api_key = api_key
        return config

    @property
    def endpoint(self) -> str:
        """Return the configured OpenAI chat-completions endpoint."""

        return f"{self.base_url}/chat/completions"

    def headers(self) -> dict[str, str]:
        """Build the OpenAI authorization and content-type headers."""

        return {
            "Authorization": f"Bearer {self._api_key or ''}",
            "Content-Type": "application/json",
        }


class OpenAIProvider:
    """An async ``ModelProvider`` for OpenAI's real API."""

    def __init__(
        self,
        config: OpenAIConfig,
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
            self._client = httpx.AsyncClient(timeout=request_timeout_seconds)
        else:  # pragma: no cover - only when httpx is not installed
            self._client = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one final answer or tool-call decision via OpenAI."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "OpenAIProvider requires httpx or an injected client",
                retryable=False,
            )

        payload = build_openai_payload(
            self._config.model,
            request,
            self._max_completion_tokens,
        )
        raw = await self._post(payload)
        return parse_openai_response(raw)

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
                f"OpenAI transport failure: {self._redact(str(exc))}",
                retryable=True,
            ) from exc

        status = int(response.status_code)
        if status >= 400:
            detail = self._redact(str(getattr(response, "text", "")))
            raise ProviderError.from_status(
                status,
                f"OpenAI request failed with status {status}: {detail}",
            )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"OpenAI returned an unparsable response body: {self._redact(str(exc))}",
                retryable=False,
            ) from exc

    def _redact(self, value: str) -> str:
        """Redact credential patterns and this provider's exact API key."""

        redacted = redact_text(value)
        if self._config._api_key:
            redacted = redacted.replace(self._config._api_key, "[REDACTED]")
        return redacted

    async def aclose(self) -> None:
        """Close the underlying client when this provider created it."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
