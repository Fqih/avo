"""Avo MCP server implementation.

Speaks the MCP JSON-RPC lifecycle (``initialize``/``shutdown``/``exit``,
handshake gating with ``-32002`` for pre-init or post-shutdown requests)
and the tool methods (``tools/list`` with derived annotations,
``tools/call``). When a ``workspace_root`` is configured it also serves
the resource methods (``resources/list``, ``resources/read``,
``resources/templates/list``) over an ``avo://workspace/`` URI scheme
validated through :class:`avo.app_tools.workspace.Workspace`; without one
the resource collections are simply empty. Prompts land in the same
release line. The server is transport-agnostic — tests inject
read/write callables; the CLI hands it the real ``sys.stdin.buffer`` /
``sys.stdout.buffer``.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from avo.app_tools.approval import required_tool_names
from avo.app_tools.workspace import Workspace, WorkspacePathError
from avo.exceptions import ToolAlreadyCompletedError
from avo.mcp_server.framing import FramingError, encode_message, iter_messages
from avo.models import ToolCall
from avo.tools import ToolRegistry

_LOG = logging.getLogger("avo.mcp_server")

# Upper bound on files surfaced by ``resources/list`` so a runaway
# workspace walk cannot dominate the response.
_RESOURCE_LIMIT = 200

# URI scheme prefix for workspace resources.
_RESOURCE_PREFIX = "avo://workspace/"


def _guess_mime(path: Path) -> str:
    """Best-effort MIME type for a workspace file."""

    return mimetypes.guess_type(path.name)[0] or "text/plain"


# Tools whose names are known read-only regardless of prefix convention.
_READ_ONLY_TOOLS = frozenset({"read_file", "glob", "grep", "git_status"})


def _tool_annotations(name: str) -> dict[str, bool]:
    """Derive MCP tool annotations from the tool name + approval policy.

    Read-only tools (fixed set or ``read_`` prefix) are safe and
    idempotent; tools listed in ``AVO_TOOLS_REQUIRE_APPROVAL`` are
    treated as destructive/open-world; everything else is a plain
    mutation hint set.
    """

    if name in _READ_ONLY_TOOLS or name.startswith("read_"):
        return {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    if name in required_tool_names():
        return {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    return {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }


class AvoMcpServer:
    """Expose a :class:`ToolRegistry` over the MCP wire protocol."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        server_name: str = "avo",
        server_version: str = "0.1.3",
        workspace_root: Path | str | None = None,
    ) -> None:
        self._registry = registry
        self._server_name = server_name
        self._server_version = server_version
        self._workspace = Workspace(workspace_root) if workspace_root is not None else None
        self._initialised = False
        self._shutting_down = False

    @property
    def server_info(self) -> dict[str, Any]:
        """Return the ``serverInfo`` block advertised to MCP clients."""

        return {"name": self._server_name, "version": self._server_version}

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC message; return the response payload or ``None``."""

        if "jsonrpc" not in message or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "invalid JSON-RPC 2.0 envelope")

        request_id = message.get("id")
        method = message.get("method", "")
        params = message.get("params") or {}

        # Notifications (no id) get a fire-and-forget handler.
        if "id" not in message:
            await self._handle_notification(method, params)
            return None

        try:
            result = await self._dispatch(method, params)
        except _MethodError as exc:
            return _error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            _LOG.exception("MCP method %r failed", method)
            return _error(request_id, -32603, f"internal error: {exc!s}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        """Notifications carry no id and require no response."""

        if method == "notifications/initialized":
            return
        if method == "exit":
            raise _ExitSignal
        # Unknown notifications are silently ignored per the spec.
        _LOG.debug("ignoring notification %r", method)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Route a request to the matching MCP method handler.

        Lifecycle gating: ``initialize`` and ``ping`` work pre-handshake;
        every other request before ``initialize`` (and everything except
        the exempt methods after ``shutdown``) fails with ``-32002``.
        """

        if method == "initialize":
            return await self._initialize(params)
        if method == "ping":
            return {}
        if method == "shutdown":
            if not self._initialised:
                raise _MethodError(-32002, "server not initialized")
            return self._shutdown()
        if not self._initialised:
            raise _MethodError(-32002, "server not initialized")
        if self._shutting_down:
            raise _MethodError(-32002, "server is shutting down")
        if method == "tools/list":
            return await self._tools_list(params)
        if method == "tools/call":
            return await self._tools_call(params)
        if method == "resources/list":
            return await self._resources_list(params)
        if method == "resources/read":
            return await self._resources_read(params)
        if method == "resources/templates/list":
            return await self._resources_templates_list(params)
        raise _MethodError(-32601, f"method not found: {method!r}")

    async def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        self._initialised = True
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
                "logging": {},
                "completions": {},
            },
            "serverInfo": self.server_info,
        }

    def _shutdown(self) -> dict[str, Any]:
        """Mark the server as shutting down; replay is tolerated."""

        self._shutting_down = True
        return {}

    async def _tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        del params  # pagination not implemented; the default registry fits in one page
        tools = []
        for metadata in self._registry.metadata:
            tools.append(
                {
                    "name": metadata.name,
                    "description": metadata.description,
                    "inputSchema": metadata.input_schema,
                    "annotations": _tool_annotations(metadata.name),
                }
            )
        return {"tools": tools}

    async def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            raise _MethodError(-32602, "params.name must be a string")
        if not isinstance(arguments, dict):
            raise _MethodError(-32602, "params.arguments must be an object")
        call = ToolCall(name=name, arguments=arguments)
        try:
            result = await self._registry.invoke(call, completed_tool_call_ids=set())
        except ToolAlreadyCompletedError as exc:
            raise _MethodError(-32000, str(exc)) from exc
        if not result.success:
            return {
                "content": [{"type": "text", "text": result.error or "tool call failed"}],
                "isError": True,
            }
        return _tool_result_payload(result.output)

    async def _resources_list(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self._workspace is None:
            return {"resources": []}
        resources: list[dict[str, Any]] = []
        root = self._workspace.root
        for path in sorted(root.rglob("*")):
            if len(resources) >= _RESOURCE_LIMIT:
                break
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            rel_str = rel.as_posix()
            resources.append(
                {
                    "uri": f"{_RESOURCE_PREFIX}{rel_str}",
                    "name": rel_str,
                    "mimeType": _guess_mime(path),
                }
            )
        return {"resources": resources}

    async def _resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise _MethodError(-32602, "params.uri must be a string")
        if not uri.startswith(_RESOURCE_PREFIX):
            raise _MethodError(
                -32602, f"invalid resource URI scheme, expected prefix {_RESOURCE_PREFIX!r}"
            )
        if self._workspace is None:
            raise _MethodError(-32000, "workspace not configured on server")

        rel_path = uri[len(_RESOURCE_PREFIX) :]
        try:
            resolved = self._workspace.validate_path(rel_path, must_exist=True)
        except WorkspacePathError as exc:
            raise _MethodError(-32000, str(exc)) from exc

        try:
            data = resolved.read_bytes()
        except OSError as exc:
            raise _MethodError(-32000, str(exc)) from exc

        if b"\x00" in data:
            raise _MethodError(-32000, "cannot read binary resource as text")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _MethodError(-32000, f"cannot decode binary resource as utf-8: {exc}") from exc

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": _guess_mime(resolved),
                    "text": text,
                }
            ]
        }

    async def _resources_templates_list(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self._workspace is None:
            return {"resourceTemplates": []}
        return {
            "resourceTemplates": [
                {
                    "uriTemplate": f"{_RESOURCE_PREFIX}{{path}}",
                    "name": "Workspace file",
                    "description": "Read a file from the workspace",
                    "mimeType": "text/plain",
                }
            ]
        }

    # ------------------------------------------------------------------
    # Transport loop
    # ------------------------------------------------------------------

    async def serve_stdio(
        self,
        *,
        read_fn: Callable[[int], bytes] | None = None,
        write_fn: Callable[[bytes], int] | None = None,
    ) -> None:
        """Run the server loop until EOF or an ``exit`` notification."""

        stream = _make_input_stream(read_fn)
        writer = _make_writer(write_fn)

        try:
            for message in iter_messages(stream):
                payload = message["payload"]
                try:
                    response = await self.handle_message(payload)
                except _ExitSignal:
                    return
                if response is not None:
                    writer(encode_message(response))
        except FramingError:
            _LOG.exception("malformed MCP frame")
            return

    def run_forever(
        self,
        *,
        read_fn: Callable[[int], bytes] | None = None,
        write_fn: Callable[[bytes], int] | None = None,
    ) -> None:
        """Synchronous entry point — convenience for the CLI."""

        asyncio.run(self.serve_stdio(read_fn=read_fn, write_fn=write_fn))


def _make_input_stream(
    read_fn: Callable[[int], bytes] | None,
) -> Any:
    """Build a byte iterator that drains ``read_fn`` until EOF.

    Wraps the blocking ``read_fn`` in a generator so the rest of the
    server loop can stream messages through ``iter_messages`` without
    bespoke buffering logic.
    """

    if read_fn is None:
        return _stdin_chunks()
    return _callable_chunks(read_fn)


def _stdin_chunks() -> Any:
    """Yield bytes from stdin in 4096-byte chunks until EOF."""

    while True:
        chunk = sys.stdin.buffer.read(4096)
        if not chunk:
            return
        yield chunk


def _callable_chunks(read_fn: Callable[[int], bytes]) -> Any:
    """Yield bytes from a user-supplied blocking reader until EOF."""

    while True:
        chunk = read_fn(4096)
        if not chunk:
            return
        yield chunk


def _make_writer(write_fn: Callable[[bytes], int] | None) -> Callable[[bytes], None]:
    """Return a stdout-flushing writer or the test-injected one."""

    def _default_write(data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    if write_fn is None:
        return _default_write
    return lambda data: (write_fn(data), None)[1]


def _tool_result_payload(result: Any) -> dict[str, Any]:
    """Wrap a tool result in the MCP ``content`` envelope."""

    import json

    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(result)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error envelope."""

    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


class _MethodError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class _ExitSignal(Exception):
    """Internal sentinel raised when the client sends ``exit``."""
