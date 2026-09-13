"""Tests for the local Web UI dashboard."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from avo.chat_session import SessionLifecycle
from avo.storage.sqlite import SQLiteEventStore
from avo.web_ui import AvoWebServer
from avo.web_ui import main as web_ui_main


@pytest.fixture
def web_server(tmp_path: Path):
    db_path = tmp_path / "test.db"
    # Start server on dynamic port (port 0 selects available port)
    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path)
    port = server.server_port

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}", db_path

    server.shutdown()
    server.server_close()


def test_web_ui_serves_html(web_server: tuple[str, Path]) -> None:
    base_url, _ = web_server
    req = urllib.request.Request(base_url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers.get("Content-Type", "")
        content = resp.read().decode("utf-8")
        assert "Avo Dashboard" in content


def test_web_ui_api_status(web_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    base_url, _ = web_server
    monkeypatch.setenv("AVO_PROVIDER", "ollama")
    monkeypatch.setenv("AVO_MODEL", "llama3.1")

    req = urllib.request.Request(f"{base_url}/api/status")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "version" in data
        assert data["provider"] == "ollama"
        assert data["model"] == "llama3.1"
        assert "doctor" in data


def test_web_ui_api_runs_and_trace(web_server: tuple[str, Path]) -> None:
    import asyncio

    base_url, db_path = web_server

    from tests.helpers import seed_run

    async def _seed() -> None:
        store = SQLiteEventStore(db_path)
        await seed_run(store, run_id="run-web-test-123", task="test task")
        await store.close()

    asyncio.run(_seed())

    # Query runs list
    req = urllib.request.Request(f"{base_url}/api/runs")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert "runs" in data
        assert any(r["run_id"] == "run-web-test-123" for r in data["runs"])

    # Query specific trace
    req_trace = urllib.request.Request(f"{base_url}/api/runs/run-web-test-123")
    with urllib.request.urlopen(req_trace, timeout=5) as resp:
        trace_data = json.loads(resp.read().decode("utf-8"))
        assert trace_data["run_id"] == "run-web-test-123"
        assert "state" in trace_data


def test_web_ui_api_sessions(web_server: tuple[str, Path]) -> None:
    base_url, db_path = web_server

    # Create dummy session
    session = SessionLifecycle.open(db_path)
    session.record_user_turn("sess-web-test", "Hello from user")
    session.close()

    req = urllib.request.Request(f"{base_url}/api/sessions")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert "sessions" in data
        assert any(s["session_id"] == "sess-web-test" for s in data["sessions"])


def test_web_ui_api_session_detail(web_server: tuple[str, Path]) -> None:
    base_url, db_path = web_server

    session = SessionLifecycle.open(db_path)
    session.record_user_turn("sess-detail-123", "Turn 1: user hello")
    session.record_assistant_turn(
        "sess-detail-123",
        "Turn 2: assistant response",
        run_id="run-detail-1",
        status="COMPLETED",
        stop_reason="FINAL",
    )
    session.close()

    # Query specific session
    req = urllib.request.Request(f"{base_url}/api/sessions/sess-detail-123")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data["session_id"] == "sess-detail-123"
        assert data["turn_count"] == 2
        assert len(data["turns"]) == 2
        assert data["turns"][0]["role"] == "user"
        assert data["turns"][1]["role"] == "assistant"

    # Query nonexistent session
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/sessions/nonexistent-xyz", timeout=5)
    assert exc_info.value.code == 404


def test_web_ui_api_chat_json(
    web_server: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, db_path = web_server

    from avo.models import ModelResponse
    from avo.providers.streaming import ModelChunk

    class DummyProvider:
        async def generate(self, req):
            return ModelResponse(content="Halo dari test assistant!")

        async def stream(self, req):
            yield ModelChunk(text="Halo dari test assistant!")

    monkeypatch.setattr("avo.config.build_provider_from_env", lambda env: DummyProvider())

    payload = json.dumps({"message": "Hello Avo!", "session_id": "sess-chat-test"}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["session_id"] == "sess-chat-test"
        assert "Halo dari test assistant!" in data["reply"]

    # Verify persisted in SQLite
    session = SessionLifecycle.open(db_path)
    turns = session.turns("sess-chat-test")
    session.close()
    assert len(turns) == 2
    assert turns[0].content == "Hello Avo!"
    assert "Halo dari test assistant!" in turns[1].content


def test_web_ui_api_chat_sse(web_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    base_url, db_path = web_server

    from avo.providers.streaming import ModelChunk

    class StreamingDummyProvider:
        async def stream(self, req):
            yield ModelChunk(thought="Analyzing user prompt...")
            yield ModelChunk(text="Streamed ")
            yield ModelChunk(text="reply!")

    monkeypatch.setattr("avo.config.build_provider_from_env", lambda env: StreamingDummyProvider())

    payload = json.dumps(
        {
            "message": "Tell me a joke",
            "session_id": "sess-sse-test",
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")
        body = resp.read().decode("utf-8")
        assert "data:" in body
        assert "Analyzing user prompt..." in body
        assert "Streamed" in body
        assert '"done": true' in body

    # Verify persisted
    session = SessionLifecycle.open(db_path)
    turns = session.turns("sess-sse-test")
    session.close()
    assert len(turns) == 2
    assert turns[1].content == "Streamed reply!"


def test_web_ui_api_chat_validation_and_options(web_server: tuple[str, Path]) -> None:
    base_url, _ = web_server

    # Empty message
    req_empty = urllib.request.Request(
        f"{base_url}/api/chat",
        data=json.dumps({"message": "   "}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_empty, timeout=5)
    assert exc_info.value.code == 400

    # OPTIONS CORS
    req_opt = urllib.request.Request(
        f"{base_url}/api/chat",
        method="OPTIONS",
    )
    with urllib.request.urlopen(req_opt, timeout=5) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def test_web_ui_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        web_ui_main(["--help"])
    assert exc.value.code == 0
    assert "avo ui" in capsys.readouterr().out
