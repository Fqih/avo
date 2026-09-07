"""MCP server mode for Avo.

Expose an Avo :class:`avo.tools.ToolRegistry` over Model Context
Protocol (MCP) so external clients (Claude Desktop, Claude Code,
Cursor, etc.) can call Avo's tools through the standard MCP wire
format.

Usage from Python::

    from avo import ToolRegistry
    from avo.app_tools.file_tools import read_file, write_file
    from avo.mcp_server import serve_stdio

    registry = ToolRegistry([read_file, write_file])
    serve_stdio(registry)

Usage from the CLI::

    avo serve-mcp

The CLI launches the same code path with a registry built from the
default application tools (file ops, shell via sandbox, git, etc.).
The server speaks JSON-RPC 2.0 over stdio with ``Content-Length``
framing — matching the upstream MCP spec so off-the-shelf clients
connect without modification.
"""

from __future__ import annotations

from collections.abc import Callable

from avo.mcp_server.server import AvoMcpServer
from avo.tools import ToolRegistry

__all__ = ["AvoMcpServer", "ToolRegistry", "serve_stdio", "serve_stdio_async"]


def serve_stdio(
    registry: ToolRegistry,
    *,
    server_name: str = "avo",
    server_version: str = "0.1.3",
    read_fn: Callable[[int], bytes] | None = None,
    write_fn: Callable[[bytes], int] | None = None,
) -> None:
    """Run the MCP server on stdio until the client disconnects.

    Blocking entry point. ``read_fn`` and ``write_fn`` are injectable
    so tests can drive the server without touching real stdio.
    """

    import asyncio

    server = AvoMcpServer(
        registry=registry,
        server_name=server_name,
        server_version=server_version,
    )
    asyncio.run(server.serve_stdio(read_fn=read_fn, write_fn=write_fn))


async def serve_stdio_async(
    registry: ToolRegistry,
    *,
    server_name: str = "avo",
    server_version: str = "0.1.3",
    read_fn: Callable[[int], bytes] | None = None,
    write_fn: Callable[[bytes], int] | None = None,
) -> None:
    """Awaitable variant of :func:`serve_stdio`."""

    server = AvoMcpServer(
        registry=registry,
        server_name=server_name,
        server_version=server_version,
    )
    await server.serve_stdio(read_fn=read_fn, write_fn=write_fn)


def build_default_registry() -> ToolRegistry:
    """Build the default application tool registry shipped with Avo.

    Lazy import so importing :mod:`avo.mcp_server` does not pull every
    application tool by side effect.
    """

    from avo.app_tools.edit_file import edit_file_tool
    from avo.app_tools.file_tools import read_file_tool, write_file_tool
    from avo.app_tools.git_status import git_status_tool
    from avo.app_tools.glob_tool import glob_tool
    from avo.app_tools.grep_tool import grep_tool

    return ToolRegistry(
        [
            read_file_tool(),
            write_file_tool(),
            edit_file_tool(),
            glob_tool(),
            grep_tool(),
            git_status_tool(),
        ]
    )
