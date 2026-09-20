"""Data models for MCP client configuration and tool representations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class McpServerConfig(BaseModel):
    """Configuration for launching an external MCP server."""

    command: str = Field(min_length=1, description="Executable command name or path")
    args: list[str] = Field(default_factory=list, description="Command-line arguments")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    disabled: bool = Field(default=False, description="Whether this server is disabled")


class McpToolDescriptor(BaseModel):
    """Normalized descriptor of an MCP tool returned by tools/list."""

    name: str = Field(min_length=1)
    description: str = Field(default="")
    input_schema: dict[str, Any] = Field(default_factory=dict)
