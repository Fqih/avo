"""Stdio JSON-RPC client transport for Model Context Protocol (MCP) servers."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from avo.exceptions import AvoError

from .models import McpToolDescriptor


class McpClientError(AvoError):
    """Raised when an MCP client protocol or transport failure occurs."""


class McpStdioClient:
    """Async client managing an MCP server subprocess communicating via stdio."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    async def connect(self) -> None:
        """Spawn the child process and start reading responses."""
        merged_env = {**os.environ, **self.env}
        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        self._reader_task = asyncio.create_task(self._read_loop(), name="mcp-stdio-reader")

    async def _read_loop(self) -> None:
        """Background loop reading JSON-RPC messages from server stdout."""
        if not self._process or not self._process.stdout:
            return

        stdout = self._process.stdout
        while not self._closed:
            try:
                # Read headers (Content-Length: N\r\n\r\n) or newline-delimited JSON
                first_line = await stdout.readline()
                if not first_line:
                    break

                line_str = first_line.decode("utf-8", errors="replace")
                if line_str.lower().startswith("content-length:"):
                    content_length = int(line_str.split(":", 1)[1].strip())
                    # Consume empty line separator
                    empty_line = await stdout.readline()
                    del empty_line
                    body = await stdout.readexactly(content_length)
                    msg = json.loads(body.decode("utf-8"))
                else:
                    # Fallback: newline delimited JSON
                    trimmed = line_str.strip()
                    if not trimmed:
                        continue
                    msg = json.loads(trimmed)

                req_id = msg.get("id")
                if req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                break
            except Exception as exc:
                if self._closed:
                    break
                # Fail all pending futures on transport crash
                for fut in self._pending_requests.values():
                    if not fut.done():
                        fut.set_exception(exc)
                self._pending_requests.clear()
                break

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and await response."""
        if not self._process or not self._process.stdin:
            raise McpClientError("Client is not connected to a subprocess.")

        req_id = self._next_id
        self._next_id += 1

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        full_packet = header + body

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[req_id] = future

        self._process.stdin.write(full_packet)
        await self._process.stdin.drain()

        response = await future
        if "error" in response:
            err = response["error"]
            code = err.get("code")
            msg = err.get("message", "Unknown error")
            raise McpClientError(f"MCP server error ({code}): {msg}")

        result: dict[str, Any] = response.get("result", {})
        return result

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    async def initialize(self) -> dict[str, Any]:
        """Perform MCP initialize handshake."""
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "avo-mcp-client", "version": "0.7.3"},
            },
        )
        await self._send_notification("notifications/initialized")
        return result

    async def list_tools(self) -> list[McpToolDescriptor]:
        """Query available tools from the server via tools/list."""
        res = await self._send_request("tools/list", {})
        raw_tools = res.get("tools", [])
        return [
            McpToolDescriptor(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in raw_tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke an MCP tool by name with arguments via tools/call."""
        return await self._send_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )

    async def close(self) -> None:
        """Terminate the server process and cleanup resources."""
        self._closed = True
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()

        if self._process:
            try:
                if self._process.stdin and not self._process.stdin.is_closing():
                    self._process.stdin.close()
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except (ProcessLookupError, TimeoutError):
                self._process.kill()
            self._process = None
