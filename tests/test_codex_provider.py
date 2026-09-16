from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from avo import ModelRequest, ToolCall
from avo.auth import AuthError
from avo.exceptions import ProviderError
from avo.oauth.store import Credential, store_credential
from avo.providers.codex import CodexConfig, CodexProvider


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


COMPLETED_TEXT = {
    "type": "response.completed",
    "response": {
        "id": "resp_1",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hello"}],
            }
        ],
        "usage": {"input_tokens": 11, "output_tokens": 3},
    },
}

COMPLETED_CALL = {
    "type": "response.completed",
    "response": {
        "id": "resp_2",
        "output": [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "add",
                "arguments": '{"a":2,"b":2}',
            }
        ],
        "usage": {},
    },
}


def _req(**over: Any) -> ModelRequest:
    base: dict[str, Any] = {
        "run_id": "r1",
        "step": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    base.update(over)
    return ModelRequest(**base)


async def _token() -> str:
    return "at-1"


@pytest.fixture
def store_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_generates_text_and_uses_bearer_identity() -> None:
    client = FakeClient(COMPLETED_TEXT)
    p = CodexProvider(CodexConfig(model="gpt-5.6-sol"), token_provider=_token, client=client)
    res = await p.generate(_req())
    assert res.content == "hello"
    assert res.usage is not None
    assert res.usage.output_tokens == 3
    sent = client.requests[0]
    assert sent["url"].endswith("/responses")
    assert sent["headers"]["Authorization"] == "Bearer at-1"
    assert sent["headers"]["originator"] == "codex_cli_rs"
    assert sent["body"]["store"] is False
    assert sent["body"]["stream"] is True


@pytest.mark.asyncio
async def test_parses_function_call() -> None:
    p = CodexProvider(
        CodexConfig(model="m"),
        token_provider=_token,
        client=FakeClient(COMPLETED_CALL),
    )
    res = await p.generate(_req())
    assert res.tool_call == ToolCall(tool_call_id="c1", name="add", arguments={"a": 2, "b": 2})


@pytest.mark.asyncio
async def test_maps_system_and_tool_items() -> None:
    client = FakeClient(COMPLETED_TEXT)
    p = CodexProvider(CodexConfig(model="m"), token_provider=_token, client=client)
    await p.generate(
        _req(
            messages=[
                {"role": "system", "content": "be brief"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 1, "b": 1}}],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "2"},
            ]
        )
    )
    items = client.requests[0]["body"]["input"]
    assert items[0] == {"role": "system", "content": "be brief"}
    assert items[-1] == {"type": "function_call_output", "call_id": "c1", "output": "2"}


@pytest.mark.asyncio
async def test_empty_output_raises() -> None:
    p = CodexProvider(
        CodexConfig(model="m"),
        token_provider=_token,
        client=FakeClient({"type": "response.completed", "response": {"output": []}}),
    )
    with pytest.raises(ProviderError, match="no output"):
        await p.generate(_req())


def test_from_avo_env_when_disabled_raises(store_dir: None) -> None:
    store_credential(
        Credential(
            provider="codex",
            kind="oauth",
            access_token="tok",
            subscription=True,
        )
    )
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        CodexConfig.from_avo_env({"AVO_ALLOW_SUBSCRIPTION": "0"}, fallback_model="gpt-5.6-sol")


def test_from_avo_env_missing_credential_raises(store_dir: None) -> None:
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        CodexConfig.from_avo_env({"AVO_ALLOW_SUBSCRIPTION": "0"}, fallback_model="gpt-5.6-sol")

    with pytest.raises(AuthError, match="No stored credential"):
        CodexConfig.from_avo_env({}, fallback_model="gpt-5.6-sol")


def test_from_avo_env_with_valid_credential_succeeds(store_dir: None) -> None:
    store_credential(
        Credential(
            provider="codex",
            kind="oauth",
            access_token="tok",
            subscription=True,
        )
    )
    config = CodexConfig.from_avo_env({}, fallback_model="gpt-5.6-sol")
    assert config.model == "gpt-5.6-sol"
    assert config.endpoint == "https://chatgpt.com/backend-api/codex/responses"


@pytest.mark.asyncio
async def test_codex_provider_401_reactive_refresh(
    store_dir: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    store_credential(
        Credential(
            provider="codex",
            kind="oauth",
            access_token="old-token",
            refresh_token="valid-refresh",
            obtained_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=6),  # lead is 5 days, so 6 days avoids proactive
            subscription=True,
        )
    )
    monkeypatch.setattr(
        "avo.oauth.flows.request_token",
        lambda entry, form: {"access_token": "new-token", "expires_in": 360000},
    )

    client = FakeClient(
        [
            _Resp({"error": "unauthorized"}, status_code=401),
            _Resp(COMPLETED_TEXT, status_code=200),
        ]
    )
    provider = CodexProvider(CodexConfig(model="gpt-5.6-sol"), client=client)
    res = await provider.generate(_req())
    assert res.content == "hello"
    assert len(client.requests) == 2
    assert client.requests[0]["headers"]["Authorization"] == "Bearer old-token"
    assert client.requests[1]["headers"]["Authorization"] == "Bearer new-token"


@pytest.mark.asyncio
async def test_codex_provider_stream_fallback() -> None:
    client = FakeClient(COMPLETED_TEXT)
    provider = CodexProvider(CodexConfig(model="gpt-5.6-sol"), token_provider=_token, client=client)
    chunks = [chunk async for chunk in provider.stream(_req())]
    assert len(chunks) > 0
    full_text = "".join(c.text for c in chunks if c.text)
    assert full_text == "hello"


@pytest.mark.asyncio
async def test_codex_provider_parses_sse_stream() -> None:
    item_payload = json.dumps(
        {
            "type": "response.output_item.done",
            "item": {
                "id": "msg_1",
                "type": "message",
                "content": [{"type": "output_text", "text": "hello from sse"}],
            },
        }
    )
    completed_payload = json.dumps(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_123",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        }
    )
    sse_text = (
        "event: response.created\n"
        'data: {"type":"response.created","response":{"id":"resp_123"}}\n\n'
        f"event: response.output_item.done\ndata: {item_payload}\n\n"
        f"event: response.completed\ndata: {completed_payload}\n\n"
    )

    class SseResp:
        def __init__(self, text: str) -> None:
            self.text = text
            self.status_code = 200

    class SseClient:
        def __init__(self, resp: SseResp) -> None:
            self.resp = resp

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            return self.resp

    provider = CodexProvider(
        CodexConfig(model="gpt-5.6-sol"),
        token_provider=_token,
        client=SseClient(SseResp(sse_text)),  # type: ignore[arg-type]
    )
    res = await provider.generate(_req())
    assert res.content == "hello from sse"
    assert res.usage is not None
    assert res.usage.input_tokens == 5
    assert res.usage.output_tokens == 3
