from __future__ import annotations

import json

import pytest


class CloudResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self.payload


class CloudClient:
    def __init__(self, responses: dict[str, CloudResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.posts: list[str] = []

    async def get(self, url: str, **kwargs: object) -> CloudResponse:
        self.calls.append((url, dict(kwargs.get("headers", {}))))
        return self.responses[next(path for path in self.responses if url.endswith(path))]

    async def post(self, url: str, **kwargs: object) -> CloudResponse:
        self.posts.append(url)
        return CloudResponse({})


@pytest.mark.asyncio
async def test_cloud_manager_lists_models_with_bearer_auth() -> None:
    from avo.ollama_manager import OllamaCloudConfig, OllamaCloudManager

    client = CloudClient(
        {
            "/api/tags": CloudResponse({"models": [{"name": "qwen3-coder:480b-cloud"}]}),
            "/api/version": CloudResponse({"version": "cloud-1"}),
        }
    )
    manager = OllamaCloudManager(
        OllamaCloudConfig(model="qwen3-coder:480b-cloud", api_key="ollama-secret"),
        client=client,
    )

    models = await manager.list_models()
    health = await manager.check_health()

    assert models[0].name == "qwen3-coder:480b-cloud"
    assert health.version == "cloud-1"
    assert all(headers == {"Authorization": "Bearer ollama-secret"} for _, headers in client.calls)


@pytest.mark.asyncio
async def test_cloud_usage_is_optional_and_normalized() -> None:
    from avo.ollama_manager import OllamaCloudConfig, OllamaCloudManager

    client = CloudClient(
        {
            "/api/usage": CloudResponse(
                {"remaining_tokens": 900, "limit_tokens": 1000, "reset_at": "tomorrow"}
            )
        }
    )
    manager = OllamaCloudManager(
        OllamaCloudConfig(model="cloud", api_key="secret"),
        client=client,
    )

    usage = await manager.usage()

    assert usage is not None
    assert usage.remaining_tokens == 900
    assert usage.limit_tokens == 1000
    assert usage.reset_at == "tomorrow"


@pytest.mark.asyncio
async def test_cloud_usage_returns_none_when_endpoint_is_not_exposed() -> None:
    from avo.ollama_manager import OllamaCloudConfig, OllamaCloudManager

    client = CloudClient({"/api/usage": CloudResponse({"error": "not found"}, 404)})
    manager = OllamaCloudManager(
        OllamaCloudConfig(model="cloud", api_key="secret"),
        client=client,
    )

    assert await manager.usage() is None
