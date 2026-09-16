"""Codex Responses API provider for ChatGPT subscriptions.

Targets ``POST https://chatgpt.com/backend-api/codex/responses`` with
OpenAI Responses API streaming wire format, bearer authentication, and
originator identity headers.
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


_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
_DEFAULT_MODEL = "gpt-5.6-sol"


class CodexConfig(BaseModel):
    """Endpoint and credential configuration for ChatGPT Codex subscription backend."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: str = _DEFAULT_MODEL
    base_url: str = _DEFAULT_BASE_URL
    auth_mode: Literal["oauth"] = "oauth"

    @property
    def endpoint(self) -> str:
        """Return the absolute Responses API endpoint."""

        base = self.base_url.rstrip("/")
        if base.endswith("/responses"):
            return base
        return f"{base}/responses"

    def request_headers(self, token: str | None = None) -> dict[str, str]:
        """Headers required by Codex subscription endpoint."""

        hdrs: dict[str, str] = {
            "Authorization": f"Bearer {token or ''}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        entry = OAUTH.get("codex")
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
    ) -> CodexConfig:
        """Build CodexConfig verifying subscription opt-in and stored credential."""

        require_subscription_allowed(environ)
        model = environ.get("AVO_CODEX_MODEL", "").strip() or (fallback_model or _DEFAULT_MODEL)
        base_url = (environ.get("AVO_CODEX_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip("/")

        stored = get_credential("codex")
        if stored is None:
            raise AuthError(
                "No stored credential found for codex. Run 'avo login codex' to log in."
            )

        return cls(model=model, base_url=base_url)


class CodexProvider:
    """An async ``ModelProvider`` for ChatGPT subscription via Codex Responses API."""

    name = "codex"

    def __init__(
        self,
        config: CodexConfig,
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
            cred = await ensure_fresh("codex")
            token = cred.access_token
        return self._config.request_headers(token)

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for message in request.messages:
            role = message.get("role")
            if role == "system":
                items.append({"role": "system", "content": message.get("content") or ""})
            elif role == "user":
                items.append({"role": "user", "content": message.get("content") or ""})
            elif role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        call_id = str(tc.get("id") or tc.get("tool_call_id") or "")
                        name = str(tc.get("name") or "")
                        arguments = tc.get("arguments", {})
                        arg_str = (
                            json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
                        )
                        items.append(
                            {
                                "type": "function_call",
                                "call_id": call_id,
                                "name": name,
                                "arguments": arg_str,
                            }
                        )
                else:
                    items.append({"role": "assistant", "content": message.get("content") or ""})
            elif role == "tool":
                call_id = str(message.get("tool_call_id") or message.get("id") or "")
                content = message.get("content", "")
                output_str = (
                    json.dumps(content) if isinstance(content, (dict, list)) else str(content)
                )
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output_str,
                    }
                )

        payload: dict[str, Any] = {
            "model": self._config.model,
            "store": False,
            "stream": True,
            "input": items,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
                for tool in request.tools
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
                f"Codex transport failure: {redact_text(str(exc))}",
                retryable=True,
            ) from exc

        status = int(response.status_code)
        if status == 401 and not is_retry:
            if self._token_provider is None:
                await ensure_fresh("codex", force=True)
            return await self._post(payload, is_retry=True)

        if status >= 400:
            detail = redact_text(str(getattr(response, "text", "")))
            raise ProviderError(
                f"Codex request failed with status {status}: {detail}",
                retryable=status == 429 or status >= 500,
            )

        text = str(getattr(response, "text", ""))
        items: list[dict[str, Any]] = []
        completed_response: dict[str, Any] = {}
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
                        event_type = obj.get("type")
                        if event_type == "response.output_item.done":
                            item = obj.get("item")
                            if isinstance(item, dict):
                                items.append(item)
                        elif event_type == "response.completed":
                            resp = obj.get("response")
                            if isinstance(resp, dict):
                                completed_response = resp
                except Exception:
                    pass

        if has_sse:
            if completed_response:
                if not completed_response.get("output"):
                    completed_response["output"] = items
                return completed_response
            if items:
                return {"output": items}

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProviderError(
                f"Codex returned an unparsable response body: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

    def _parse_response(self, raw: Any) -> ModelResponse:
        if not isinstance(raw, dict):
            raise ProviderError("Invalid Codex response", retryable=False)

        resp_obj = raw.get("response") if raw.get("type") == "response.completed" else raw
        if not isinstance(resp_obj, dict):
            resp_obj = raw

        resp_id = resp_obj.get("id")
        id_kwargs: dict[str, str] = {}
        if isinstance(resp_id, str) and resp_id:
            id_kwargs["response_id"] = resp_id

        raw_usage = resp_obj.get("usage")
        usage: TokenUsage | None = None
        if isinstance(raw_usage, dict):
            in_tok = raw_usage.get("input_tokens")
            out_tok = raw_usage.get("output_tokens")
            if isinstance(in_tok, int) and isinstance(out_tok, int):
                usage = TokenUsage(input_tokens=in_tok, output_tokens=out_tok)

        output = resp_obj.get("output")
        if not isinstance(output, list) or not output:
            raise ProviderError("codex returned no output", retryable=False)

        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                raw_args = item.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception as exc:
                    raise ProviderError(
                        f"Invalid function_call arguments: {exc}", retryable=False
                    ) from exc
                tool_call = ToolCall(
                    tool_call_id=str(item.get("call_id", "")),
                    name=str(item.get("name", "")),
                    arguments=args if isinstance(args, dict) else {},
                )
                return ModelResponse(tool_call=tool_call, usage=usage, **id_kwargs)

        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                contents = item.get("content", [])
                if isinstance(contents, list):
                    for c in contents:
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            text_parts.append(str(c.get("text", "")))
                        elif isinstance(c, str):
                            text_parts.append(c)
                elif isinstance(contents, str):
                    text_parts.append(contents)
            elif item.get("type") == "output_text":
                text_parts.append(str(item.get("text", "")))

        if not text_parts:
            raise ProviderError("codex returned no output", retryable=False)

        return ModelResponse(content="".join(text_parts), usage=usage, **id_kwargs)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one final answer or tool call via Codex."""

        if self._client is None:
            raise ProviderError(
                "CodexProvider requires httpx or an injected client",
                retryable=False,
            )

        payload = self._build_payload(request)
        raw = await self._post(payload)
        return self._parse_response(raw)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Yield chunks from the Codex response stream."""

        if self._client is None:
            raise ProviderError(
                "CodexProvider requires httpx or an injected client",
                retryable=False,
            )

        response = await self.generate(request)
        for chunk in response_to_chunks(response):
            yield chunk

    async def aclose(self) -> None:
        """Close the client if owned."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()


__all__ = ["CodexConfig", "CodexProvider"]
