"""Google Gemini generateContent provider.

The provider targets the native Gemini REST endpoint
``POST /v1beta/models/{model}:generateContent`` with the
``x-goog-api-key`` header. Roles are mapped onto the ``contents`` schema:
system turns become ``systemInstruction``, Avo ``assistant`` turns become
Gemini ``model`` turns, and tool traffic rides ``functionCall`` /
``functionResponse`` parts. ``usageMetadata`` surfaces as
:class:`TokenUsage`; missing counters leave ``ModelResponse.usage`` as
``None`` so the runtime can mark ``token_accounting_available`` correctly.

Streaming uses the SSE variant ``streamGenerateContent?alt=sse``: each
``data:`` frame is a complete ``GenerateContentResponse`` object whose
text / thought parts, ``functionCall`` parts, ``finishReason``, and
``usageMetadata`` are mapped into :class:`ModelChunk` carriers
(``tool_call_delta`` uses the normalized shape assembled by
:class:`~avo.providers.streaming.StreamAssembler`).

Prompt caching (Gemini implicit context caching) is intentionally not
wired in; ``request.cache`` is ignored by this adapter.

Configuration:

- ``AVO_GEMINI_API_KEY`` — Gemini API key (required).
- ``AVO_GEMINI_BASE_URL`` — override the default base URL.
- ``AVO_GEMINI_MODEL`` — provider-specific model override.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, JsonValue, PrivateAttr

from avo import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.exceptions import ProviderError
from avo.providers.streaming import ModelChunk, response_to_chunks

from .http_common import (
    _AsyncHTTPClient,
    json_safe_content,
    redact_text,
    stream_sse_chunks,
)

try:  # pragma: no cover - exercised indirectly by the optional dependency
    import httpx
except ModuleNotFoundError:  # pragma: no cover - httpx is optional at import time
    httpx = None  # type: ignore[assignment]


_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
_DEFAULT_MODEL = "gemini-2.5-pro"


class GeminiConfig(BaseModel):
    """Endpoint and credential configuration for the Gemini REST API."""

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
    ) -> GeminiConfig:
        """Build config from the AVO_GEMINI_* environment variables."""

        api_key = environ.get("AVO_GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("AVO_GEMINI_API_KEY is required when AVO_PROVIDER=gemini")

        model = environ.get("AVO_GEMINI_MODEL", "").strip() or (fallback_model or _DEFAULT_MODEL)
        base_url = (environ.get("AVO_GEMINI_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip("/")

        config = cls(model=model, base_url=base_url)
        config._api_key = api_key
        return config

    @property
    def endpoint(self) -> str:
        """Return the absolute generateContent URL for the configured model."""

        return f"{self.base_url}/v1beta/models/{self.model}:generateContent"

    @property
    def stream_endpoint(self) -> str:
        """Return the SSE streaming URL for the configured model."""

        return f"{self.base_url}/v1beta/models/{self.model}:streamGenerateContent?alt=sse"

    def headers(self) -> dict[str, str]:
        """Gemini API-key and content-type headers."""

        return {
            "x-goog-api-key": self._api_key or "",
            "Content-Type": "application/json",
        }


class GeminiProvider:
    """An async ``ModelProvider`` and ``StreamingModelProvider`` for Gemini."""

    name = "gemini"

    def __init__(
        self,
        config: GeminiConfig,
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
        else:  # pragma: no cover - only when httpx is not installed
            self._client = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one final answer or tool-call decision via Gemini."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "GeminiProvider requires httpx or an injected client",
                retryable=False,
            )

        payload = self._build_payload(request)
        raw = await self._post(payload)
        return self._parse_response(raw)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Yield :class:`ModelChunk` events from the Gemini SSE stream."""

        if self._client is None:  # pragma: no cover - requires missing httpx
            raise ProviderError(
                "GeminiProvider requires httpx or an injected client",
                retryable=False,
            )
        if not hasattr(self._client, "stream"):
            final = await self.generate(request)
            for chunk in response_to_chunks(final):
                yield chunk
            return

        client = self._client

        def parse_line(line: str) -> list[ModelChunk]:
            return list(_parse_gemini_stream_event(_load_json(line)))

        async for chunk in stream_sse_chunks(
            client,
            self._config.stream_endpoint,
            self._config.headers(),
            self._build_payload(request),
            self._request_timeout_seconds,
            transport_name="Gemini",
            parse_line=parse_line,
        ):
            yield chunk

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in request.messages:
            role = message["role"]
            if role == "system":
                content = message.get("content")
                if isinstance(content, str) and content:
                    system_parts.append(content)
                continue
            contents.append(_gemini_content(message))

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": self._max_completion_tokens},
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        declarations = [
            {
                "name": metadata.name,
                "description": metadata.description,
                "parameters": metadata.input_schema,
            }
            for metadata in request.tools
        ]
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
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
                f"Gemini transport failure: {redact_text(str(exc))}",
                retryable=True,
            ) from exc

        status = int(response.status_code)
        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError(
                f"Gemini request failed with status {status}: {detail}",
                retryable=status == 429 or status >= 500,
            )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"Gemini returned an unparsable response body: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    @staticmethod
    def _parse_response(payload: object) -> ModelResponse:
        try:
            if not isinstance(payload, dict):
                raise TypeError("expected an object")
            candidates = payload["candidates"]
            if not isinstance(candidates, list) or not candidates:
                raise ValueError("candidates must be a non-empty list")
            candidate = candidates[0]
            if not isinstance(candidate, dict):
                raise ValueError("candidate must be an object")
            message = candidate["content"]
            if not isinstance(message, dict):
                raise ValueError("content must be an object")
            parts = message["parts"]
            if not isinstance(parts, list):
                raise ValueError("parts must be a list")

            usage = _parse_gemini_usage(payload.get("usageMetadata"))
            # Adopt the provider's response id when present; the model's
            # default_factory only fires when it is absent.
            id_kwargs: dict[str, str] = {}
            raw_id = payload.get("responseId")
            if isinstance(raw_id, str) and raw_id:
                id_kwargs["response_id"] = raw_id

            for part in parts:
                if not isinstance(part, dict):
                    raise ValueError("content parts must be objects")
                call = part.get("functionCall")
                if isinstance(call, dict):
                    return ModelResponse(
                        tool_call=_parse_gemini_function_call(call),
                        usage=usage,
                        **id_kwargs,
                    )

            text_parts: list[str] = []
            for part in parts:
                assert isinstance(part, dict)
                if part.get("thought"):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            content = "".join(text_parts)
            if not content:
                raise ValueError("response contained no text or functionCall parts")
            return ModelResponse(content=content, usage=usage, **id_kwargs)
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"Invalid Gemini response: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    async def aclose(self) -> None:
        """Close the underlying client when this provider created it."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()


def _gemini_content(message: dict[str, Any]) -> dict[str, Any]:
    role = message["role"]
    if role == "assistant" and "tool_call" in message:
        tool_call = message["tool_call"]
        if not isinstance(tool_call, dict):
            raise ValueError("assistant tool_call must be an object")
        arguments = tool_call["arguments"]
        return {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": tool_call["name"],
                        "args": arguments if isinstance(arguments, dict) else {},
                    }
                }
            ],
        }
    if role == "tool":
        content = message.get("content")
        response: dict[str, JsonValue]
        if isinstance(content, dict):
            response = cast(dict[str, JsonValue], content)
        else:
            response = {"result": json_safe_content(content)}
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": str(message.get("name") or message["tool_call_id"]),
                        "response": response,
                    }
                }
            ],
        }
    content = message.get("content")
    if isinstance(content, list):
        # Translate typed content blocks (text/image from
        # avo.content_blocks) into Gemini parts; images ride inlineData.
        parts: list[dict[str, Any]] = []
        for raw_block in content:
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, Any], raw_block)
            block_type = block.get("type")
            if block_type == "text":
                parts.append({"text": str(block.get("text", ""))})
            elif block_type == "image":
                raw_source = block.get("source")
                source = raw_source if isinstance(raw_source, dict) else {}
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": str(source.get("media_type", "image/png")),
                            "data": str(source.get("data", "")),
                        }
                    }
                )
        return {"role": _gemini_role(role), "parts": parts}
    return {"role": _gemini_role(role), "parts": [{"text": json_safe_content(content)}]}


def _gemini_role(role: str) -> str:
    """Map Avo roles onto Gemini turn authors."""

    return "model" if role == "assistant" else "user"


def _parse_gemini_usage(value: object) -> TokenUsage | None:
    """Parse Gemini ``usageMetadata``; absent or non-numeric → ``None``."""

    if not isinstance(value, dict):
        return None
    input_tokens = value.get("promptTokenCount")
    output_tokens = value.get("candidatesTokenCount")
    if not isinstance(input_tokens, int) and not isinstance(output_tokens, int):
        return None
    return TokenUsage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
        output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
    )


def _parse_gemini_function_call(call: dict[str, Any]) -> ToolCall:
    arguments = call.get("args")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("functionCall args must be an object")
    name = call.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("functionCall must carry a name")
    # Gemini assigns no call id; ToolCall's default_factory fills one so
    # downstream tool-result correlation works unchanged.
    return ToolCall(name=name, arguments=cast(dict[str, JsonValue], arguments))


def _load_json(line: str) -> dict[str, Any] | None:
    """Decode one SSE data payload; malformed frames are skipped."""

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _parse_gemini_stream_event(event: dict[str, Any] | None) -> list[ModelChunk]:
    """Translate one streamed ``GenerateContentResponse`` into chunks.

    Text parts become incremental chunks, ``thought`` parts ride the
    thinking channel, ``functionCall`` parts map onto the normalized
    ``tool_call_delta`` shape (Gemini delivers the full ``args`` object
    per frame, so the JSON string arrives as one fragment), and the
    terminal frame carries ``finishReason`` plus ``usageMetadata``.
    """

    if event is None:
        return []
    chunks: list[ModelChunk] = []
    usage = _parse_gemini_usage(event.get("usageMetadata"))
    response_id = event.get("responseId")
    carried_id = response_id if isinstance(response_id, str) and response_id else None

    candidates = event.get("candidates")
    finish_reason: str | None = None
    call_index = 0
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        candidate = candidates[0]
        raw_finish = candidate.get("finishReason")
        if isinstance(raw_finish, str) and raw_finish:
            finish_reason = raw_finish
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                call = part.get("functionCall")
                if isinstance(call, dict):
                    try:
                        parsed = _parse_gemini_function_call(call)
                    except ValueError:
                        continue
                    chunks.append(
                        ModelChunk(
                            tool_call_delta={
                                "index": call_index,
                                "id": parsed.tool_call_id,
                                "name": parsed.name,
                                "arguments": json.dumps(parsed.arguments, sort_keys=True),
                            },
                            response_id=carried_id,
                        )
                    )
                    call_index += 1
                    continue
                text = part.get("text")
                if not isinstance(text, str) or not text:
                    continue
                if part.get("thought"):
                    chunks.append(ModelChunk(thought=text, response_id=carried_id))
                else:
                    chunks.append(ModelChunk(text=text, response_id=carried_id))

    if finish_reason is not None or usage is not None:
        chunks.append(ModelChunk(finish_reason=finish_reason, usage=usage, response_id=carried_id))
    return chunks


__all__ = ["GeminiConfig", "GeminiProvider"]
