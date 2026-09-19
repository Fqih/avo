"""Tests for Web Cockpit HTTP endpoints: approvals and DAG trace."""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from avo.models import ToolCall
from avo.web_ui import AvoWebServer

_SERVER_TOKENS: dict[int, str] = {}


def _mutation_headers(base_url: str) -> dict[str, str]:
    port = urllib.parse.urlsplit(base_url).port
    assert port is not None
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_SERVER_TOKENS[port]}",
    }


@pytest.fixture
def cockpit_server(tmp_path: Path):
    db_path = tmp_path / "cockpit_test.db"
    server = AvoWebServer(("127.0.0.1", 0), database_path=db_path)
    port = server.server_port
    _SERVER_TOKENS[port] = server.auth_token

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}", server

    server.shutdown()
    server.server_close()


def test_http_approvals_endpoints(cockpit_server: tuple[str, AvoWebServer]) -> None:
    base_url, server = cockpit_server

    # Check initially empty pending
    req = urllib.request.Request(f"{base_url}/api/approvals/pending")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["count"] == 0
        assert data["pending"] == []

    # Manually register a pending approval directly into bridge
    call = ToolCall(tool_call_id="tc_http_1", name="dangerous_tool", arguments={"force": True})
    import asyncio

    loop = asyncio.new_event_loop()
    future = loop.create_future()

    from datetime import UTC, datetime

    from avo.web_approval import PendingApproval

    pending_req = PendingApproval(
        request_id="req_test_1",
        tool_name=call.name,
        arguments=call.arguments if isinstance(call.arguments, dict) else {},
        created_at=datetime.now(UTC),
        future=future,
        run_id="run_test",
        timeout_seconds=60.0,
    )
    server.approval_bridge._pending["req_test_1"] = pending_req

    # Now pending should list it
    req = urllib.request.Request(f"{base_url}/api/approvals/pending")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data["count"] == 1
        assert data["pending"][0]["request_id"] == "req_test_1"
        assert data["pending"][0]["tool_name"] == "dangerous_tool"

    # Submit decision via POST /api/approvals/{req_id}/decision
    post_data = json.dumps({"approved": True, "reason": "Operator confirmed"}).encode("utf-8")
    post_req = urllib.request.Request(
        f"{base_url}/api/approvals/req_test_1/decision",
        data=post_data,
        headers=_mutation_headers(base_url),
        method="POST",
    )
    with urllib.request.urlopen(post_req, timeout=5) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["status"] == "resolved"
        assert res["approved"] is True
        assert res["request_id"] == "req_test_1"

    loop.close()


def test_http_dag_endpoint_404_on_missing_run(cockpit_server: tuple[str, AvoWebServer]) -> None:
    base_url, _ = cockpit_server
    req = urllib.request.Request(f"{base_url}/api/runs/non_existent_run/dag")
    with pytest.raises(HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 404
