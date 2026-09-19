"""Web approval bridge for human-in-the-loop tool execution via HTTP/Web Cockpit."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
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


class WebApprovalBridge:
    """Coordinates tool approvals between running agents and the Web Cockpit."""

    def __init__(self, default_timeout_seconds: float = 300.0) -> None:
        self.default_timeout_seconds = default_timeout_seconds
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

        req = PendingApproval(
            request_id=request_id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments if isinstance(tool_call.arguments, dict) else {},
            created_at=datetime.now(UTC),
            future=future,
            run_id=run_id,
            timeout_seconds=self.default_timeout_seconds,
        )

        with self._lock:
            self._pending[request_id] = req

        try:
            return await asyncio.wait_for(future, timeout=self.default_timeout_seconds)
        except TimeoutError:
            return False
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def list_pending(self) -> list[dict[str, Any]]:
        """Return all active pending approval requests."""
        with self._lock:
            return [
                {
                    "request_id": req.request_id,
                    "run_id": req.run_id,
                    "tool_name": req.tool_name,
                    "arguments": req.arguments,
                    "created_at": req.created_at.isoformat(),
                    "timeout_seconds": req.timeout_seconds,
                }
                for req in self._pending.values()
            ]

    def resolve(self, request_id: str, approved: bool, reason: str = "") -> bool:
        """Resolve a pending approval request. Return True if found and resolved."""
        del reason
        with self._lock:
            req = self._pending.get(request_id)
            if not req:
                return False

            if not req.future.done():
                try:
                    req.future.get_loop().call_soon_threadsafe(req.future.set_result, approved)
                except RuntimeError:
                    return False
            return True

    def clear(self) -> None:
        """Cancel and clear all pending approvals."""
        with self._lock:
            for req in self._pending.values():
                if not req.future.done():
                    with contextlib.suppress(RuntimeError):
                        req.future.get_loop().call_soon_threadsafe(req.future.set_result, False)
            self._pending.clear()
