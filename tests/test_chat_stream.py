"""REPL-level streaming wiring tests (task 3).

The chat gate ``AVO_CHAT_STREAM`` (default on) wires a live printer into
the runtime's ``stream_callback``; the answer must appear exactly once in
the rendered output, and a mid-token stream failure must surface an
honest retry notice instead of duplicating the answer.
"""

from __future__ import annotations

import io
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from avo import ModelRequest, ModelResponse, TokenUsage
from avo.app_tools.file_tools import bind_workspace
from avo.chat import _run_turn, build_chat_context
from avo.exceptions import ProviderError
from avo.providers.fake import FakeProvider
from avo.providers.streaming import ModelChunk, response_to_chunks


class _FragmentingStreamProvider(FakeProvider):
    """FakeProvider streaming its scripted answer in small text deltas."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        response = await self.generate(request)
        for chunk in response_to_chunks(response):
            if chunk.text:
                text = chunk.text
                for start in range(0, len(text), 3):
                    yield ModelChunk(text=text[start : start + 3])
            else:
                yield chunk


class _FlakyStreamProvider(FakeProvider):
    """First stream dies mid-tokens with a retryable error, then succeeds."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        super().__init__(responses, repeat_last=True)
        self.stream_attempts = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        response = await self.generate(request)
        self.stream_attempts += 1
        if self.stream_attempts == 1:
            yield ModelChunk(text="partial")
            raise ProviderError("connection reset mid-stream", retryable=True)
        for chunk in response_to_chunks(response):
            yield chunk


@pytest.fixture
def chat_env(tmp_path: Path) -> dict[str, Path]:
    db = tmp_path / "chat.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("seed", encoding="utf-8")
    return {"db": db, "workspace": workspace}


def _environ(**overrides: str) -> dict[str, str]:
    env = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "fake-test-model",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }
    env.update(overrides)
    return env


def _final_response() -> ModelResponse:
    return ModelResponse(
        content="live streamed answer",
        usage=TokenUsage(input_tokens=2, output_tokens=3),
        response_id="sr-chat",
    )


async def _run_one_turn(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    provider: object,
    environ: dict[str, str],
) -> str:
    monkeypatch.setattr("os.environ", environ)
    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=environ,
    )
    ctx.runtime.provider = provider  # type: ignore[assignment]
    stdout = io.StringIO()
    stderr = io.StringIO()
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "hello", stdout, stderr)
    return stdout.getvalue() + stderr.getvalue()


@pytest.mark.asyncio
async def test_streaming_turn_prints_answer_exactly_once(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ()
    output = await _run_one_turn(
        chat_env, monkeypatch, _FragmentingStreamProvider([_final_response()]), env
    )

    assert output.count("live streamed answer") == 1
    assert "• live streamed answer" in output
    assert "Avo>" not in output
    assert "Avo [" not in output
    assert "run_id=" not in output


@pytest.mark.asyncio
async def test_gate_off_disables_stream_callback(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FragmentingStreamProvider([_final_response()])
    env = _environ(AVO_CHAT_STREAM="0")
    monkeypatch.setattr("os.environ", env)
    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    assert ctx.stream_enabled is False
    ctx.runtime.provider = provider  # type: ignore[assignment]

    stdout = io.StringIO()
    stderr = io.StringIO()
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "hello", stdout, stderr)

    output = stdout.getvalue()
    assert output.count("live streamed answer") == 1
    assert "• live streamed answer" in output
    assert "Avo>" not in output
    assert "Avo [" not in output
    assert "run_id=" not in output


@pytest.mark.asyncio
async def test_mid_stream_failure_shows_notice_and_single_final_answer(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FlakyStreamProvider([_final_response()])
    output = await _run_one_turn(chat_env, monkeypatch, provider, _environ())

    assert provider.stream_attempts == 2
    # The notice must stay truthful: the retry decision happens downstream
    # and may still fail, so it cannot promise "retrying".
    assert "⟲ stream interrupted\n" in output
    assert "retrying" not in output
    # The retried final answer appears exactly once (the partial text was
    # already on screen and cannot be unprinted).
    assert output.count("live streamed answer") == 1
    assert output.index("stream interrupted") < output.index("live streamed answer")
    assert "Avo [" not in output
    assert "run_id=" not in output


@pytest.mark.asyncio
async def test_non_streaming_turn_uses_compact_thought_and_footer(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = ModelResponse(content="<think>private reasoning</think>plain answer")
    output = await _run_one_turn(
        chat_env,
        monkeypatch,
        FakeProvider([response]),
        _environ(AVO_CHAT_STREAM="0"),
    )

    assert re.search(r"Thought for \d+s", output)
    assert "• plain answer" in output
    assert "💭 Thought process" not in output
    assert re.search(r"\* Cooked for \d+s · done \d{1,2}:\d{2}", output)
    assert "run_id=" not in output
