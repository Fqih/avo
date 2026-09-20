"""Tests for local Ollama auto-detection in first-run onboarding setup."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from avo.chat_setup import interactive_first_run_setup, probe_local_ollama


def test_probe_local_ollama_returns_models_when_server_up() -> None:
    mock_response_data = b'{"models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.2:latest"}]}'

    class MockResponse:
        def __init__(self) -> None:
            self.status = 200

        def read(self) -> bytes:
            return mock_response_data

        def __enter__(self) -> MockResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    with patch("urllib.request.urlopen", return_value=MockResponse()):
        models = probe_local_ollama()
        assert models == ["qwen2.5-coder:7b", "llama3.2:latest"]


def test_probe_local_ollama_returns_empty_when_server_down() -> None:
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        models = probe_local_ollama()
        assert models == []


def test_first_run_auto_detects_ollama_and_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "avo.chat_setup.probe_local_ollama",
        lambda: ["qwen2.5-coder:7b", "llama3.2:latest"],
    )

    stdin = io.StringIO("\n")  # User hits Enter to accept
    stdout = io.StringIO()

    env = interactive_first_run_setup(stdin, stdout, auto_detect_local=True)

    assert env is not None
    assert env["AVO_PROVIDER"] == "ollama"
    assert env["AVO_MODEL"] == "qwen2.5-coder:7b"
    assert "Local Ollama detected" in stdout.getvalue()
