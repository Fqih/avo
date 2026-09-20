"""Web approval bridge and durable storage for human-in-the-loop tool execution."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from avo.models import ToolCall


@dataclass
class PendingApproval:
    """A tool execution waiting for operator approval from the Web UI."""

    request_id: str
    tool_name: str
    arguments: dict[str, Any]
    created_at: datetime
    future: asyncio.Future[bool] = field(repr=False)
    run_id: str | None = None
    timeout_seconds: float = 300.0


class DurableApprovalStore:
    """SQLite-backed persistent store for tool approvals."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    id TEXT PRIMARY KEY,
                    run_id TEXT,
                    tool_name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    timeout_seconds REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approvals_status ON pending_approvals(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approvals_run ON pending_approvals(run_id)"
            )

    def create_approval(
        self,
        request_id: str,
        run_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_seconds: float = 300.0,
    ) -> None:
        """Insert a pending approval record."""
        now_str = datetime.now(UTC).isoformat()
        args_json = json.dumps(arguments)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_approvals
                (id, run_id, tool_name, arguments, status, created_at, decided_at, timeout_seconds)
                VALUES (?, ?, ?, ?, 'pending', ?, NULL, ?)
                """,
                (request_id, run_id, tool_name, args_json, now_str, timeout_seconds),
            )

    def get_approval(self, request_id: str) -> dict[str, Any] | None:
        """Fetch approval details by request_id."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM pending_approvals WHERE id = ?",
                (request_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def record_decision(self, request_id: str, approved: bool) -> bool:
        """Record approval decision in database."""
        status = "approved" if approved else "denied"
        now_str = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_approvals
                SET status = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, now_str, request_id),
            )
            return cursor.rowcount > 0

    def list_pending(self) -> list[dict[str, Any]]:
        """List all approvals currently pending decision."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM pending_approvals WHERE status = 'pending' ORDER BY created_at ASC"
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        raw_args = row["arguments"]
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else {}
        except Exception:
            args = {}
        return {
            "request_id": row["id"],
            "run_id": row["run_id"],
            "tool_name": row["tool_name"],
            "arguments": args,
            "status": row["status"],
            "created_at": row["created_at"],
            "decided_at": row["decided_at"],
            "timeout_seconds": row["timeout_seconds"],
        }


class WebApprovalBridge:
    """Coordinates tool approvals between running agents, DB store, and the Web Cockpit."""

    def __init__(
        self,
        default_timeout_seconds: float = 300.0,
        store: DurableApprovalStore | None = None,
    ) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self.store = store
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def create_callback(self, run_id: str | None = None) -> Callable[[ToolCall], Awaitable[bool]]:
        """Return an async approval_callback compatible with AgentRuntime."""

        async def _callback(tool_call: ToolCall) -> bool:
            return await self.request_approval(tool_call, run_id=run_id)

        return _callback

    async def request_approval(self, tool_call: ToolCall, run_id: str | None = None) -> bool:
        """Register a pending approval and pause until web decision or timeout."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        request_id = secrets.token_hex(8)
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        req = PendingApproval(
            request_id=request_id,
            tool_name=tool_call.name,
            arguments=args,
            created_at=datetime.now(UTC),
            future=future,
            run_id=run_id,
            timeout_seconds=self.default_timeout_seconds,
        )

        with self._lock:
            self._pending[request_id] = req

        if self.store is not None:
            self.store.create_approval(
                request_id=request_id,
                run_id=run_id,
                tool_name=tool_call.name,
                arguments=args,
                timeout_seconds=self.default_timeout_seconds,
            )

        try:
            res = await asyncio.wait_for(future, timeout=self.default_timeout_seconds)
            return res
        except TimeoutError:
            if self.store is not None:
                self.store.record_decision(request_id, approved=False)
            return False
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def list_pending(self) -> list[dict[str, Any]]:
        """Return all active pending approval requests."""
        if self.store is not None:
            return self.store.list_pending()

        with self._lock:
            return [
                {
                    "request_id": req.request_id,
                    "run_id": req.run_id,
                    "tool_name": req.tool_name,
                    "arguments": req.arguments,
                    "status": "pending",
                    "created_at": req.created_at.isoformat(),
                    "decided_at": None,
                    "timeout_seconds": req.timeout_seconds,
                }
                for req in self._pending.values()
            ]

    def get_approval(self, request_id: str) -> dict[str, Any] | None:
        """Fetch approval details by request ID."""
        if self.store is not None:
            return self.store.get_approval(request_id)

        with self._lock:
            req = self._pending.get(request_id)
            if not req:
                return None
            return {
                "request_id": req.request_id,
                "run_id": req.run_id,
                "tool_name": req.tool_name,
                "arguments": req.arguments,
                "status": "pending",
                "created_at": req.created_at.isoformat(),
                "decided_at": None,
                "timeout_seconds": req.timeout_seconds,
            }

    def resolve(self, request_id: str, approved: bool, reason: str = "") -> bool:
        """Resolve a pending approval request. Return True if found and resolved."""
        del reason
        recorded = False
        if self.store is not None:
            recorded = self.store.record_decision(request_id, approved=approved)

        with self._lock:
            req = self._pending.get(request_id)
            if req:
                if not req.future.done():
                    with contextlib.suppress(RuntimeError):
                        req.future.get_loop().call_soon_threadsafe(req.future.set_result, approved)
                return True

        return recorded

    def clear(self) -> None:
        """Cancel and clear all pending approvals."""
        with self._lock:
            for req in self._pending.values():
                if not req.future.done():
                    with contextlib.suppress(RuntimeError):
                        req.future.get_loop().call_soon_threadsafe(req.future.set_result, False)
            self._pending.clear()
