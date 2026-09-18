from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from avo import ModelRequest
from avo.auth import AuthError
from avo.exceptions import ProviderError
from avo.oauth.store import Credential, store_credential
from avo.providers.gemini_cli import GeminiCliConfig, GeminiCliProvider


class _Resp:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._p = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._p

    @property
    def text(self) -> str:
        return json.dumps(self._p)


class FakeClient:
    def __init__(self, replies: list[Any] | Any) -> None:
        if isinstance(replies, list):
            self.replies = list(replies)
        else:
            self.replies = [replies]
        self.requests: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.requests.append({"url": url, "headers": headers, "body": json, "timeout": timeout})
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, _Resp):
            return reply
        return _Resp(reply)


DONE_TEXT = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"text": "halo"}],
            }
        }
    ],
    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
}

DONE_CALL = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"functionCall": {"id": "fc1", "name": "add", "args": {"a": 1, "b": 1}}}],
            }
        }
    ]
}


async def _token() -> str:
    return "at-9"


def _req(msgs: list[dict[str, Any]] | None = None) -> ModelRequest:
    return ModelRequest(
        run_id="r1",
        step=1,
        messages=msgs or [{"role": "user", "content": "hi"}],
    )


@pytest.fixture
def store_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_bearer_and_url_and_parse_text() -> None:
    client = FakeClient(DONE_TEXT)
    p = GeminiCliProvider(
        GeminiCliConfig(model="gemini-2.5-pro"),
        token_provider=_token,
        client=client,
    )
    res = await p.generate(_req())
    assert res.content == "halo"
    assert res.usage is not None
    assert res.usage.input_tokens == 7
    assert res.usage.output_tokens == 2
    sent = client.requests[0]
    assert "v1internal:streamGenerateContent" in sent["url"] and "alt=sse" in sent["url"]
    assert sent["headers"]["Authorization"] == "Bearer at-9"
    assert sent["headers"]["x-goog-api-client"].startswith("google-genai-sdk")
    assert sent["body"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


@pytest.mark.asyncio
async def test_parses_function_call() -> None:
    p = GeminiCliProvider(
        GeminiCliConfig(model="m"),
        token_provider=_token,
        client=FakeClient(DONE_CALL),
    )
    res = await p.generate(_req())
    assert res.tool_call is not None
    assert res.tool_call.name == "add"
    assert res.tool_call.arguments == {"a": 1, "b": 1}


@pytest.mark.asyncio
async def test_tool_messages_map_to_function_response() -> None:
    client = FakeClient(DONE_TEXT)
    p = GeminiCliProvider(GeminiCliConfig(model="m"), token_provider=_token, client=client)
    await p.generate(
        _req(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "fc1", "name": "add", "arguments": {"a": 1, "b": 1}}],
                },
                {"role": "tool", "tool_call_id": "fc1", "content": "2"},
            ]
        )
    )
    contents = client.requests[0]["body"]["contents"]
    assert contents[-1]["parts"][0]["functionResponse"] == {
        "name": "add",
        "response": {"result": "2"},
    }


@pytest.mark.asyncio
async def test_empty_candidates_raises() -> None:
    p = GeminiCliProvider(
        GeminiCliConfig(model="m"),
        token_provider=_token,
        client=FakeClient({"candidates": []}),
    )
    with pytest.raises(ProviderError, match="Invalid Gemini response"):
        await p.generate(_req())


def test_from_avo_env_when_disabled_raises(store_dir: None) -> None:
    store_credential(
        Credential(
            provider="gemini",
            kind="oauth",
            access_token="tok",
            subscription=True,
        )
    )
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        GeminiCliConfig.from_avo_env(
            {"AVO_ALLOW_SUBSCRIPTION": "0"}, fallback_model="gemini-2.5-pro"
        )


def test_from_avo_env_missing_credential_raises(store_dir: None) -> None:
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        GeminiCliConfig.from_avo_env(
            {"AVO_ALLOW_SUBSCRIPTION": "0"}, fallback_model="gemini-2.5-pro"
        )

    with pytest.raises(AuthError, match="No stored credential"):
        GeminiCliConfig.from_avo_env(
            {"AVO_ALLOW_SUBSCRIPTION": "1"}, fallback_model="gemini-2.5-pro"
        )


def test_from_avo_env_with_valid_credential_succeeds(store_dir: None) -> None:
    store_credential(
        Credential(
            provider="gemini",
            kind="oauth",
            access_token="tok",
            subscription=True,
        )
    )
    config = GeminiCliConfig.from_avo_env(
        {"AVO_ALLOW_SUBSCRIPTION": "1"}, fallback_model="gemini-2.5-pro"
    )
    assert config.model == "gemini-2.5-pro"
    assert (
        config.endpoint
        == "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
    )


@pytest.mark.asyncio
async def test_gemini_cli_provider_401_reactive_refresh(
    store_dir: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    store_credential(
        Credential(
            provider="gemini",
            kind="oauth",
            access_token="old-token",
            refresh_token="valid-refresh",
            obtained_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=1),  # lead is 300s, so 1 hr avoids proactive
            subscription=True,
        )
    )
    monkeypatch.setattr(
        "avo.oauth.flows.request_token",
        lambda entry, form: {"access_token": "new-token", "expires_in": 3600},
    )

    client = FakeClient(
        [
            _Resp({"error": "unauthorized"}, status_code=401),
            _Resp(DONE_TEXT, status_code=200),
        ]
    )
    provider = GeminiCliProvider(GeminiCliConfig(model="gemini-2.5-pro"), client=client)
    res = await provider.generate(_req())
    assert res.content == "halo"
    assert len(client.requests) == 2
    assert client.requests[0]["headers"]["Authorization"] == "Bearer old-token"
    assert client.requests[1]["headers"]["Authorization"] == "Bearer new-token"


@pytest.mark.asyncio
async def test_gemini_cli_provider_stream_fallback() -> None:
    client = FakeClient(DONE_TEXT)
    provider = GeminiCliProvider(
        GeminiCliConfig(model="gemini-2.5-pro"),
        token_provider=_token,
        client=client,
    )
    chunks = [chunk async for chunk in provider.stream(_req())]
    assert len(chunks) > 0
    full_text = "".join(c.text for c in chunks if c.text)
    assert full_text == "halo"


def test_discovers_models_from_antigravity_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.providers import gemini_cli

    class Result:
        returncode = 0
        stdout = (
            "Fetching available models...\n"
            "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
            "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
        )
        stderr = ""

    monkeypatch.setattr(gemini_cli.subprocess, "run", lambda *args, **kwargs: Result())

    models = gemini_cli.discover_antigravity_models()

    assert [(item.model_id, item.label) for item in models] == [
        ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)"),
    ]


def test_discovers_models_from_cliproxyapi(monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.providers import gemini_cli

    class Response:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": [
                        {"id": "gemini-3.8-flash-high", "display_name": "Gemini 3.8 Flash"},
                        {"id": "claude-sonnet-4-6", "displayName": "Claude Sonnet 4.6"},
                    ]
                }
            ).encode()

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(gemini_cli.urllib.request, "urlopen", lambda *args, **kwargs: Response())

    models = gemini_cli.discover_cliproxyapi_models(base_url="http://127.0.0.1:8317")

    assert [item.model_id for item in models] == ["gemini-3.8-flash-high", "claude-sonnet-4-6"]


@pytest.mark.asyncio
async def test_antigravity_transport_uses_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.providers import gemini_cli

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b'{"result":"pong"}', b""

        def kill(self) -> None:
            pass

    calls: list[tuple[str, ...]] = []

    async def fake_create(*args: str, **kwargs: Any) -> Process:
        calls.append(args)
        return Process()

    monkeypatch.setattr(gemini_cli.asyncio, "create_subprocess_exec", fake_create)
    provider = GeminiCliProvider(
        GeminiCliConfig(model="gemini-3.8-flash-high", transport="antigravity"),
        client=FakeClient(DONE_TEXT),
    )

    response = await provider.generate(_req())

    assert response.content == "pong"
    assert calls
    assert "agy" in calls[0]
    assert "--model" in calls[0]
    assert "gemini-3.8-flash-high" in calls[0]


@pytest.mark.asyncio
async def test_cliproxyapi_transport_uses_openai_compatible_endpoint() -> None:
    provider = GeminiCliProvider(
        GeminiCliConfig(
            model="gemini-3.8-flash-high",
            transport="cliproxyapi",
            cliproxyapi_base_url="http://127.0.0.1:8317",
            cliproxyapi_api_key="local-key",
        ),
        client=FakeClient({"choices": [{"message": {"role": "assistant", "content": "pong"}}]}),
    )

    response = await provider.generate(_req())

    assert response.content == "pong"


@pytest.mark.asyncio
async def test_gemini_cli_provider_parses_sse_stream() -> None:
    chunk1 = json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "halo from "}], "role": "model"}}]}
    )
    chunk2 = json.dumps(
        {
            "candidates": [{"content": {"parts": [{"text": "sse"}], "role": "model"}}],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 3},
        }
    )
    sse_text = f"data: {chunk1}\n\ndata: {chunk2}\n\n"

    class SseResp:
        def __init__(self, text: str) -> None:
            self.text = text
            self.status_code = 200

    class SseClient:
        def __init__(self, resp: SseResp) -> None:
            self.resp = resp

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            return self.resp

    provider = GeminiCliProvider(
        GeminiCliConfig(model="gemini-2.5-pro"),
        token_provider=_token,
        client=SseClient(SseResp(sse_text)),  # type: ignore[arg-type]
    )
    res = await provider.generate(_req())
    assert res.content == "halo from sse"
    assert res.usage is not None
    assert res.usage.input_tokens == 4
    assert res.usage.output_tokens == 3
