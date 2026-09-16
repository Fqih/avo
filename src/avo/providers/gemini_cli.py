"""Gemini CLI provider for Google OAuth / Cloud Code PA subscription backend.

Targets ``POST https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse``
with Google Gemini content schema, bearer authentication, and Google genai SDK identity headers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from avo import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.auth import AuthError
from avo.exceptions import ProviderError
from avo.oauth.gate import require_subscription_allowed
from avo.oauth.refresh import ensure_fresh
from avo.oauth.registry import OAUTH
from avo.oauth.store import get_credential
from avo.providers.http_common import _AsyncHTTPClient, redact_text
from avo.providers.streaming import ModelChunk, response_to_chunks

try:  # pragma: no cover
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


_DEFAULT_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
_DEFAULT_MODEL = "gemini-2.5-pro"


class GeminiCliConfig(BaseModel):
    """Endpoint and credential configuration for Gemini CLI subscription backend."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: str = _DEFAULT_MODEL
    base_url: str = _DEFAULT_BASE_URL
    auth_mode: Literal["oauth"] = "oauth"

    @property
    def endpoint(self) -> str:
        """Return the absolute streamGenerateContent endpoint with alt=sse."""

        base = self.base_url.rstrip("/")
        if ":streamGenerateContent" in base:
            return base
        return f"{base}:streamGenerateContent?alt=sse"

    def request_headers(self, token: str | None = None) -> dict[str, str]:
        """Headers required by Gemini CLI endpoint."""

        hdrs: dict[str, str] = {
            "Authorization": f"Bearer {token or ''}",
            "Content-Type": "application/json",
        }
        entry = OAUTH.get("gemini")
        if entry and entry.identity_headers:
            hdrs.update(entry.identity_headers)
        return hdrs

    def headers(self) -> dict[str, str]:
        """Return headers with empty or default token."""

        return self.request_headers()

    @classmethod
    def from_avo_env(
        cls,
        environ: Mapping[str, str],
        *,
        fallback_model: str,
    ) -> GeminiCliConfig:
        """Build GeminiCliConfig verifying subscription opt-in and stored credential."""

        require_subscription_allowed(environ)
        model = environ.get("AVO_GEMINI_CLI_MODEL", "").strip() or (
            fallback_model or _DEFAULT_MODEL
        )
        base_url = (environ.get("AVO_GEMINI_CLI_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip(
            "/"
        )

        stored = get_credential("gemini")
        if stored is None:
            raise AuthError(
                "No stored credential found for gemini. Run 'avo login gemini' to log in."
            )

        return cls(model=model, base_url=base_url)


class GeminiCliProvider:
    """An async ``ModelProvider`` for Gemini subscription via cloudcode-pa."""

    name = "gemini_cli"

    def __init__(
        self,
        config: GeminiCliConfig,
        max_completion_tokens: int = 1024,
        request_timeout_seconds: float = 30.0,
        *,
        client: _AsyncHTTPClient | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self._config = config
        self._max_completion_tokens = max_completion_tokens
        self._request_timeout_seconds = request_timeout_seconds
        self._token_provider = token_provider
        self._owns_client = client is None
        if client is not None:
            self._client: _AsyncHTTPClient | None = client
        elif httpx is not None:
            self._client = httpx.AsyncClient(timeout=request_timeout_seconds)  # type: ignore[assignment]
        else:  # pragma: no cover
            self._client = None

    async def _get_headers(self) -> dict[str, str]:
        token: str | None
        if self._token_provider is not None:
            token = await self._token_provider()
        else:
            cred = await ensure_fresh("gemini")
            token = cred.access_token
        return self._config.request_headers(token)

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        tool_names: dict[str, str] = {}
        for message in request.messages:
            role = message.get("role")
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            cid = str(tc.get("id") or tc.get("tool_call_id") or "")
                            name = str(tc.get("name") or "")
                            if cid and name:
                                tool_names[cid] = name
                elif "tool_call" in message:
                    tc = message["tool_call"]
                    if isinstance(tc, dict):
                        cid = str(tc.get("id") or tc.get("tool_call_id") or "")
                        name = str(tc.get("name") or "")
                        if cid and name:
                            tool_names[cid] = name

            if role == "system":
                continue
            if role == "user":
                content = str(message.get("content") or "")
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    parts: list[dict[str, Any]] = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            args = tc.get("arguments") or {}
                            parts.append(
                                {
                                    "functionCall": {
                                        "name": str(tc.get("name") or ""),
                                        "args": args if isinstance(args, dict) else {},
                                    }
                                }
                            )
                    contents.append({"role": "model", "parts": parts})
                elif "tool_call" in message:
                    tc = message["tool_call"]
                    if isinstance(tc, dict):
                        args = tc.get("arguments") or {}
                        contents.append(
                            {
                                "role": "model",
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": str(tc.get("name") or ""),
                                            "args": args if isinstance(args, dict) else {},
                                        }
                                    }
                                ],
                            }
                        )
                else:
                    content = str(message.get("content") or "")
                    contents.append({"role": "model", "parts": [{"text": content}]})
            elif role == "tool":
                cid = str(message.get("tool_call_id") or message.get("id") or "")
                fn_name = str(message.get("name") or tool_names.get(cid) or cid)
                tool_content = message.get("content", "")
                response_dict = (
                    tool_content
                    if isinstance(tool_content, dict)
                    else {"result": str(tool_content)}
                )
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": fn_name,
                                    "response": response_dict,
                                }
                            }
                        ],
                    }
                )

        payload: dict[str, Any] = {
            "model": self._config.model,
            "contents": contents,
        }
        system_parts = [
            str(m.get("content", ""))
            for m in request.messages
            if m.get("role") == "system" and m.get("content")
        ]
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        }
                        for tool in request.tools
                    ]
                }
            ]
        return payload

    async def _post(self, payload: dict[str, Any], *, is_retry: bool = False) -> Any:
        assert self._client is not None
        transport_errors: tuple[type[BaseException], ...] = (
            (httpx.HTTPError,) if httpx is not None else ()
        )
        headers = await self._get_headers()
        try:
            response = await self._client.post(
                self._config.endpoint,
                headers=headers,
                json=payload,
                timeout=self._request_timeout_seconds,
            )
        except transport_errors as exc:
            raise ProviderError(
                f"Gemini transport failure: {redact_text(str(exc))}",
                retryable=True,
            ) from exc

        status = int(response.status_code)
        if status == 401 and not is_retry:
            if self._token_provider is None:
                await ensure_fresh("gemini", force=True)
            return await self._post(payload, is_retry=True)

        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError(
                f"Gemini request failed with status {status}: {detail}",
                retryable=status == 429 or status >= 500,
            )

        text = str(getattr(response, "text", ""))
        merged_parts: list[dict[str, Any]] = []
        final_raw: dict[str, Any] = {}
        has_sse = False

        for line in text.splitlines():
            sline = line.strip()
            if sline.startswith("data: "):
                has_sse = True
                data_str = sline[6:].strip()
                if data_str == "[DONE]":
                    continue
                try:
                    obj = json.loads(data_str)
                    if isinstance(obj, dict):
                        final_raw = obj
                        candidates = obj.get("candidates")
                        if isinstance(candidates, list) and candidates:
                            cand = candidates[0]
                            if isinstance(cand, dict):
                                msg = cand.get("content")
                                if isinstance(msg, dict):
                                    parts = msg.get("parts")
                                    if isinstance(parts, list):
                                        merged_parts.extend(parts)
                except Exception:
                    pass

        if has_sse and final_raw:
            if merged_parts:
                candidates = final_raw.get("candidates", [{}])
                candidates[0].setdefault("content", {})["parts"] = merged_parts
                final_raw["candidates"] = candidates
            return final_raw

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"Gemini returned an unparsable response body: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    def _parse_response(self, raw: Any) -> ModelResponse:
        if not isinstance(raw, dict):
            raise ProviderError("Invalid Gemini response", retryable=False)

        candidates = raw.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderError("Invalid Gemini response: empty candidates", retryable=False)

        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise ProviderError("Invalid Gemini candidate", retryable=False)

        message = candidate.get("content")
        if not isinstance(message, dict):
            raise ProviderError("Invalid candidate content", retryable=False)

        parts = message.get("parts")
        if not isinstance(parts, list):
            raise ProviderError("Invalid content parts", retryable=False)

        raw_usage = raw.get("usageMetadata")
        usage: TokenUsage | None = None
        if isinstance(raw_usage, dict):
            in_tok = raw_usage.get("promptTokenCount")
            out_tok = raw_usage.get("candidatesTokenCount")
            if isinstance(in_tok, int) and isinstance(out_tok, int):
                usage = TokenUsage(input_tokens=in_tok, output_tokens=out_tok)

        for part in parts:
            if not isinstance(part, dict):
                continue
            fc = part.get("functionCall")
            if isinstance(fc, dict):
                args = fc.get("args") or {}
                call_id = str(fc.get("id") or "fc_gemini")
                tool_call = ToolCall(
                    tool_call_id=call_id,
                    name=str(fc.get("name", "")),
                    arguments=args if isinstance(args, dict) else {},
                )
                return ModelResponse(tool_call=tool_call, usage=usage)

        text_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("thought"):
                continue
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)

        content = "".join(text_parts)
        return ModelResponse(content=content, usage=usage)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one final answer or tool call via Gemini CLI."""

        if self._client is None:
            raise ProviderError(
                "GeminiCliProvider requires httpx or an injected client",
                retryable=False,
            )

        payload = self._build_payload(request)
        raw = await self._post(payload)
        return self._parse_response(raw)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Yield chunks from Gemini response stream."""

        if self._client is None:
            raise ProviderError(
                "GeminiCliProvider requires httpx or an injected client",
                retryable=False,
            )

        response = await self.generate(request)
        for chunk in response_to_chunks(response):
            yield chunk

    async def aclose(self) -> None:
        """Close the client if owned."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()


__all__ = ["GeminiCliConfig", "GeminiCliProvider"]
