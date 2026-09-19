"""Live provider model discovery with cache and honest fallbacks."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from avo.exceptions import ProviderError
from avo.model_catalog_service import (
    CatalogSource,
    ModelCatalogCache,
    ModelCatalogResult,
    catalog_entries,
)
from avo.providers.http_common import redact_text


class _Response(Protocol):
    status_code: int
    text: str

    def json(self) -> object: ...


class ModelDiscoveryClient(Protocol):
    """Small HTTP surface needed by model-list adapters."""

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> _Response: ...


class _UrllibResponse:
    def __init__(self, status_code: int, payload: object, text: str) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self) -> object:
        return self.payload


class _UrllibClient:
    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> _UrllibResponse:
        return await asyncio.to_thread(
            _sync_get_json,
            url,
            headers or {},
            timeout or 10.0,
        )


def _sync_get_json(url: str, headers: dict[str, str], timeout: float) -> _UrllibResponse:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            raw = response.read().decode("utf-8", errors="replace")
            return _UrllibResponse(int(response.status), json.loads(raw), raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProviderError(
            f"model discovery failed with HTTP {exc.code}: {redact_text(detail)[:500]}"
        ) from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ProviderError(f"model discovery transport failure: {redact_text(str(exc))}") from exc


async def _response_json(response: _Response) -> object:
    status = int(response.status_code)
    if status >= 400:
        raise ProviderError(
            f"model discovery failed with HTTP {status}: {redact_text(response.text)[:500]}"
        )
    value = response.json()
    return await value if inspect.isawaitable(value) else value


class _BaseDiscovery:
    def __init__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str] | None = None,
        client: ModelDiscoveryClient | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.headers = headers or {}
        self.client = client or _UrllibClient()

    async def _payload(self) -> object:
        response = await self.client.get(self.endpoint, headers=self.headers, timeout=10.0)
        return await _response_json(response)


class OpenAICompatibleDiscovery(_BaseDiscovery):
    """Discover models from an OpenAI-compatible ``/models`` endpoint."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str | None = None,
        client: ModelDiscoveryClient | None = None,
    ) -> None:
        del provider
        base = base_url.rstrip("/")
        endpoint = base if base.endswith("/models") else f"{base}/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        super().__init__(endpoint=endpoint, headers=headers, client=client)

    async def list_models(self) -> tuple[str, ...]:
        payload = await self._payload()
        raw = payload.get("data", []) if isinstance(payload, dict) else []
        values: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    value = item.get("id") or item.get("name")
                    if isinstance(value, str):
                        values.append(value)
        if not values:
            raise ProviderError("model discovery returned no usable models")
        return tuple(sorted(set(value.strip() for value in values if value.strip())))


class OllamaDiscovery(_BaseDiscovery):
    """Discover local or cloud Ollama models from ``/api/tags``."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        client: ModelDiscoveryClient | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        super().__init__(
            endpoint=f"{base_url.rstrip('/')}/api/tags", headers=headers, client=client
        )

    async def list_models(self) -> tuple[str, ...]:
        payload = await self._payload()
        raw = payload.get("models", []) if isinstance(payload, dict) else []
        values = (
            [item.get("name") for item in raw if isinstance(item, dict)]
            if isinstance(raw, list)
            else []
        )
        models = sorted(
            {value.strip() for value in values if isinstance(value, str) and value.strip()}
        )
        if not models:
            raise ProviderError("Ollama model discovery returned no usable models")
        return tuple(models)


class AnthropicDiscovery(_BaseDiscovery):
    """Discover models from Anthropic's ``/v1/models`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        client: ModelDiscoveryClient | None = None,
    ) -> None:
        base = base_url.rstrip("/")
        endpoint = base if base.endswith("/models") else f"{base}/models"
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        super().__init__(endpoint=endpoint, headers=headers, client=client)

    async def list_models(self) -> tuple[str, ...]:
        payload = await self._payload()
        raw = payload.get("data", []) if isinstance(payload, dict) else []
        values = (
            [item.get("id") for item in raw if isinstance(item, dict)]
            if isinstance(raw, list)
            else []
        )
        models = sorted(
            {value.strip() for value in values if isinstance(value, str) and value.strip()}
        )
        if not models:
            raise ProviderError("Anthropic model discovery returned no usable models")
        return tuple(models)


class GeminiApiDiscovery(_BaseDiscovery):
    """Discover Gemini API models through the Google model-list endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        api_key: str | None = None,
        client: ModelDiscoveryClient | None = None,
    ) -> None:
        headers = {"x-goog-api-key": api_key} if api_key else {}
        super().__init__(endpoint=f"{base_url.rstrip('/')}/models", headers=headers, client=client)

    async def list_models(self) -> tuple[str, ...]:
        payload = await self._payload()
        raw = payload.get("models", []) if isinstance(payload, dict) else []
        values: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                value = item.get("name")
                if isinstance(value, str):
                    values.append(value.removeprefix("models/"))
        models = sorted({value.strip() for value in values if value.strip()})
        if not models:
            raise ProviderError("Gemini model discovery returned no usable models")
        return tuple(models)


class GeminiCliDiscovery:
    """Discover account-scoped Gemini models through Antigravity ``agy``."""

    def __init__(self, *, environ: Mapping[str, str] | None = None) -> None:
        self.environ = environ or {}

    async def list_models(self) -> tuple[str, ...]:
        from avo.providers.gemini_cli import (
            discover_antigravity_models,
            discover_cliproxyapi_models,
        )

        configured_proxy = (
            self.environ.get("AVO_CLIPROXYAPI_BASE_URL", "").strip()
            or self.environ.get("AVO_GEMINI_CLI_BASE_URL", "").strip()
        )
        api_key = (
            self.environ.get("AVO_CLIPROXYAPI_API_KEY", "").strip()
            or self.environ.get("AVO_GEMINI_CLI_API_KEY", "").strip()
            or None
        )
        if configured_proxy:
            models = await asyncio.to_thread(
                discover_cliproxyapi_models,
                base_url=configured_proxy,
                api_key=api_key,
            )
            return tuple(item.model_id for item in models)

        try:
            models = await asyncio.to_thread(discover_antigravity_models)
            return tuple(item.model_id for item in models)
        except ProviderError as agy_error:
            try:
                models = await asyncio.to_thread(
                    discover_cliproxyapi_models,
                    api_key=api_key,
                )
            except ProviderError as proxy_error:
                raise ProviderError(
                    f"Antigravity and CLIProxyAPI model discovery failed: "
                    f"{redact_text(str(agy_error))}; {redact_text(str(proxy_error))}"
                ) from proxy_error
            return tuple(item.model_id for item in models)


@dataclass(frozen=True)
class _CatalogMetadata:
    capabilities: tuple[str, ...]
    auth_requirement: str
    transport: str


def _catalog_metadata(provider: str) -> _CatalogMetadata:
    """Return non-secret capability metadata for one provider family."""

    provider_key = provider.strip().lower()
    if provider_key == "ollama":
        return _CatalogMetadata(("text", "tools"), "none", "ollama")
    if provider_key == "ollama-cloud":
        return _CatalogMetadata(("text", "tools"), "api-key", "ollama")
    if provider_key in {"codex", "gemini-cli", "gemini_cli"}:
        transport = "openai-compatible" if provider_key == "codex" else "antigravity"
        return _CatalogMetadata(("text", "tools"), "oauth", transport)
    if provider_key in {"anthropic", "anthropic-api", "claude"}:
        return _CatalogMetadata(("text", "tools", "vision"), "api-key", "anthropic-messages")
    return _CatalogMetadata(("text", "tools"), "api-key", "openai-compatible")


def _cache_root(environ: Mapping[str, str]) -> os.PathLike[str]:
    configured = environ.get("AVO_MODEL_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    config_dir = environ.get("AVO_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir).expanduser() / "model-catalog"
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path("~/.config").expanduser()
    return base / "avo" / "model-catalog"


def _credential_from_env(environ: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = environ.get(name, "").strip()
        if value:
            return value
    return None


def _discovery_for(
    provider: str,
    environ: Mapping[str, str],
    *,
    client: ModelDiscoveryClient | None,
) -> Any:
    provider_key = provider.strip().lower()
    if provider_key == "ollama":
        return OllamaDiscovery(
            base_url=environ.get("AVO_OLLAMA_BASE_URL", "http://localhost:11434"),
            api_key=_credential_from_env(environ, "AVO_OLLAMA_API_KEY"),
            client=client,
        )
    if provider_key == "ollama-cloud":
        return OllamaDiscovery(
            base_url=environ.get("AVO_OLLAMA_BASE_URL", "https://ollama.com"),
            api_key=_credential_from_env(environ, "AVO_OLLAMA_API_KEY"),
            client=client,
        )
    if provider_key in {"openai", "openrouter", "groq", "cerebras", "codex"}:
        defaults = {
            "openai": "https://api.openai.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "groq": "https://api.groq.com/openai/v1",
            "cerebras": "https://api.cerebras.ai/v1",
            "codex": environ.get("AVO_CLIPROXYAPI_BASE_URL", ""),
        }
        base = environ.get("AVO_OPENAI_BASE_URL", "").strip() or defaults[provider_key]
        if not base:
            raise ProviderError("Codex model discovery requires CLIProxyAPI base URL")
        key = _credential_from_env(environ, "AVO_OPENAI_API_KEY", "AVO_CLIPROXYAPI_API_KEY")
        return OpenAICompatibleDiscovery(
            provider=provider_key, base_url=base, api_key=key, client=client
        )
    if provider_key in {"anthropic", "anthropic-api", "claude"}:
        return AnthropicDiscovery(
            base_url=environ.get("AVO_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
            api_key=_credential_from_env(environ, "AVO_ANTHROPIC_API_KEY"),
            client=client,
        )
    if provider_key == "gemini-api":
        return GeminiApiDiscovery(
            base_url=environ.get(
                "AVO_GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
            ),
            api_key=_credential_from_env(environ, "AVO_GEMINI_API_KEY"),
            client=client,
        )
    if provider_key in {"gemini-cli", "gemini_cli"}:
        return GeminiCliDiscovery(environ=environ)
    raise ProviderError(f"provider {provider_key!r} has no live model discovery adapter")


async def discover_provider_models(
    provider: str,
    environ: Mapping[str, str],
    *,
    cache: ModelCatalogCache | None = None,
    client: ModelDiscoveryClient | None = None,
    static_models: Sequence[str] = (),
) -> ModelCatalogResult:
    """Discover models with live, cache, then explicitly-labelled static fallback."""

    provider_key = provider.strip().lower()
    metadata = _catalog_metadata(provider_key)
    catalog_cache = cache or ModelCatalogCache(Path(_cache_root(environ)))
    try:
        discovery = _discovery_for(provider_key, environ, client=client)
        model_ids = await discovery.list_models()
        entries = catalog_entries(
            provider_key,
            model_ids,
            source=CatalogSource.LIVE,
            recommended=model_ids[0] if model_ids else None,
            capabilities=metadata.capabilities,
            auth_requirement=metadata.auth_requirement,
            transport=metadata.transport,
        )
        result = ModelCatalogResult(
            provider=provider_key,
            models=entries,
            source=CatalogSource.LIVE,
            fetched_at=datetime.now(UTC),
        )
        catalog_cache.save(result)
        return result
    except Exception as exc:
        warning = redact_text(str(exc))[:500]
        cached = catalog_cache.load(provider_key)
        if cached is not None:
            return cached.model_copy(update={"warning": f"live discovery unavailable: {warning}"})

    entries = catalog_entries(
        provider_key,
        tuple(static_models),
        source=CatalogSource.STATIC,
        recommended=next(iter(static_models), None),
        capabilities=metadata.capabilities,
        auth_requirement=metadata.auth_requirement,
        transport=metadata.transport,
    )
    return ModelCatalogResult(
        provider=provider_key,
        models=entries,
        source=CatalogSource.STATIC,
        warning=f"live discovery unavailable: {warning}",
    )


__all__ = [
    "AnthropicDiscovery",
    "GeminiApiDiscovery",
    "GeminiCliDiscovery",
    "ModelDiscoveryClient",
    "OllamaDiscovery",
    "OpenAICompatibleDiscovery",
    "discover_provider_models",
]
