"""Tests for DurableApprovalStore and persistent WebApprovalBridge."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from avo.models import ToolCall
from avo.web_approval import DurableApprovalStore, WebApprovalBridge


def test_durable_approval_store_crud(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    store = DurableApprovalStore(db_path)

    request_id = "req-test-1"
    run_id = "run-test-1"
    tool_name = "run_terminal"
    args = {"command": "pytest"}

    store.create_approval(
        request_id=request_id,
        run_id=run_id,
        tool_name=tool_name,
        arguments=args,
        timeout_seconds=60.0,
    )

    # Verify retrieval
    item = store.get_approval(request_id)
    assert item is not None
    assert item["request_id"] == request_id
    assert item["run_id"] == run_id
    assert item["tool_name"] == tool_name
    assert item["arguments"] == args
    assert item["status"] == "pending"

    # List pending
    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0]["request_id"] == request_id

    # Record decision
    success = store.record_decision(request_id, approved=True)
    assert success is True

    # Check updated item
    updated = store.get_approval(request_id)
    assert updated is not None
    assert updated["status"] == "approved"
    assert updated["decided_at"] is not None

    # Pending list should now be empty
    assert len(store.list_pending()) == 0


def test_durable_approval_store_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"

    # Process 1 creates approval
    store1 = DurableApprovalStore(db_path)
    store1.create_approval(
        request_id="req-persist",
        run_id="run-persist",
        tool_name="edit_file",
        arguments={"path": "foo.py"},
        timeout_seconds=120.0,
    )

    # Process 2 (new instance on same DB) reads and resolves
    store2 = DurableApprovalStore(db_path)
    pending = store2.list_pending()
    assert len(pending) == 1
    assert pending[0]["request_id"] == "req-persist"

    store2.record_decision("req-persist", approved=False)
    resolved = store2.get_approval("req-persist")
    assert resolved is not None
    assert resolved["status"] == "denied"


@pytest.mark.asyncio
async def test_bridge_with_durable_store(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    store = DurableApprovalStore(db_path)
    bridge = WebApprovalBridge(store=store)

    tool_call = ToolCall(
        name="test_tool",
        arguments={"key": "val"},
    )

    # Start approval request in background
    req_task = asyncio.create_task(bridge.request_approval(tool_call, run_id="run-bridge"))
    await asyncio.sleep(0.05)

    pending = bridge.list_pending()
    assert len(pending) == 1
    req_id = pending[0]["request_id"]

    # Verify store has the pending item
    stored = store.get_approval(req_id)
    assert stored is not None
    assert stored["status"] == "pending"

    # Resolve approval via bridge
    resolved = bridge.resolve(req_id, approved=True)
    assert resolved is True

    result = await req_task
    assert result is True

    # Verify store is updated
    updated = store.get_approval(req_id)
    assert updated is not None
    assert updated["status"] == "approved"


@pytest.mark.asyncio
async def test_bridge_recovers_durable_decision_after_process_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    store1 = DurableApprovalStore(db_path)
    store1.create_approval(
        request_id="req-restart-1",
        run_id="run-restart-1",
        tool_name="edit_file",
        arguments={"path": "src/main.py"},
        timeout_seconds=60.0,
    )

    # Webhook resolves approval while process is offline
    store1.record_decision("req-restart-1", approved=True)

    # Process 2 boots up fresh with new bridge on same DB
    store2 = DurableApprovalStore(db_path)
    bridge2 = WebApprovalBridge(store=store2)

    tool_call = ToolCall(name="edit_file", arguments={"path": "src/main.py"})

    # Agent resumes and requests approval for the run
    decision = await bridge2.request_approval(tool_call, run_id="run-restart-1")
    assert decision is True


def test_durable_store_rejects_replay_on_argument_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    store = DurableApprovalStore(db_path)
    store.create_approval(
        request_id="req-mismatch-1",
        run_id="run-mismatch",
        tool_name="edit_file",
        arguments={"path": "safe.py"},
        timeout_seconds=60.0,
    )
    store.record_decision("req-mismatch-1", approved=True)

    # Matching args succeeds
    decision_ok = store.find_decision_for_run(
        "run-mismatch", "edit_file", arguments={"path": "safe.py"}
    )
    assert decision_ok is True

    # Differing args fails closed (returns None, cannot replay approved status on changed args)
    decision_diff = store.find_decision_for_run(
        "run-mismatch", "edit_file", arguments={"path": "danger.py"}
    )
    assert decision_diff is None


def test_durable_store_rejects_expired_approvals(tmp_path: Path) -> None:
    db_path = tmp_path / "approvals.db"
    store = DurableApprovalStore(db_path)
    # Approval created with 0.001s timeout
    store.create_approval(
        request_id="req-expire-1",
        run_id="run-expire",
        tool_name="run_terminal",
        arguments={"cmd": "ls"},
        timeout_seconds=0.001,
    )
    store.record_decision("req-expire-1", approved=True)

    import time

    time.sleep(0.01)

    # After expiry, find_decision_for_run must reject stale approval
    expired = store.find_decision_for_run("run-expire", "run_terminal", arguments={"cmd": "ls"})
    assert expired is None
