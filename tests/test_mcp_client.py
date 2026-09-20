"""Tests for Dynamic Model Context Protocol (MCP) Client Hub."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from avo.mcp_client.manager import McpClientManager
from avo.mcp_client.models import McpServerConfig
from avo.mcp_client.stdio_client import McpStdioClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


@pytest.mark.asyncio
async def test_mcp_stdio_client_lifecycle() -> None:
    client = McpStdioClient(
        command=sys.executable,
        args=[str(FIXTURE_PATH)],
    )

    await client.connect()
    try:
        init_res = await client.initialize()
        assert init_res.get("serverInfo", {}).get("name") == "fake"

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"
        assert tools[0].description == "Echo args"

        call_res = await client.call_tool("echo", {"x": "avo-mcp-test"})
        assert call_res.get("isError") is False
        assert len(call_res.get("content", [])) > 0
        assert "avo-mcp-test" in call_res["content"][0]["text"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_client_manager_discovers_and_builds_tools() -> None:
    config = {
        "fake_server": McpServerConfig(
            command=sys.executable,
            args=[str(FIXTURE_PATH)],
        )
    }

    manager = McpClientManager(config=config)
    try:
        tools = await manager.discover_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.metadata.name == "mcp__fake_server__echo"
        assert "Echo args" in tool.metadata.description

        # Test tool invocation as FunctionTool
        result: Any = await tool.invoke({"x": "hello-via-manager"})
        assert result["isError"] is False
        assert "hello-via-manager" in result["content"][0]["text"]
    finally:
        await manager.close()
