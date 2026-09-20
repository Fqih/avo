from __future__ import annotations

import json

import pytest

from avo.ollama_manager import OllamaManager, OllamaPullRefused


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload=None, lines=()):
        self._payload = payload
        self._lines = tuple(lines)

    def json(self):
        return self._payload

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeClient:
    def __init__(self):
        self.posts: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        if url.endswith("/api/tags"):
            return FakeResponse({"models": [{"name": "qwen2.5-coder:7b", "size": 4700}]})
        return FakeResponse({"version": "0.12.3"})

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs["json"]))
        return FakeResponse(
            lines=[
                json.dumps({"status": "pulling manifest"}),
                json.dumps({"status": "downloading", "completed": 50, "total": 100}),
                json.dumps({"status": "success"}),
            ]
        )


@pytest.mark.asyncio
async def test_manager_lists_models_and_checks_health() -> None:
    manager = OllamaManager("http://ollama.test", client=FakeClient())

    models = await manager.list_models()
    health = await manager.check_health()

    assert models[0].name == "qwen2.5-coder:7b"
    assert models[0].size_bytes == 4700
    assert health.available is True
    assert health.version == "0.12.3"


@pytest.mark.asyncio
async def test_pull_never_posts_when_confirmation_is_refused() -> None:
    client = FakeClient()
    manager = OllamaManager("http://ollama.test", client=client)

    with pytest.raises(OllamaPullRefused):
        await manager.pull("qwen2.5-coder:7b", confirm=lambda plan: False)

    assert client.posts == []


@pytest.mark.asyncio
async def test_pull_reports_progress_and_verifies_after_success() -> None:
    client = FakeClient()
    manager = OllamaManager("http://ollama.test", client=client)
    progress = []

    model = await manager.pull(
        "qwen2.5-coder:7b",
        confirm=lambda plan: True,
        output=progress.append,
    )

    assert model.name == "qwen2.5-coder:7b"
    assert len(progress) == 3
    assert progress[-1].status == "success"
    assert client.posts[0][1] == {"model": "qwen2.5-coder:7b", "stream": True}


def test_cloud_config_is_remote_and_keeps_api_key_explicit() -> None:
    from avo.ollama_manager import build_ollama_cloud_config

    config = build_ollama_cloud_config(model="qwen3-coder:480b-cloud", api_key="ollama-secret")
    assert config["AVO_PROVIDER"] == "ollama-cloud"
    assert config["AVO_OLLAMA_BASE_URL"] == "https://ollama.com"
    assert config["AVO_OLLAMA_API_KEY"] == "ollama-secret"
