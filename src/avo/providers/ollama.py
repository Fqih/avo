"""Native Ollama chat-completions provider.

Ollama's native ``/api/chat`` endpoint accepts the same OpenAI-compatible
tool schema and returns an assistant ``message`` with optional
``tool_calls``. Token usage surfaces as ``prompt_eval_count`` and
``eval_count`` only when the response is finished, so the provider must
leave ``usage`` unset whenever those fields are absent rather than fabricate
zeros.

Configuration comes from the AVO_-prefixed environment documented in
:mod:`avo.config`. The ``AUTH_TOKEN``/``OPENAI_AUTH_TOKEN``
convention used by the legacy live-benchmark harness does not apply here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, PrivateAttr

from avo import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.exceptions import ProviderError

from .http_common import _AsyncHTTPClient, json_safe_content, redact_text

try:  # pragma: no cover - exercised indirectly by the optional dependency
    import httpx
except ModuleNotFoundError:  # pragma: no cover - httpx is optional at import time
    httpx = None  # type: ignore[assignment]


_DEFAULT_BASE_URL = "http://localhost:11434"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OllamaConfig(BaseModel):
    """Endpoint configuration for a local Ollama instance."""

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
    ) -> OllamaConfig:
        """Build config from the AVO_OLLAMA_* environment variables."""

        model = environ.get("AVO_OLLAMA_MODEL", "").strip() or fallback_model
        base_url = (environ.get("AVO_OLLAMA_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip("/")
        api_key = environ.get("AVO_OLLAMA_API_KEY", "").strip() or None
        config = cls(model=model, base_url=base_url)
        config._api_key = api_key
        return config

    @property
    def endpoint(self) -> str:
        """Return the absolute native chat-completions URL."""

        return f"{self.base_url}/api/chat"

    def headers(self) -> dict[str, str]:
        """Authorization headers; Ollama itself does not require auth."""

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


class OllamaProvider:
    """An async ``ModelProvider`` for a local Ollama server."""

    def __init__(
        self,
        config: OllamaConfig,
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
            # Local Ollama must not accidentally go through a corporate or
            # shell-configured HTTP proxy. Some proxies return a misleading
            # 200 with an empty body for POST /api/chat.
            trust_env = not _is_local_base_url(config.base_url)
            self._client = httpx.AsyncClient(  # type: ignore[assignment]
                timeout=request_timeout_seconds,
                trust_env=trust_env,
            )
        else:  # pragma: no cover - only when httpx is not installed
            self._client = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one final answer or tool-call decision via Ollama."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "OllamaProvider requires httpx or an injected client",
                retryable=False,
            )

        payload = self._build_payload(request)
        raw = await self._post(payload)
        return self._parse_response(raw, allowed_tool_names={tool.name for tool in request.tools})

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        messages = [_ollama_message(message) for message in request.messages]
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": self._max_completion_tokens},
        }
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
        if tools:
            payload["tools"] = tools
        return payload

    async def _post(self, payload: dict[str, Any]) -> Any:
        assert self._client is not None
        transport_errors: tuple[type[BaseException], ...] = (
            (httpx.HTTPError,) if httpx is not None else ()
        )
        for attempt in range(2):
            try:
                response = await self._client.post(
                    self._config.endpoint,
                    headers=self._config.headers(),
                    json=payload,
                    timeout=self._request_timeout_seconds,
                )
            except transport_errors as exc:
                raise ProviderError(
                    f"Ollama transport failure: {redact_text(str(exc))}",
                    retryable=True,
                ) from exc

            status = int(response.status_code)
            if status >= 400:
                detail = redact_text(str(getattr(response, "text", "")))
                raise ProviderError.from_status(
                    status,
                    f"Ollama request failed with status {status}: {detail}",
                )

            try:
                return response.json()
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                body = str(getattr(response, "text", "")).strip()
                if not body and attempt == 0:
                    continue
                if not body:
                    raw_headers = getattr(response, "headers", {})
                    content_type = str(
                        raw_headers.get("content-type", "unknown")
                        if hasattr(raw_headers, "get")
                        else "unknown"
                    )
                    raise ProviderError(
                        "Ollama returned an empty response body after retry "
                        f"(status={status}, content-type={content_type}, "
                        f"endpoint={self._config.endpoint})",
                        retryable=True,
                    ) from exc
                raise ProviderError(
                    f"Ollama returned an unparsable response body: {redact_text(str(exc))}",
                    retryable=False,
                ) from exc

        raise ProviderError("Ollama returned no response body after retry", retryable=True)

    @staticmethod
    def _parse_response(
        payload: object,
        *,
        allowed_tool_names: set[str] | None = None,
    ) -> ModelResponse:
        try:
            if not isinstance(payload, dict):
                raise TypeError("expected an object")
            message = payload["message"]
            if not isinstance(message, dict):
                raise ValueError("message must be an object")
            usage = OllamaProvider._parse_usage(payload)

            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                tool_call = OllamaProvider._parse_tool_call(tool_calls)
                return ModelResponse(tool_call=tool_call, usage=usage)

            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError("message content must be a string")
            legacy_tool_call = _parse_legacy_json_tool_call(content, allowed_tool_names or set())
            if legacy_tool_call is not None:
                return ModelResponse(tool_call=legacy_tool_call, usage=usage)
            return ModelResponse(content=content, usage=usage)
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"Invalid Ollama response: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    @staticmethod
    def _parse_usage(payload: dict[str, Any]) -> TokenUsage | None:
        """Return usage only when Ollama reported both halves."""

        prompt_eval = payload.get("prompt_eval_count")
        eval_count = payload.get("eval_count")
        if not isinstance(prompt_eval, int) or not isinstance(eval_count, int):
            return None
        if prompt_eval < 0 or eval_count < 0:
            raise ValueError("token counts must be non-negative")
        return TokenUsage(input_tokens=prompt_eval, output_tokens=eval_count)

    @staticmethod
    def _parse_tool_call(value: object) -> ToolCall:
        if not isinstance(value, list) or not value:
            raise ValueError("tool_calls must be a non-empty list")
        raw_call = value[0]
        if not isinstance(raw_call, dict):
            raise ValueError("tool call must be an object")
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise ValueError("tool call.function must be an object")
        raw_arguments = function.get("arguments")
        arguments = _coerce_arguments(raw_arguments)
        return ToolCall(
            tool_call_id=str(raw_call.get("id") or ""),
            name=str(function.get("name") or ""),
            arguments=arguments,
        )

    async def aclose(self) -> None:
        """Close the underlying client when this provider created it."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()


def _is_local_base_url(base_url: str) -> bool:
    """Return whether an Ollama base URL points at the local machine."""

    return (urlparse(base_url).hostname or "").lower() in _LOCAL_HOSTS


def _ollama_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message["role"]
    if role == "assistant" and "tool_call" in message:
        tool_call = message["tool_call"]
        if not isinstance(tool_call, dict):
            raise ValueError("assistant tool_call must be an object")
        return {
            "role": "assistant",
            "content": tool_call.get("content", ""),
            "tool_calls": [
                {
                    "function": {
                        "name": tool_call.get("name"),
                        "arguments": tool_call.get("arguments", {}),
                    }
                }
            ],
        }
    if role == "tool":
        return {
            "role": "tool",
            "content": json_safe_content(message.get("content")),
        }
    content = message.get("content")
    if isinstance(content, list):
        # Translate typed blocks (text/image). Text is concatenated;
        # images contribute to the ``images`` sibling array that Ollama
        # expects (base64 strings, no data: prefix).
        text_parts: list[str] = []
        images: list[str] = []
        for raw_block in content:
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, Any], raw_block)
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(str(block.get("text", "")))
            elif block_type == "image":
                raw_source = block.get("source")
                source = raw_source if isinstance(raw_source, dict) else {}
                data = source.get("data") if isinstance(source, dict) else None
                if isinstance(data, str) and data:
                    images.append(data)
        out: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
        if images:
            out["images"] = images
        return out
    return {"role": role, "content": content}


def _coerce_arguments(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("tool call arguments must decode as JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("tool call arguments must decode to an object")
        return decoded
    raise ValueError("tool call arguments must be a string or object")


def _parse_legacy_json_tool_call(content: str, allowed_names: set[str]) -> ToolCall | None:
    """Recover strict JSON tool calls emitted as plain text by small Ollama models."""

    if not allowed_names:
        return None
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict) or set(decoded) != {"name", "arguments"}:
        return None
    name = decoded.get("name")
    arguments = decoded.get("arguments")
    if not isinstance(name, str) or not name or name not in allowed_names:
        return None
    if not isinstance(arguments, dict):
        return None
    return ToolCall(name=name, arguments=arguments)


__all__ = ["OllamaConfig", "OllamaProvider"]
