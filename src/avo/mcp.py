"""Minimal Model Context Protocol (MCP) client adapter.

MCP speaks JSON-RPC 2.0 over a stdio transport. This module launches a
server subprocess (``python -m my_mcp_server`` or any ``cmd``), performs
the ``initialize`` handshake, discovers ``tools/list``, and wraps each
remote tool as a Avo :class:`FunctionTool` so the runtime can
invoke it without knowing about MCP.

The transport is intentionally minimal:

* one persistent subprocess per server
* line-delimited JSON-RPC framed by ``Content-Length``
* a single request id counter, async gather via futures dict

``mcp`` is an optional extra (``pip install avo[mcp]``); this
module has no hard runtime dependency — it speaks JSON-RPC directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, JsonValue, model_validator

from avo import FunctionTool as PublicFunctionTool
from avo.exceptions import ToolExecutionError

MCPError = ToolExecutionError

JsonDict = dict[str, JsonValue]


@dataclass
class _Pending:
    future: asyncio.Future[JsonDict]
    method: str


class MCPServer:
    """A single MCP server subprocess."""

    def __init__(
        self,
        *,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: Path | str | None = None,
    ) -> None:
        if not command:
            raise MCPError("MCPServer requires a non-empty command")
        self._command = command
        self._env = env if env is not None else dict(os.environ)
        self._cwd = str(cwd) if cwd is not None else None
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._server_info: JsonDict = {}
        self._tools: list[JsonDict] = []

    @property
    def server_info(self) -> JsonDict:
        return dict(self._server_info)

    @property
    def tools(self) -> tuple[JsonDict, ...]:
        return tuple(self._tools)

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self._initialize()
        await self._list_tools()

    async def stop(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()

    async def call_tool(self, name: str, arguments: JsonDict) -> JsonDict:
        return await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    async def _initialize(self) -> None:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "avo", "version": "0.1.0"},
            },
        )
        info = result.get("serverInfo")
        self._server_info = info if isinstance(info, dict) else {}
        await self._notify("notifications/initialized", {})

    async def _list_tools(self) -> None:
        result = await self._request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise MCPError("tools/list did not return a list")
        self._tools = [tool for tool in tools if isinstance(tool, dict)]

    async def _request(self, method: str, params: JsonDict) -> JsonDict:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise MCPError("server is not running")
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[JsonDict] = loop.create_future()
        self._pending[request_id] = _Pending(future=future, method=method)
        envelope = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        await self._send(envelope)
        # 90s covers spawning the server on a cold CI runner: every
        # stdio spawn re-imports the full avo package, and the first
        # (initialize) request therefore waits on interpreter startup.
        return await asyncio.wait_for(future, timeout=90.0)

    async def _notify(self, method: str, params: JsonDict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("server is not running")
        envelope = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._send(envelope)

    async def _send(self, envelope: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("server is not running")
        body = json.dumps(envelope).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._proc.stdin.write(header + body)
            await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            raise MCPError(f"MCP transport closed: {exc}") from exc

    async def _read_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        try:
            await self._read_messages(self._proc.stdout)
        except (asyncio.IncompleteReadError, MCPError, ValueError):
            pass  # EOF, malformed framing — treated as connection loss below
        finally:
            self._fail_pending(MCPError("MCP server closed its stdout (process exited?)"))

    async def _read_messages(self, stdout: asyncio.StreamReader) -> None:
        while True:
            content_length = await _read_header(stdout)
            body = await stdout.readexactly(content_length)
            try:
                envelope = json.loads(body)
            except json.JSONDecodeError:
                continue
            request_id = envelope.get("id")
            if not isinstance(request_id, int):
                continue  # notification, ignore
            pending = self._pending.pop(request_id, None)
            if pending is None:
                continue
            if "error" in envelope and envelope["error"] is not None:
                pending.future.set_exception(
                    MCPError(f"MCP error for {pending.method}: {envelope['error']}")
                )
            else:
                result = envelope.get("result") or {}
                if not isinstance(result, dict):
                    result = {"value": result}
                pending.future.set_result(result)

    def _fail_pending(self, error: MCPError) -> None:
        """Wake every outstanding request when the connection is dead.

        Without this, a server that dies leaves :meth:`_request` waiting
        its full timeout budget instead of failing immediately.
        """

        for request_id in list(self._pending):
            pending = self._pending.pop(request_id)
            if not pending.future.done():
                pending.future.set_exception(error)

    async def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        while True:
            try:
                chunk = await self._proc.stderr.readline()
            except ValueError:
                return
            if not chunk:
                return
            # stderr is intentionally discarded — MCP servers use stderr
            # for diagnostics, never protocol. Future hooks may pipe
            # this into a logger.
            _ = chunk


async def _read_header(stream: asyncio.StreamReader) -> int:
    content_length = 0
    while True:
        line = await stream.readline()
        if not line:
            raise asyncio.IncompleteReadError(b"", 0)
        stripped = line.strip()
        if not stripped:
            break
        if stripped.lower().startswith(b"content-length:"):
            try:
                content_length = int(stripped.split(b":", 1)[1].strip())
            except ValueError as exc:
                raise MCPError(f"bad content-length header: {stripped!r}") from exc
    return content_length


# ---------------------------------------------------------------------------
# Adapter — wrap MCP tools as Avo FunctionTools.
# ---------------------------------------------------------------------------


class _ArgumentsModel(BaseModel):
    """Permissive arguments model — every tool has its own schema."""

    model_config = {"extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def _strip_none(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if v is not None}
        return value


def _build_arguments_model(schema: JsonDict) -> type[BaseModel]:
    """Construct a permissive Pydantic model that still rejects extra None values."""

    title = schema.get("title")
    name = title if isinstance(title, str) and title else "MCPToolArguments"
    return cast(type[BaseModel], type(name, (_ArgumentsModel,), {"__annotations__": {}}))


def mcp_tool(server: MCPServer, descriptor: JsonDict) -> PublicFunctionTool[Any]:
    """Wrap one MCP tool descriptor as a Avo :class:`FunctionTool`."""

    name = str(descriptor.get("name") or "")
    if not name:
        raise MCPError("MCP tool descriptor missing name")
    description = str(descriptor.get("description") or "")
    schema = descriptor.get("inputSchema") or {}
    if not isinstance(schema, dict):
        schema = {}
    arguments_model = _build_arguments_model(schema)

    async def _invoke(arguments: BaseModel) -> dict[str, JsonValue]:
        payload = arguments.model_dump(exclude_none=True)
        result = await server.call_tool(name, payload)
        content = result.get("content")
        is_error = bool(result.get("isError"))
        payload_out: dict[str, JsonValue] = {
            "name": name,
            "is_error": is_error,
            "result": result,
        }
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                payload_out["text"] = first.get("text")
        return payload_out

    return PublicFunctionTool(
        name=name,
        description=description or f"MCP tool {name}",
        arguments_model=arguments_model,
        function=_invoke,
    )


def mcp_tools(server: MCPServer) -> list[PublicFunctionTool[Any]]:
    """Wrap every tool exposed by ``server`` as a Avo tool."""

    return [mcp_tool(server, descriptor) for descriptor in server.tools]


@dataclass
class MCPClient:
    """High-level client — manages one :class:`MCPServer` lifecycle."""

    server: MCPServer
    started: bool = False

    async def __aenter__(self) -> MCPClient:
        await self.server.start()
        self.started = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.server.stop()

    def tools(self) -> list[PublicFunctionTool[Any]]:
        if not self.started:
            raise MCPError("MCPClient used outside `async with` block")
        return mcp_tools(self.server)


__all__ = [
    "MCPClient",
    "MCPError",
    "MCPServer",
    "mcp_tool",
    "mcp_tools",
]
