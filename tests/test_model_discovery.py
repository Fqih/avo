from __future__ import annotations

from pathlib import Path

import pytest


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def json(self) -> object:
        return self.payload


class FakeClient:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, dict(kwargs.get("headers", {}))))
        response = FakeResponse(self.payload)
        response.status_code = self.status_code
        return response


@pytest.mark.asyncio
async def test_openai_compatible_discovery_normalizes_live_ids() -> None:
    from avo.model_discovery import OpenAICompatibleDiscovery

    client = FakeClient({"data": [{"id": "gpt-5"}, {"id": "gpt-5"}, {"id": ""}]})
    discovery = OpenAICompatibleDiscovery(
        provider="openai",
        base_url="https://api.openai.test/v1",
        api_key="secret-key",
        client=client,
    )

    assert await discovery.list_models() == ("gpt-5",)
    assert client.calls == [
        (
            "https://api.openai.test/v1/models",
            {"Authorization": "Bearer secret-key"},
        )
    ]


@pytest.mark.asyncio
async def test_discovery_snapshot_includes_provider_metadata(tmp_path: Path) -> None:
    from avo.model_catalog_service import CatalogSource, ModelCatalogCache
    from avo.model_discovery import discover_provider_models

    result = await discover_provider_models(
        "codex",
        {"AVO_CLIPROXYAPI_BASE_URL": "https://proxy.test/v1"},
        cache=ModelCatalogCache(tmp_path),
        client=FakeClient({"data": [{"id": "gpt-5.6-sol"}]}),
    )

    assert result.source is CatalogSource.LIVE
    assert result.models[0].auth_requirement == "oauth"
    assert result.models[0].transport == "openai-compatible"
    assert "text" in result.models[0].capabilities


@pytest.mark.asyncio
async def test_ollama_discovery_uses_tags_endpoint_without_auth_for_local() -> None:
    from avo.model_discovery import OllamaDiscovery

    client = FakeClient({"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.2:3b"}]})
    discovery = OllamaDiscovery(base_url="http://127.0.0.1:11434", client=client)

    assert await discovery.list_models() == ("llama3.2:3b", "qwen2.5:7b")
    assert client.calls[0][0].endswith("/api/tags")
    assert client.calls[0][1] == {}


@pytest.mark.asyncio
async def test_gemini_cli_discovery_prefers_configured_cliproxyapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import avo.providers.gemini_cli as gemini_cli
    from avo.model_discovery import GeminiCliDiscovery

    monkeypatch.setattr(
        gemini_cli,
        "discover_cliproxyapi_models",
        lambda **kwargs: (gemini_cli.AntigravityModel("live-gemini", "Live Gemini"),),
    )
    monkeypatch.setattr(
        gemini_cli,
        "discover_antigravity_models",
        lambda **kwargs: pytest.fail("agy should not be used when CLIProxyAPI is configured"),
    )

    discovery = GeminiCliDiscovery(
        environ={
            "AVO_CLIPROXYAPI_BASE_URL": "http://127.0.0.1:8317",
            "AVO_CLIPROXYAPI_API_KEY": "local-key",
        }
    )

    assert await discovery.list_models() == ("live-gemini",)


@pytest.mark.asyncio
async def test_discovery_uses_fresh_cache_when_live_request_fails(tmp_path: Path) -> None:
    from avo.model_catalog_service import CatalogSource, ModelCatalogCache, catalog_entries
    from avo.model_discovery import discover_provider_models

    cache = ModelCatalogCache(tmp_path)
    cached = catalog_entries("openai", ("gpt-cached",), source=CatalogSource.LIVE)
    from avo.model_catalog_service import ModelCatalogResult

    cache.save(ModelCatalogResult(provider="openai", models=cached, source=CatalogSource.LIVE))

    result = await discover_provider_models(
        "openai",
        {"OPENAI_API_KEY": "secret", "OPENAI_BASE_URL": "https://invalid.test/v1"},
        cache=cache,
        client=FakeClient({}, status_code=503),
        static_models=("gpt-static",),
    )

    assert result.source is CatalogSource.CACHE
    assert result.models[0].model_id == "gpt-cached"
    assert result.warning is not None


@pytest.mark.asyncio
async def test_discovery_marks_static_fallback_when_no_live_or_cache(tmp_path: Path) -> None:
    from avo.model_catalog_service import CatalogSource, ModelCatalogCache
    from avo.model_discovery import discover_provider_models

    result = await discover_provider_models(
        "anthropic-api",
        {},
        cache=ModelCatalogCache(tmp_path),
        static_models=("claude-sonnet-4-6",),
        client=FakeClient({}, status_code=401),
    )

    assert result.source is CatalogSource.STATIC
    assert result.models[0].model_id == "claude-sonnet-4-6"
    assert result.warning is not None
