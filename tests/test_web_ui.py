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


def test_web_ui_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        web_ui_main(["--help"])
    assert exc.value.code == 0
    assert "avo ui" in capsys.readouterr().out
