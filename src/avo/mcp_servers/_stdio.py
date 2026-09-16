"""JSON-RPC 2.0 over stdio for MCP servers.

The :class:`AvoMCPServer` helper does the boring half of MCP so each
server file only declares its tools. It speaks the same ``Content-Length``
framed JSON-RPC the official SDK uses, handles the ``initialize`` /
``notifications/initialized`` handshake, and replies to ``tools/list``
and ``tools/call`` based on a tool registry.

Two design choices worth flagging:

* ``tools/list`` returns every registered tool's name, description, and
  ``inputSchema``. Schemas are built from the tool callable's signature
  using Pydantic models the server module declares explicitly (we don't
  do runtime introspection — explicit schemas are easier to keep
  version-stable).
* ``tools/call`` dispatches by ``name`` into a ``Tool`` registry. Errors
  are returned as ``isError=True`` results, not transport errors, so the
  caller can keep its run state-machine consistent.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

# Loosely-typed dict for handler return values. Pydantic's `JsonValue`
# would be stricter, but tool payloads frequently contain ints, bools, and
# None alongside strings — `object` matches what handlers actually emit
# without forcing every implementation to opt into `Any` explicitly.
JsonDict = dict[str, object]


@dataclass(frozen=True)
class Tool:
    """One MCP tool the server exposes."""

    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: Callable[..., Coroutine[Any, Any, JsonDict]]


class AvoMCPServer:
    """Base class — subclass and call :meth:`run` from ``__main__``.

    Subclasses register tools via :meth:`tool`; ``run`` drives the
    JSON-RPC stdio loop until EOF.
    """

    def __init__(self, *, server_name: str, server_version: str = "0.1.0") -> None:
        self._name = server_name
        self._version = server_version
        self._tools: dict[str, Tool] = {}
        self._next_id = 1

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def tool(
        self,
        *,
        name: str,
        description: str,
        arguments_model: type[BaseModel],
    ) -> Callable[
        [Callable[..., Coroutine[Any, Any, JsonDict]]],
        Callable[..., Coroutine[Any, Any, JsonDict]],
    ]:
        """Decorator — register an async function as an MCP tool."""

        def decorator(
            handler: Callable[..., Coroutine[Any, Any, JsonDict]],
        ) -> Callable[..., Coroutine[Any, Any, JsonDict]]:
            if name in self._tools:
                raise ValueError(f"duplicate tool registration: {name!r}")
            self._tools[name] = Tool(
                name=name,
                description=description,
                arguments_model=arguments_model,
                handler=handler,
            )
            return handler

        return decorator

    def _schema_for(self, model: type[BaseModel]) -> JsonDict:
        """Render a Pydantic model as a JSON Schema fragment."""

        schema: JsonDict = model.model_json_schema()
        return schema

    # ------------------------------------------------------------------
    # MCP method dispatch
    # ------------------------------------------------------------------

    async def _handle(self, method: str, params: JsonDict) -> JsonDict:
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self._name, "version": self._version},
            }
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": self._schema_for(tool.arguments_model),
                    }
                    for tool in self._tools.values()
                ]
            }
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if name not in self._tools:
                return {
                    "content": [{"type": "text", "text": f"unknown tool: {name!r}"}],
                    "isError": True,
                }
            entry = self._tools[name]
            try:
                model = entry.arguments_model.model_validate(arguments)
                result = await entry.handler(model)
            except _UserFacingError as exc:
                return {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                }
            except Exception as exc:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"{type(exc).__name__}: {exc}",
                        }
                    ],
                    "isError": True,
                }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, sort_keys=True, default=str),
                    }
                ],
                "isError": False,
            }
        if method == "ping":
            return {}
        raise _UnknownMethodError(method)

    # ------------------------------------------------------------------
    # Stdio transport
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        stdin: asyncio.StreamReader | None = None,
        stdout: asyncio.StreamWriter | None = None,
    ) -> None:
        """Serve forever on stdio until EOF or fatal transport error.

        Uses a background thread to read ``sys.stdin.buffer`` because
        ``asyncio.connect_read_pipe`` raises ``PermissionError`` on some
        sandboxes where the stdin file descriptor is not selectable
        (e.g. when stdin is redirected from a process substitution or
        when the file descriptor is associated with a terminal in a
        way the event loop refuses). Threading bypasses the selector.
        """

        loop = asyncio.get_running_loop()
        in_queue: asyncio.Queue[object] = asyncio.Queue()
        # Use a unique sentinel object so EOF doesn't collide with
        # legitimate zero-length reads (``read1`` may return ``b""``
        # without EOF, so we cannot use empty bytes as the marker).
        sentinel = object()

        if stdin is None:
            import threading
            from typing import cast

            stdin_buffer = cast(Any, sys.stdin.buffer)

            def _drain_stdin() -> None:
                try:
                    while True:
                        chunk = stdin_buffer.read1(4096)
                        if not chunk:
                            loop.call_soon_threadsafe(in_queue.put_nowait, sentinel)
                            return
                        loop.call_soon_threadsafe(in_queue.put_nowait, chunk)
                except Exception:
                    loop.call_soon_threadsafe(in_queue.put_nowait, sentinel)

            thread = threading.Thread(target=_drain_stdin, daemon=True)
            thread.start()
        else:
            # Caller pre-bound a reader; feed bytes into the same queue.
            pass

        buf = bytearray()

        async def _read_envelope_threaded() -> JsonDict | None:
            while True:
                if buf:
                    # Try to parse what we have first; if a complete
                    # envelope is buffered, return it without blocking.
                    parsed = _try_parse_envelope(bytes(buf))
                    if parsed is not None:
                        del buf[: parsed[1]]
                        return parsed[0]
                item = await in_queue.get()
                if item is sentinel:
                    return None
                assert isinstance(item, (bytes, bytearray))
                buf.extend(item)

        out_stream = _SyncStdoutWriter() if stdout is None else stdout

        while True:
            envelope = await _read_envelope_threaded()
            if envelope is None:
                return
            request_id = envelope.get("id")
            method = str(envelope.get("method") or "")
            params = envelope.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            if request_id is None:
                # Notifications must not receive a response per JSON-RPC 2.0.
                continue
            try:
                result = await self._handle(method, params)
                response: JsonDict = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except _UnknownMethodError:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
                }
            await _write_envelope(out_stream, response)


def _try_parse_envelope(buf: bytes) -> tuple[JsonDict, int] | None:
    """Return ``(envelope, bytes_consumed)`` if ``buf`` has a full envelope."""

    sep = buf.find(b"\r\n\r\n")
    if sep == -1:
        return None
    header = buf[:sep].decode("ascii", errors="replace").lower()
    content_length = 0
    for line in header.splitlines():
        if line.startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
            break
    if content_length <= 0:
        return None
    total = sep + 4 + content_length
    if len(buf) < total:
        return None
    import json as _json

    try:
        envelope = _json.loads(buf[sep + 4 : total].decode("utf-8"))
    except _json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    return envelope, total


class _SyncStdoutWriter:
    """File-like sink that ``_write_envelope`` can drain synchronously.

    ``_write_envelope`` writes to ``stream.write`` and awaits ``drain``;
    ``_SyncStdoutWriter`` implements both with a thread-safe
    ``sys.stdout.buffer.write`` + flush.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._lock:
            sys.stdout.buffer.write(data)

    async def drain(self) -> None:
        with self._lock:
            sys.stdout.buffer.flush()


class _UnknownMethodError(Exception):
    pass


class _UserFacingError(Exception):
    """Raised inside tool handlers to send a clean error to the client."""


async def _read_envelope(stream: asyncio.StreamReader) -> JsonDict | None:
    content_length = 0
    while True:
        line = await stream.readline()
        if not line:
            return None
        stripped = line.strip()
        if not stripped:
            if content_length:
                break
            continue
        if stripped.lower().startswith(b"content-length:"):
            try:
                content_length = int(stripped.split(b":", 1)[1].strip())
            except ValueError as exc:
                raise _UserFacingError(f"bad content-length header: {stripped!r}") from exc
    if content_length <= 0:
        return None
    body = await stream.readexactly(content_length)
    try:
        loaded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _UserFacingError(f"invalid JSON envelope: {exc}") from exc
    if not isinstance(loaded, dict):
        raise _UserFacingError("envelope must be a JSON object")
    return loaded


class _EnvelopeSink(Protocol):
    """Duck-typed envelope sink — anything with sync write + async drain."""

    def write(self, data: bytes) -> object: ...

    async def drain(self) -> object: ...


async def _write_envelope(stream: _EnvelopeSink, envelope: JsonDict) -> None:
    body = json.dumps(envelope).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header + body)
    await stream.drain()


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def user_error(message: str) -> None:
    """Raise :class:`_UserFacingError` so the MCP response is ``isError=True``."""

    raise _UserFacingError(message)


__all__ = [
    "AvoMCPServer",
    "Tool",
    "user_error",
]
