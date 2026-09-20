from __future__ import annotations

from typing import ClassVar

from avo import cli_models
from avo.ollama_manager import OllamaHealth, OllamaModel, PullProgress


class FakeManager:
    instances: ClassVar[list[FakeManager]] = []

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.pulled = []
        FakeManager.instances.append(self)

    async def check_health(self):
        return OllamaHealth(True, "0.12.3")

    async def list_models(self):
        return (OllamaModel("qwen2.5-coder:7b", 4_700_000_000),)

    async def pull(self, model, *, confirm, output):
        self.pulled.append(model)
        plan = type(
            "Plan",
            (),
            {"model": model, "download_bytes": 100, "already_installed": False},
        )()
        assert await confirm(plan)
        output(PullProgress(model=model, status="success"))
        return OllamaModel(model, 100)


def test_models_list_shows_local_models_and_health(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_models, "OllamaManager", FakeManager)

    assert cli_models.main(["ollama", "list"]) == 0

    output = capsys.readouterr().out
    assert "Ollama Local" in output
    assert "qwen2.5-coder:7b" in output
    assert "0.12.3" in output


def test_models_pull_requires_explicit_confirmation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_models, "OllamaManager", FakeManager)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    assert cli_models.main(["ollama", "pull", "qwen2.5-coder:7b"]) == 0

    assert FakeManager.instances[-1].pulled == ["qwen2.5-coder:7b"]
    assert "success" in capsys.readouterr().out


def test_models_cloud_is_not_presented_as_local_download(capsys) -> None:
    assert cli_models.main(["ollama", "cloud"]) == 0
    output = capsys.readouterr().out.lower()
    assert "remote" in output
    assert "pull" not in output
