"""Gemini CLI provider for Google OAuth / Cloud Code PA subscription backend.

Targets ``POST https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse``
with Google Gemini content schema, bearer authentication, and Google genai SDK identity headers.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr

from avo import ModelRequest, ModelResponse, TokenUsage, ToolCall
from avo.auth import AuthError
from avo.exceptions import ProviderError
from avo.oauth.gate import require_subscription_allowed
from avo.oauth.refresh import ensure_fresh
from avo.oauth.registry import OAUTH
from avo.oauth.store import get_credential
from avo.providers.http_common import (
    _AsyncHTTPClient,
    build_openai_payload,
    parse_openai_response,
    redact_text,
)
from avo.providers.streaming import ModelChunk, response_to_chunks

try:  # pragma: no cover
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


_DEFAULT_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
_DEFAULT_MODEL = "gemini-2.5-pro"
_DEFAULT_CLIPROXYAPI_URL = "http://127.0.0.1:8317"


@dataclass(frozen=True)
class AntigravityModel:
    """One model exposed by the locally installed Antigravity CLI."""

    model_id: str
    label: str


def antigravity_executable() -> str:
    """Return the configured Antigravity executable name or path."""

    return os.environ.get("AVO_ANTIGRAVITY_BIN", "agy").strip() or "agy"


def _cliproxyapi_base_url() -> str:
    return (
        os.environ.get("AVO_CLIPROXYAPI_BASE_URL", "").strip()
        or os.environ.get("AVO_GEMINI_CLI_BASE_URL", "").strip()
        or _DEFAULT_CLIPROXYAPI_URL
    ).rstrip("/")


def _cliproxyapi_models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def discover_cliproxyapi_models(
    *, base_url: str | None = None, api_key: str | None = None, timeout_seconds: float = 10.0
) -> tuple[AntigravityModel, ...]:
    """Fetch the live model catalog exposed by a CLIProxyAPI server."""

    request = urllib.request.Request(_cliproxyapi_models_url(base_url or _cliproxyapi_base_url()))
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ProviderError(f"could not query CLIProxyAPI models: {redact_text(str(exc))}") from exc

    raw_models = payload.get("data", []) if isinstance(payload, dict) else []
    models: list[AntigravityModel] = []
    if isinstance(raw_models, list):
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("name") or "").strip()
            label = str(item.get("display_name") or item.get("displayName") or model_id).strip()
            if model_id:
                models.append(AntigravityModel(model_id=model_id, label=label))
    if not models:
        raise ProviderError("CLIProxyAPI returned no usable models")
    return tuple(models)


def _antigravity_error_detail(stdout: bytes | str, stderr: bytes | str) -> str:
    """Return a short, redacted CLI error without leaking credentials."""

    def _text(value: bytes | str) -> str:
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

    return redact_text((_text(stderr) or _text(stdout)).strip())


def discover_antigravity_models(*, timeout_seconds: float = 45.0) -> tuple[AntigravityModel, ...]:
    """Fetch the account's current model list from ``agy models``."""

    executable = antigravity_executable()
    try:
        completed = subprocess.run(
            [executable, "models"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProviderError(f"could not query Antigravity models: {exc}") from exc

    if completed.returncode != 0:
        detail = redact_text((completed.stderr or completed.stdout or "").strip())
        raise ProviderError(
            f"Antigravity model discovery failed ({completed.returncode}): "
            f"{detail or 'unknown error'}"
        )

    models: list[AntigravityModel] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        model_id, label = (part.strip() for part in line.split("\t", 1))
        if model_id and label and " " not in model_id:
            models.append(AntigravityModel(model_id=model_id, label=label))
    if not models:
        raise ProviderError("Antigravity returned no usable models")
    return tuple(models)


class GeminiCliConfig(BaseModel):
    """Endpoint and credential configuration for Gemini CLI subscription backend."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: str = _DEFAULT_MODEL
    base_url: str = _DEFAULT_BASE_URL
    auth_mode: Literal["oauth"] = "oauth"
    transport: Literal["http", "antigravity", "cliproxyapi"] = "http"
    cliproxyapi_base_url: str = _DEFAULT_CLIPROXYAPI_URL
    cliproxyapi_api_key: SecretStr | None = None

    @property
    def endpoint(self) -> str:
        """Return the absolute streamGenerateContent endpoint with alt=sse."""

        if self.transport == "cliproxyapi":
            base = self.cliproxyapi_base_url.rstrip("/")
            return (
                f"{base}/chat/completions"
                if base.endswith("/v1")
                else f"{base}/v1/chat/completions"
            )

        base = self.base_url.rstrip("/")
        if ":streamGenerateContent" in base:
            return base
        return f"{base}:streamGenerateContent?alt=sse"

    def request_headers(self, token: str | None = None) -> dict[str, str]:
        """Headers required by Gemini CLI endpoint."""

        if self.transport == "cliproxyapi":
            headers = {"Content-Type": "application/json"}
            if self.cliproxyapi_api_key:
                headers["Authorization"] = f"Bearer {self.cliproxyapi_api_key.get_secret_value()}"
            return headers

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

        model = environ.get("AVO_GEMINI_CLI_MODEL", "").strip() or (
            fallback_model or _DEFAULT_MODEL
        )
        raw_transport = environ.get("AVO_GEMINI_CLI_TRANSPORT", "http").strip().lower()
        if raw_transport == "cliproxyapi":
            transport: Literal["http", "antigravity", "cliproxyapi"] = "cliproxyapi"
        elif raw_transport == "antigravity":
            transport = "antigravity"
        else:
            transport = "http"
        if raw_transport == "http" and environ.get("AVO_CLIPROXYAPI_BASE_URL", "").strip():
            transport = "cliproxyapi"
        if transport in ("http", "antigravity"):
            require_subscription_allowed(environ)
        base_url = (environ.get("AVO_GEMINI_CLI_BASE_URL", "").strip() or _DEFAULT_BASE_URL).rstrip(
            "/"
        )
        cliproxyapi_base_url = (
            environ.get("AVO_CLIPROXYAPI_BASE_URL", "").strip() or _DEFAULT_CLIPROXYAPI_URL
        ).rstrip("/")
        cliproxyapi_api_key = (
            environ.get("AVO_CLIPROXYAPI_API_KEY", "").strip()
            or environ.get("AVO_GEMINI_CLI_API_KEY", "").strip()
            or None
        )

        stored = get_credential("gemini")
        if transport == "http" and stored is None:
            raise AuthError(
                "No stored credential found for gemini. Run 'avo login gemini' to log in."
            )

        return cls(
            model=model,
            base_url=base_url,
            transport=transport,
            cliproxyapi_base_url=cliproxyapi_base_url,
            cliproxyapi_api_key=cliproxyapi_api_key,
        )


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
        if self._config.transport == "cliproxyapi":
            return self._config.request_headers()
        token: str | None
        if self._token_provider is not None:
            token = await self._token_provider()
        else:
            cred = await ensure_fresh("gemini")
            token = cred.access_token
        return self._config.request_headers(token)

    def _build_antigravity_prompt(self, request: ModelRequest) -> str:
        """Flatten an Avo request into the prompt accepted by ``agy --print``."""

        sections: list[str] = []
        for message in request.messages:
            role = str(message.get("role") or "user").lower()
            content = message.get("content")
            if content is None:
                continue
            text = str(content).strip()
            if not text:
                continue
            if role == "system":
                sections.append(f"[System instructions]\n{text}")
            elif role == "assistant":
                sections.append(f"[Previous assistant response]\n{text}")
            else:
                sections.append(f"[User]\n{text}")

        if request.tools:
            tool_lines = [
                f"- {tool.name}: {tool.description}" for tool in request.tools if tool.name
            ]
            if tool_lines:
                sections.append(
                    "[Available Avo tools]\n"
                    + "\n".join(tool_lines)
                    + "\nUse your own Antigravity workspace tools when action is required."
                )

        return "\n\n".join(sections) or "Help me with the current workspace."

    @staticmethod
    def _extract_antigravity_text(value: Any) -> str | None:
        """Extract final text from the JSON shapes emitted by ``agy``."""

        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [GeminiCliProvider._extract_antigravity_text(item) for item in value]
            text = "".join(part for part in parts if part)
            return text or None
        if not isinstance(value, dict):
            return None

        for key in ("result", "response", "text", "content", "answer", "message"):
            if key in value:
                extracted = GeminiCliProvider._extract_antigravity_text(value[key])
                if extracted:
                    return extracted
        return None

    async def _generate_via_antigravity(self, request: ModelRequest) -> ModelResponse:
        """Use the installed Antigravity CLI for account auth, routing and tools."""

        command = (
            antigravity_executable(),
            "--model",
            self._config.model,
            "--print",
            self._build_antigravity_prompt(request),
            "--output-format",
            "json",
        )
        process: Any = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._request_timeout_seconds
            )
        except TimeoutError as exc:
            if process is not None:
                process.kill()
                await process.communicate()
            raise ProviderError(
                f"Antigravity request timed out after {self._request_timeout_seconds:.0f}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise ProviderError(
                f"Antigravity CLI is unavailable: {redact_text(str(exc))}",
                retryable=False,
            ) from exc

        if process.returncode != 0:
            detail = _antigravity_error_detail(stdout, stderr)
            retryable = process.returncode in {429, 500, 502, 503, 504}
            raise ProviderError(
                f"Antigravity request failed ({process.returncode}): {detail or 'unknown error'}",
                retryable=retryable,
            )

        raw_output = stdout.decode("utf-8", errors="replace").strip()
        if not raw_output:
            raise ProviderError("Antigravity returned an empty response", retryable=True)
        try:
            raw: Any = json.loads(raw_output)
        except json.JSONDecodeError:
            raw = raw_output

        text = self._extract_antigravity_text(raw)
        if not text:
            raise ProviderError("Antigravity returned no assistant text", retryable=False)
        return ModelResponse(content=text)

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
        if status == 401 and not is_retry and self._config.transport != "cliproxyapi":
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

        if self._config.transport == "antigravity":
            return await self._generate_via_antigravity(request)

        if self._config.transport == "cliproxyapi":
            if self._client is None:
                raise ProviderError(
                    "CLIProxyAPI transport requires httpx or an injected client",
                    retryable=False,
                )
            payload = build_openai_payload(
                self._config.model,
                request,
                self._max_completion_tokens,
            )
            raw = await self._post(payload)
            return parse_openai_response(raw)

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


__all__ = [
    "AntigravityModel",
    "GeminiCliConfig",
    "GeminiCliProvider",
    "antigravity_executable",
    "discover_antigravity_models",
    "discover_cliproxyapi_models",
]
