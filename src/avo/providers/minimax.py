"""MiniMax live provider supporting OpenAI- and Anthropic-compatible styles."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr

from avo import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.exceptions import ProviderError

from .http_common import (
    _AsyncHTTPClient,
    build_openai_payload,
    json_safe_content,
    parse_openai_response,
    redact_text,
)

try:  # pragma: no cover - exercised indirectly by the optional dependency
    import httpx
except ModuleNotFoundError:  # pragma: no cover - httpx is optional at import time
    httpx = None  # type: ignore[assignment]

ApiStyle = Literal["openai", "anthropic"]
_ANTHROPIC_VERSION = "2023-06-01"


class MiniMaxConfig(BaseModel):
    """Endpoint and credential configuration for the MiniMax provider.

    Credentials are stored as private attributes so they never appear in
    ``repr``, ``model_dump``/``model_dump_json``, or validation errors.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: str
    base_url: str
    api_style: ApiStyle = "openai"

    _auth_token: str | None = PrivateAttr(default=None)
    _openai_auth_token: str | None = PrivateAttr(default=None)
    _avo_api_key: str | None = PrivateAttr(default=None)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MiniMaxConfig:
        """Build a config from a mapping (defaults to the process environment).

        Legacy live-benchmark harness: ``MODEL_MINIMAX`` + ``BASE_URL`` plus a
        style-dependent ``OPENAI_AUTH_TOKEN`` (openai) or ``AUTH_TOKEN``
        (anthropic). Kept verbatim so reproduction commands do not change.
        """

        env: Mapping[str, str] = os.environ if environ is None else environ

        try:
            model = env["MODEL_MINIMAX"]
            base_url = env["BASE_URL"]
        except KeyError as exc:
            raise ValueError(
                f"Missing required MiniMax environment variable: {exc.args[0]}"
            ) from exc

        api_style = env.get("MINIMAX_API_STYLE", "openai")
        if api_style not in ("openai", "anthropic"):
            raise ValueError(
                f"Unsupported MINIMAX_API_STYLE {api_style!r}; expected 'openai' or 'anthropic'"
            )

        config = cls(model=model, base_url=base_url, api_style=api_style)
        if api_style == "openai":
            try:
                config._openai_auth_token = env["OPENAI_AUTH_TOKEN"]
            except KeyError as exc:
                raise ValueError(
                    "Missing OPENAI_AUTH_TOKEN for the 'openai' MiniMax API style"
                ) from exc
        else:
            try:
                config._auth_token = env["AUTH_TOKEN"]
            except KeyError as exc:
                raise ValueError(
                    "Missing AUTH_TOKEN for the 'anthropic' MiniMax API style"
                ) from exc
        return config

    @classmethod
    def from_avo_env(
        cls,
        environ: Mapping[str, str],
        *,
        fallback_model: str,
    ) -> MiniMaxConfig:
        """Build config from the AVO_MINIMAX_* environment variables.

        ``AVO_MINIMAX_API_KEY`` is required and serves whichever style
        ``AVO_MINIMAX_API_STYLE`` selects (default ``anthropic`` per the
        AVO_ contract).
        """

        api_key = environ.get("AVO_MINIMAX_API_KEY", "").strip()
        if not api_key:
            raise ValueError("AVO_MINIMAX_API_KEY is required when AVO_PROVIDER=minimax")

        model = environ.get("AVO_MINIMAX_MODEL", "").strip() or fallback_model
        base_url = (
            environ.get("AVO_MINIMAX_BASE_URL", "").strip() or "https://api.minimax.io"
        ).rstrip("/")
        api_style = environ.get("AVO_MINIMAX_API_STYLE", "anthropic").strip().lower()
        if api_style not in ("openai", "anthropic"):
            raise ValueError(
                f"Unsupported AVO_MINIMAX_API_STYLE {api_style!r}; expected 'openai' or 'anthropic'"
            )

        config = cls(model=model, base_url=base_url, api_style=api_style)
        config._avo_api_key = api_key
        return config

    @property
    def endpoint(self) -> str:
        """Return the absolute request URL for the configured API style.

        Accepts both forms of ``base_url``:

        - bare host (``https://api.minimax.io``) -> appends the style-specific
          path suffix;
        - full URL with the style-specific path already present
          (``https://api.minimax.io/anthropic``) -> returns it unchanged.

        The detection avoids the double-suffix bug when an operator
        pastes a full URL into the env var.
        """

        base = self.base_url.rstrip("/")
        if self.api_style == "openai" and base.endswith("/v1/chat/completions"):
            return base
        if self.api_style == "anthropic" and base.endswith("/anthropic/v1/messages"):
            return base
        if self.api_style == "openai":
            return f"{base}/v1/chat/completions"
        # anthropic (default)
        if base.endswith("/anthropic"):
            return f"{base}/v1/messages"
        return f"{base}/anthropic/v1/messages"

    def headers(self) -> dict[str, str]:
        """Return only the auth headers for the selected style plus content type."""

        avo_key = self._avo_api_key
        if self.api_style == "openai":
            token = avo_key or self._openai_auth_token or ""
            return {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        token = avo_key or self._auth_token or ""
        return {
            "x-api-key": token,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }


class MiniMaxProvider:
    """An async ``ModelProvider`` for MiniMax OpenAI/Anthropic-compatible APIs."""

    def __init__(
        self,
        config: MiniMaxConfig,
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
        """Generate one final answer or tool-call decision via MiniMax."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "MiniMaxProvider requires httpx or an injected client",
                retryable=False,
            )

        if self._config.api_style == "openai":
            payload = build_openai_payload(self._config.model, request, self._max_completion_tokens)
        else:
            payload = self._build_anthropic_payload(request)

        raw = await self._post(payload)
        if self._config.api_style == "openai":
            return parse_openai_response(raw)
        return self._parse_anthropic_response(raw)

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
                f"MiniMax transport failure: {redact_text(str(exc))}",
                retryable=True,
            ) from exc

        status = int(response.status_code)
        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError.from_status(
                status,
                f"MiniMax request failed with status {status}: {detail}",
            )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"MiniMax returned an unparsable response body: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    def _build_anthropic_payload(self, request: ModelRequest) -> dict[str, Any]:
        messages = [self._anthropic_message(message) for message in request.messages]
        payload: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._max_completion_tokens,
            "messages": messages,
        }
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

    @staticmethod
    def _anthropic_message(message: dict[str, Any]) -> dict[str, Any]:
        role = message["role"]
        if role == "assistant" and "tool_call" in message:
            tool_call = message["tool_call"]
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
                        "content": json_safe_content(message["content"]),
                    }
                ],
            }
        return {"role": role, "content": message["content"]}

    @staticmethod
    def _parse_anthropic_response(payload: object) -> ModelResponse:
        try:
            if not isinstance(payload, dict):
                raise TypeError("expected an object")
            blocks = payload["content"]
            if not isinstance(blocks, list):
                raise ValueError("content must be a list of blocks")

            usage = MiniMaxProvider._parse_anthropic_usage(payload.get("usage"))

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
                    return ModelResponse(tool_call=tool_call, usage=usage)
                if block.get("type") == "text":
                    text = block["text"]
                    if not isinstance(text, str):
                        raise ValueError("text block must contain a string")
                    text_parts.append(text)

            if not text_parts:
                raise ValueError("response contained no text or tool_use blocks")
            return ModelResponse(content="".join(text_parts), usage=usage)
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"Invalid Anthropic-compatible response: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    @staticmethod
    def _parse_anthropic_usage(value: object) -> TokenUsage | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("usage must be an object")
        return TokenUsage(
            input_tokens=value["input_tokens"],
            output_tokens=value["output_tokens"],
        )

    async def aclose(self) -> None:
        """Close the underlying client when this provider created it."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
