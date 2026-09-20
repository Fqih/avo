"""Manager for discovering, registering, and routing calls to external MCP servers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from avo import FunctionTool as PublicFunctionTool

from .models import McpServerConfig
from .stdio_client import McpStdioClient


class McpGenericArguments(BaseModel):
    """Dynamic arguments container for MCP tools."""

    model_config = {"extra": "allow"}


class McpClientManager:
    """Manages lifecycles of multiple MCP server clients and exposes their tools."""

    def __init__(self, config: dict[str, McpServerConfig] | None = None) -> None:
        self.config = config or {}
        self._clients: dict[str, McpStdioClient] = {}

    @classmethod
    def from_file(cls, path: str | Path) -> McpClientManager:
        """Load MCP servers configuration from a JSON file."""
        config_path = Path(path)
        if not config_path.is_file():
            return cls({})

        data = json.loads(config_path.read_text(encoding="utf-8"))
        servers_raw = data.get("mcpServers", data)
        config: dict[str, McpServerConfig] = {}
        for name, entry in servers_raw.items():
            if isinstance(entry, dict):
                config[name] = McpServerConfig.model_validate(entry)
        return cls(config)

    async def connect_all(self) -> None:
        """Start and initialize all enabled MCP clients."""
        for name, cfg in self.config.items():
            if cfg.disabled or name in self._clients:
                continue
            client = McpStdioClient(command=cfg.command, args=cfg.args, env=cfg.env)
            await client.connect()
            await client.initialize()
            self._clients[name] = client

    async def discover_tools(self) -> list[PublicFunctionTool[McpGenericArguments]]:
        """Connect to all servers and return wrapped FunctionTools for all tools."""
        await self.connect_all()
        tools: list[PublicFunctionTool[McpGenericArguments]] = []

        for server_name, client in self._clients.items():
            descriptors = await client.list_tools()
            for desc in descriptors:
                tool_name = f"mcp__{server_name}__{desc.name}"
                description = f"[{server_name}] {desc.description or desc.name}"

                # Closure capture
                def _make_caller(
                    s_client: McpStdioClient, original_name: str
                ) -> Callable[[McpGenericArguments], Awaitable[dict[str, Any]]]:
                    async def _caller(args: McpGenericArguments) -> dict[str, Any]:
                        return await s_client.call_tool(original_name, args.model_dump())

                    return _caller

                tool = PublicFunctionTool(
                    name=tool_name,
                    description=description,
                    arguments_model=McpGenericArguments,
                    function=_make_caller(client, desc.name),
                )
                tools.append(tool)

        return tools

    async def close(self) -> None:
        """Close all active MCP client connections."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
