"""Tests for Web Cockpit Full-Duplex: WebApprovalBridge and DAG trace graph."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from avo.models import ToolCall
from avo.web_approval import WebApprovalBridge
from avo.web_dag import build_run_dag


@pytest.mark.asyncio
async def test_web_approval_bridge_lifecycle() -> None:
    bridge = WebApprovalBridge(default_timeout_seconds=5.0)
    callback = bridge.create_callback(run_id="run_123")

    call = ToolCall(tool_call_id="tc_1", name="run_shell", arguments={"command": "ls -la"})

    # Start approval request task
    task = asyncio.create_task(callback(call))

    await asyncio.sleep(0.05)

    pending = bridge.list_pending()
    assert len(pending) == 1
    req = pending[0]
    assert req["tool_name"] == "run_shell"
    assert req["run_id"] == "run_123"
    req_id = req["request_id"]

    # Approve request
    resolved = bridge.resolve(req_id, approved=True)
    assert resolved is True

    decision = await task
    assert decision is True
    assert len(bridge.list_pending()) == 0


@pytest.mark.asyncio
async def test_web_approval_bridge_denial() -> None:
    bridge = WebApprovalBridge(default_timeout_seconds=5.0)
    callback = bridge.create_callback(run_id="run_456")

    call = ToolCall(tool_call_id="tc_2", name="delete_db", arguments={})
    task = asyncio.create_task(callback(call))

    await asyncio.sleep(0.05)
    pending = bridge.list_pending()
    assert len(pending) == 1
    req_id = pending[0]["request_id"]

    # Deny request
    bridge.resolve(req_id, approved=False)

    decision = await task
    assert decision is False
    assert len(bridge.list_pending()) == 0


def test_build_run_dag() -> None:
    now = datetime.now(UTC).isoformat()
    trace_data: dict[str, Any] = {
        "run_id": "run_test_dag",
        "entries": [
            {
                "sequence": 1,
                "event_type": "run_started",
                "summary": "Agent started",
                "created_at": now,
            },
            {
                "sequence": 2,
                "event_type": "model_call_completed",
                "summary": "Prompt model",
                "created_at": now,
            },
            {
                "sequence": 3,
                "event_type": "tool_call_completed",
                "summary": "Execute read_file",
                "created_at": now,
            },
        ],
    }

    dag = build_run_dag(trace_data)
    assert dag["run_id"] == "run_test_dag"
    assert dag["node_count"] == 3
    assert dag["edge_count"] == 2
    assert "graph TD" in dag["mermaid"]
    assert "node_1" in dag["mermaid"]
    assert "node_3" in dag["mermaid"]
