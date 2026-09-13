"""Tests for the local Web UI dashboard."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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
        assert "renderMarkdown" in content
        assert "copyCode" in content
        assert "code-block-wrapper" in content


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
        assert "cost" in data
        assert "total_tokens" in data["cost"]


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
        assert "context_advice" in data
        assert "usage_percent" in data["context_advice"]
        assert "context_limit" in data["context_advice"]

    # Query nonexistent session
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/sessions/nonexistent-xyz", timeout=5)
    assert exc_info.value.code == 404


def test_web_ui_api_history(web_server: tuple[str, Path]) -> None:
    base_url, db_path = web_server

    session = SessionLifecycle.open(db_path)
    session.record_user_turn("sess-hist-1", "Deploy application with Helm")
    session.record_assistant_turn(
        "sess-hist-1",
        "Helm chart released to cluster",
        run_id="run-h1",
        status="COMPLETED",
        stop_reason="FINAL",
    )
    session.close()

    # 1. Search for keyword "Helm"
    req = urllib.request.Request(f"{base_url}/api/history?q=Helm")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["query"] == "Helm"
        assert len(data["results"]) == 2

    # 2. Search scoped to session
    req_scoped = urllib.request.Request(f"{base_url}/api/history?q=cluster&session_id=sess-hist-1")
    with urllib.request.urlopen(req_scoped, timeout=5) as resp:
        assert resp.status == 200
        data_scoped = json.loads(resp.read().decode("utf-8"))
        assert len(data_scoped["results"]) == 1
        assert data_scoped["results"][0]["role"] == "assistant"


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


def test_web_ui_api_cost(web_server: tuple[str, Path]) -> None:
    from decimal import Decimal

    from avo.ledger import TokenLedger
    from avo.models import TokenUsage

    base_url, db_path = web_server
    ledger = TokenLedger(db_path)
    ledger.record(
        run_id="run-cost-1",
        step=1,
        model="gpt-4o",
        usage=TokenUsage(input_tokens=150, output_tokens=50),
        cost_usd=Decimal("0.0050"),
    )
    ledger.close()

    req = urllib.request.Request(f"{base_url}/api/cost")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["run_count"] == 1
        assert data["total"]["total_tokens"] == 200
        assert data["total"]["input_tokens"] == 150
        assert data["total"]["output_tokens"] == 50
        assert data["cost_usd"] == "0.0050"
        assert len(data["models"]) == 1
        assert data["models"][0]["model"] == "gpt-4o"


def test_web_ui_api_persona_and_permissions(web_server: tuple[str, Path]) -> None:
    import urllib.error

    base_url, _ = web_server

    # 1. Verify status contains persona and permissions
    req_status = urllib.request.Request(f"{base_url}/api/status")
    with urllib.request.urlopen(req_status, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert "persona" in data
        assert data["persona"]["active"] == "default"
        assert "available" in data["persona"]
        assert "coder" in data["persona"]["available"]
        assert "permissions" in data
        assert data["permissions"]["mode"] == "bypass"
        assert "accept_edits" in data["permissions"]["available"]

    # 2. GET /api/persona
    req_p_get = urllib.request.Request(f"{base_url}/api/persona")
    with urllib.request.urlopen(req_p_get, timeout=5) as resp:
        p_data = json.loads(resp.read().decode("utf-8"))
        assert p_data["active"] == "default"

    # 3. POST /api/persona - switch to coder
    req_p_post = urllib.request.Request(
        f"{base_url}/api/persona",
        data=json.dumps({"persona": "coder", "instructions": "Write clean async code"}).encode(
            "utf-8"
        ),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_p_post, timeout=5) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["persona"] == "coder"
        assert res["instructions"] == "Write clean async code"

    # 4. POST /api/persona - invalid persona returns 400
    req_p_bad = urllib.request.Request(
        f"{base_url}/api/persona",
        data=json.dumps({"persona": "nonexistent_role"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_p_bad, timeout=5)
    assert exc_info.value.code == 400

    # 4b. POST /api/persona - register custom persona
    req_p_reg = urllib.request.Request(
        f"{base_url}/api/persona",
        data=json.dumps(
            {
                "register": {
                    "name": "devops",
                    "prompt": "Role: DevOps Engineer.\nFocus: CI/CD automation.",
                },
                "persona": "devops",
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_p_reg, timeout=5) as resp:
        assert resp.status == 200
        reg_res = json.loads(resp.read().decode("utf-8"))
        assert reg_res["ok"] is True
        assert reg_res["persona"] == "devops"
        assert "devops" in reg_res["available"]

    # 5. GET /api/permissions
    req_perm_get = urllib.request.Request(f"{base_url}/api/permissions")
    with urllib.request.urlopen(req_perm_get, timeout=5) as resp:
        perm_data = json.loads(resp.read().decode("utf-8"))
        assert perm_data["mode"] == "bypass"

    # 6. POST /api/permissions - switch to accept_edits
    req_perm_post = urllib.request.Request(
        f"{base_url}/api/permissions",
        data=json.dumps({"mode": "accept_edits"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_perm_post, timeout=5) as resp:
        assert resp.status == 200
        perm_res = json.loads(resp.read().decode("utf-8"))
        assert perm_res["ok"] is True
        assert perm_res["mode"] == "accept_edits"

    # 7. POST /api/permissions - invalid mode returns 400
    req_perm_bad = urllib.request.Request(
        f"{base_url}/api/permissions",
        data=json.dumps({"mode": "invalid_mode"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_perm_bad:
        urllib.request.urlopen(req_perm_bad, timeout=5)
    assert exc_perm_bad.value.code == 400


def test_web_ui_api_git(tmp_path: Path) -> None:
    # 1. Initialize git repository
    ws = tmp_path / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(ws), check=True)
    (ws / "tracked.txt").write_text("initial content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init commit"], cwd=str(ws), check=True)

    db_path = tmp_path / "git_test.db"
    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path, workspace_root=ws)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # A. GET /api/git - verify clean state and initial commit
        req_get = urllib.request.Request(f"{base_url}/api/git")
        with urllib.request.urlopen(req_get, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["is_repo"] is True
            assert data["branch"] == "main"
            assert data["is_clean"] is True
            assert len(data["recent_commits"]) == 1
            assert data["recent_commits"][0]["subject"] == "init commit"

        # B. Modify file and query status
        (ws / "tracked.txt").write_text("updated content", encoding="utf-8")
        with urllib.request.urlopen(req_get, timeout=5) as resp:
            data_modified = json.loads(resp.read().decode("utf-8"))
            assert data_modified["is_clean"] is False
            assert "tracked.txt" in data_modified["modified"]
            assert "+updated content" in data_modified["diff"]

        # C. POST /api/git/commit
        req_commit = urllib.request.Request(
            f"{base_url}/api/git/commit",
            data=json.dumps({"message": "feat: update tracked file"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_commit, timeout=5) as resp:
            assert resp.status == 200
            commit_res = json.loads(resp.read().decode("utf-8"))
            assert commit_res["ok"] is True
            assert "commit_hash" in commit_res
            assert commit_res["message"] == "feat: update tracked file"

        # Verify clean again
        with urllib.request.urlopen(req_get, timeout=5) as resp:
            data_after_commit = json.loads(resp.read().decode("utf-8"))
            assert data_after_commit["is_clean"] is True
            assert len(data_after_commit["recent_commits"]) == 2

        # D. POST /api/git/branch - create and switch
        req_branch = urllib.request.Request(
            f"{base_url}/api/git/branch",
            data=json.dumps({"branch": "feature/dashboard-git", "create": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_branch, timeout=5) as resp:
            assert resp.status == 200
            branch_res = json.loads(resp.read().decode("utf-8"))
            assert branch_res["ok"] is True
            assert branch_res["branch"] == "feature/dashboard-git"

        # Verify current branch is updated
        with urllib.request.urlopen(req_get, timeout=5) as resp:
            data_branch = json.loads(resp.read().decode("utf-8"))
            assert data_branch["branch"] == "feature/dashboard-git"
            branch_names = [b["name"] for b in data_branch["branches"]]
            assert "feature/dashboard-git" in branch_names
            assert "main" in branch_names

    finally:
        server.shutdown()
        server.server_close()


def test_web_ui_api_git_non_repo(tmp_path: Path) -> None:
    empty_dir = tmp_path / "not_git"
    empty_dir.mkdir()
    db_path = tmp_path / "nongit.db"

    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path, workspace_root=empty_dir)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        req_get = urllib.request.Request(f"{base_url}/api/git")
        with urllib.request.urlopen(req_get, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["is_repo"] is False
            assert data["is_clean"] is True
            assert data["entries"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_web_ui_api_git_stash(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(ws), check=True)
    (ws / "file.txt").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(ws), check=True)

    db_path = tmp_path / "git_stash_test.db"
    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path, workspace_root=ws)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. Modify file
        (ws / "file.txt").write_text("dirty state", encoding="utf-8")

        # 2. POST /api/git/stash (save)
        req_save = urllib.request.Request(
            f"{base_url}/api/git/stash",
            data=json.dumps({"action": "save", "message": "save for later"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_save, timeout=5) as resp:
            assert resp.status == 200
            save_res = json.loads(resp.read().decode("utf-8"))
            assert save_res["ok"] is True
            assert len(save_res["stashes"]) == 1
            assert "save for later" in save_res["stashes"][0]["description"]

        assert (ws / "file.txt").read_text(encoding="utf-8") == "initial"

        # 3. GET /api/git/stash
        req_get_stash = urllib.request.Request(f"{base_url}/api/git/stash")
        with urllib.request.urlopen(req_get_stash, timeout=5) as resp:
            assert resp.status == 200
            stash_data = json.loads(resp.read().decode("utf-8"))
            assert "stashes" in stash_data
            assert len(stash_data["stashes"]) == 1

        # 4. POST /api/git/stash (pop)
        req_pop = urllib.request.Request(
            f"{base_url}/api/git/stash",
            data=json.dumps({"action": "pop", "index": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_pop, timeout=5) as resp:
            assert resp.status == 200
            pop_res = json.loads(resp.read().decode("utf-8"))
            assert pop_res["ok"] is True
            assert len(pop_res["stashes"]) == 0

        assert (ws / "file.txt").read_text(encoding="utf-8") == "dirty state"

        # 5. POST /api/git/stash (save & drop)
        with urllib.request.urlopen(req_save, timeout=5) as resp:
            assert resp.status == 200
            save_res2 = json.loads(resp.read().decode("utf-8"))
            assert len(save_res2["stashes"]) == 1

        req_drop = urllib.request.Request(
            f"{base_url}/api/git/stash",
            data=json.dumps({"action": "drop", "index": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_drop, timeout=5) as resp:
            assert resp.status == 200
            drop_res = json.loads(resp.read().decode("utf-8"))
            assert drop_res["ok"] is True
            assert len(drop_res["stashes"]) == 0
    finally:
        server.shutdown()
        server.server_close()


def test_web_ui_api_git_commit_show(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(ws), check=True)
    (ws / "sample.py").write_text("print('hello world')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat: initial commit"], cwd=str(ws), check=True)

    # Get commit hash
    rev_proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(ws),
        check=True,
        capture_output=True,
        text=True,
    )
    commit_hash = rev_proc.stdout.strip()

    db_path = tmp_path / "git_commit_test.db"
    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path, workspace_root=ws)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # A. Query commit details
        req_commit = urllib.request.Request(f"{base_url}/api/git/commit/{commit_hash}")
        with urllib.request.urlopen(req_commit, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["hash"] == commit_hash
            assert data["subject"] == "feat: initial commit"
            assert data["author"] == "Tester"
            assert data["email"] == "tester@example.com"
            assert "sample.py" in data["diff"]
            assert "print('hello world')" in data["diff"]
            assert data["truncated"] is False

        # B. Query non-existent commit -> 404
        req_missing = urllib.request.Request(f"{base_url}/api/git/commit/deadbeef0000")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_missing, timeout=5)
        assert exc_info.value.code == 404

        # C. Verify index.html contains commitModal and stash UI
        req_html = urllib.request.Request(base_url)
        with urllib.request.urlopen(req_html, timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert "commitModal" in html
            assert "inspectCommit" in html
            assert "gitStashesList" in html
            assert "handleStashPop" in html
            assert "handleStashDrop" in html
    finally:
        server.shutdown()
        server.server_close()


def test_web_ui_api_router_and_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVO_PROVIDER", "ollama")
    monkeypatch.setenv("AVO_MODEL", "llama3.1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-mock-test-key")

    db_path = tmp_path / "router_test.db"
    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path, workspace_root=tmp_path)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. GET /api/router
        req_get = urllib.request.Request(f"{base_url}/api/router")
        with urllib.request.urlopen(req_get, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["active_provider"] == "ollama"
            assert data["active_model"] == "llama3.1"
            assert data["is_router"] is False
            assert len(data["routes"]) == 1
            assert data["routes"][0]["name"] == "ollama"

        # 2. POST /api/provider - valid switch
        req_prov = urllib.request.Request(
            f"{base_url}/api/provider",
            data=json.dumps(
                {
                    "provider": "openrouter",
                    "model": "anthropic/claude-3-haiku",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_prov, timeout=5) as resp:
            assert resp.status == 200
            prov_data = json.loads(resp.read().decode("utf-8"))
            assert prov_data["ok"] is True
            assert prov_data["provider"] == "openrouter"
            assert prov_data["model"] == "anthropic/claude-3-haiku"
            assert os.environ["AVO_PROVIDER"] == "openrouter"
            assert os.environ["AVO_MODEL"] == "anthropic/claude-3-haiku"

        # 3. POST /api/provider - invalid payload returns 400
        req_prov_bad = urllib.request.Request(
            f"{base_url}/api/provider",
            data=json.dumps({"provider": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_bad:
            urllib.request.urlopen(req_prov_bad, timeout=5)
        assert exc_bad.value.code == 400

        # 4. POST /api/router/probe
        req_probe = urllib.request.Request(
            f"{base_url}/api/router/probe",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_probe, timeout=5) as resp:
            assert resp.status == 200
            probe_data = json.loads(resp.read().decode("utf-8"))
            assert probe_data["ok"] is True
            assert "outcomes" in probe_data

        # 5. POST /api/router/bench - with mocked bench functions
        from avo.bench import RouteBenchResult

        async def _mock_bench_route(
            name: str, provider: Any, prompt: str = "", **kwargs: Any
        ) -> RouteBenchResult:
            return RouteBenchResult(
                route=name,
                provider_name=getattr(provider, "name", name),
                model=getattr(provider, "model", "default"),
                success=True,
                latency_ms=42.0,
                ttft_ms=15.0,
                output="ok",
                error=None,
            )

        async def _mock_bench_all(
            routes: Any, prompt: str = "", **kwargs: Any
        ) -> list[RouteBenchResult]:
            return [
                RouteBenchResult(
                    route=name,
                    provider_name=getattr(p, "name", name),
                    model=getattr(p, "model", "default"),
                    success=True,
                    latency_ms=30.0 + idx * 10,
                    ttft_ms=10.0,
                    output="ok",
                    error=None,
                )
                for idx, (name, p) in enumerate(routes)
            ]

        monkeypatch.setattr("avo.bench.benchmark_route", _mock_bench_route)
        monkeypatch.setattr("avo.bench.benchmark_all_routes", _mock_bench_all)

        req_bench = urllib.request.Request(
            f"{base_url}/api/router/bench",
            data=json.dumps({"prompt": "speed test"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_bench, timeout=5) as resp:
            assert resp.status == 200
            bench_data = json.loads(resp.read().decode("utf-8"))
            assert bench_data["ok"] is True
            assert bench_data["prompt"] == "speed test"
            assert len(bench_data["results"]) == 1
            assert bench_data["results"][0]["route"] == "openrouter"
            assert bench_data["results"][0]["latency_ms"] == 42.0

        # 6. Verify HTML contains router playground elements
        req_html = urllib.request.Request(base_url)
        with urllib.request.urlopen(req_html, timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert "routerRoutesTableBody" in html
            assert "probeRouterHealth" in html
            assert "routerBenchResultsContainer" in html
            assert "runRouterBenchmark" in html
            assert "handleSwitchProvider" in html

        # 7. Test router provider branch directly in server methods
        from avo.providers.fake import FakeProvider
        from avo.providers.router import BaseRouterProvider

        class MockRouter(BaseRouterProvider):
            def __init__(self) -> None:
                p1 = FakeProvider([])
                p1.name = "fake1"
                p1.model = "m1"
                p2 = FakeProvider([])
                p2.name = "fake2"
                p2.model = "m2"
                super().__init__([("r1", p1), ("r2", p2)])

            async def generate(self, request: Any) -> Any:
                raise NotImplementedError

        mock_router = MockRouter()
        monkeypatch.setattr(
            "avo.config.build_provider_from_env",
            lambda env: mock_router,
        )

        status = server.get_sync_router_status()
        assert status["is_router"] is True
        assert len(status["routes"]) == 2
        assert status["routes"][0]["name"] == "r1"
        assert status["routes"][1]["name"] == "r2"

        probe = server.sync_probe_router()
        assert probe["ok"] is True
        assert "outcomes" in probe

        bench = server.sync_bench_routes(prompt="bench prompt")
        assert bench["ok"] is True
        assert len(bench["results"]) == 2
        assert bench["results"][0]["route"] in {"r1", "r2"}
    finally:
        server.shutdown()
        server.server_close()


def test_web_ui_api_workspace_tree_and_editor(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    (ws / "src" / "pkg").mkdir(parents=True)
    (ws / "src" / "pkg" / "module.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (ws / "README.md").write_text("# Test Workspace\n", encoding="utf-8")

    # Ignored folders
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("git config", encoding="utf-8")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "cached.pyc").write_text("pyc", encoding="utf-8")

    db_path = tmp_path / "workspace_test.db"
    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path, workspace_root=ws)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. GET /api/workspace/tree
        req_tree = urllib.request.Request(f"{base_url}/api/workspace/tree")
        with urllib.request.urlopen(req_tree, timeout=5) as resp:
            assert resp.status == 200
            tree_data = json.loads(resp.read().decode("utf-8"))
            assert tree_data["workspace_name"] == "workspace"
            assert tree_data["total_entries"] >= 3
            names = [node["name"] for node in tree_data["tree"]]
            assert "src" in names
            assert "README.md" in names
            assert ".git" not in names
            assert "__pycache__" not in names

            # Check nested structure
            src_node = next(n for n in tree_data["tree"] if n["name"] == "src")
            assert src_node["type"] == "directory"
            assert len(src_node["children"]) == 1
            assert src_node["children"][0]["name"] == "pkg"

        # 2. GET /api/workspace/file - valid read
        req_file = urllib.request.Request(f"{base_url}/api/workspace/file?path=src/pkg/module.py")
        with urllib.request.urlopen(req_file, timeout=5) as resp:
            assert resp.status == 200
            file_data = json.loads(resp.read().decode("utf-8"))
            assert file_data["ok"] is True
            assert file_data["path"] == "src/pkg/module.py"
            assert "def add(a, b):" in file_data["content"]
            assert file_data["line_count"] == 2

        # 3. GET /api/workspace/file - error cases
        # 3a. missing path query param
        with pytest.raises(urllib.error.HTTPError) as exc_missing:
            urllib.request.urlopen(f"{base_url}/api/workspace/file", timeout=5)
        assert exc_missing.value.code == 400

        # 3b. nonexistent file
        with pytest.raises(urllib.error.HTTPError) as exc_nonexistent:
            urllib.request.urlopen(f"{base_url}/api/workspace/file?path=nonexistent.txt", timeout=5)
        assert exc_nonexistent.value.code == 400

        # 3c. path traversal escape attempt
        with pytest.raises(urllib.error.HTTPError) as exc_escape:
            urllib.request.urlopen(
                f"{base_url}/api/workspace/file?path=../../secret.txt", timeout=5
            )
        assert exc_escape.value.code == 400

        # 3d. directory requested instead of file
        with pytest.raises(urllib.error.HTTPError) as exc_dir:
            urllib.request.urlopen(f"{base_url}/api/workspace/file?path=src", timeout=5)
        assert exc_dir.value.code == 400

        # 4. POST /api/workspace/file - valid edit of existing file
        req_save = urllib.request.Request(
            f"{base_url}/api/workspace/file",
            data=json.dumps(
                {
                    "path": "src/pkg/module.py",
                    "content": "def add(a, b):\n    return a + b + 1\n",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_save, timeout=5) as resp:
            assert resp.status == 200
            save_res = json.loads(resp.read().decode("utf-8"))
            assert save_res["ok"] is True
            assert save_res["path"] == "src/pkg/module.py"

        # Verify disk updated
        disk_content = (ws / "src" / "pkg" / "module.py").read_text(encoding="utf-8")
        assert "return a + b + 1" in disk_content

        # 5. POST /api/workspace/file - create brand new file in new subfolder
        req_create = urllib.request.Request(
            f"{base_url}/api/workspace/file",
            data=json.dumps(
                {
                    "path": "docs/guide.md",
                    "content": "# User Guide\n\nWelcome to Avo.\n",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_create, timeout=5) as resp:
            assert resp.status == 200
            create_res = json.loads(resp.read().decode("utf-8"))
            assert create_res["ok"] is True
            assert create_res["path"] == "docs/guide.md"

        saved_guide = (ws / "docs" / "guide.md").read_text(encoding="utf-8")
        assert saved_guide == "# User Guide\n\nWelcome to Avo.\n"

        # 6. POST /api/workspace/file - error handling
        # 6a. empty body
        req_empty = urllib.request.Request(
            f"{base_url}/api/workspace/file",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_p_empty:
            urllib.request.urlopen(req_empty, timeout=5)
        assert exc_p_empty.value.code == 400

        # 6b. escaping root
        req_bad_path = urllib.request.Request(
            f"{base_url}/api/workspace/file",
            data=json.dumps(
                {
                    "path": "../outside.txt",
                    "content": "escape attempt",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_p_bad:
            urllib.request.urlopen(req_bad_path, timeout=5)
        assert exc_p_bad.value.code == 400

        # 7. Verify HTML contains workspace tab & explorer elements
        req_html = urllib.request.Request(base_url)
        with urllib.request.urlopen(req_html, timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert "tab-workspace" in html
            assert "view-workspace" in html
            assert "workspaceTreeContainer" in html
            assert "editorTextarea" in html
            assert "refreshWorkspaceTree" in html
            assert "saveWorkspaceFile" in html
    finally:
        server.shutdown()
        server.server_close()
