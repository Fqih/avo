"""Capability metadata and narrowing helpers for tools and child agents."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from avo.agent_profiles import AgentCapability

if TYPE_CHECKING:
    from avo.config_resolver import AvoSecurityConfig, ResolvedValue
    from avo.tools import Tool

T = TypeVar("T")


class ToolCapability(StrEnum):
    """Risk boundary assigned to one registered tool."""

    READ = "read"
    MUTATE = "mutate"
    EXECUTE = "execute"
    NETWORK = "network"


_CAPABILITY_ORDER: dict[ToolCapability, int] = {
    ToolCapability.READ: 0,
    ToolCapability.MUTATE: 1,
    ToolCapability.EXECUTE: 2,
    ToolCapability.NETWORK: 3,
}

_MUTATING_NAMES = frozenset({"write_file", "edit_file", "batch_replace", "git_commit"})
_EXECUTABLE_NAMES = frozenset({"run_shell", "run_terminal", "test_runner", "lint"})
_NETWORK_NAMES = frozenset({"web_fetch", "web_search", "http_fetch"})


def classify_tool(name: str) -> ToolCapability:
    """Classify a tool by its stable built-in name.

    Unknown and third-party tools are intentionally read-only until a caller
    opts them into a higher capability by constructing metadata explicitly.
    """

    if name in _MUTATING_NAMES:
        return ToolCapability.MUTATE
    if name in _EXECUTABLE_NAMES:
        return ToolCapability.EXECUTE
    if name in _NETWORK_NAMES or name.startswith("mcp_http"):
        return ToolCapability.NETWORK
    return ToolCapability.READ


def filter_tools(
    parent_tools: Iterable[Tool],
    *,
    maximum: ToolCapability | None = None,
    read_only: bool = False,
) -> list[Tool]:
    """Return a subset of ``parent_tools`` without widening capabilities."""

    if read_only:
        maximum = ToolCapability.READ
    limit = _CAPABILITY_ORDER[maximum] if maximum is not None else None
    selected: list[Tool] = []
    for tool in parent_tools:
        capability = tool.metadata.capability
        if limit is None or _CAPABILITY_ORDER[capability] <= limit:
            selected.append(tool)
    return selected


def _replace_value(resolved: ResolvedValue[T], value: T) -> ResolvedValue[T]:
    return replace(resolved, value=value)


def inherit_policy(
    parent: AvoSecurityConfig,
    child_capability: AgentCapability,
) -> AvoSecurityConfig:
    """Return a child policy that never broadens the parent boundary."""

    if child_capability is AgentCapability.INHERITED:
        return parent
    return replace(
        parent,
        sandbox_required=_replace_value(parent.sandbox_required, True),
        sandbox_network=_replace_value(parent.sandbox_network, False),
        plugin_editable=_replace_value(parent.plugin_editable, False),
        plugin_activation=_replace_value(parent.plugin_activation, False),
        web_cors_enabled=_replace_value(parent.web_cors_enabled, False),
    )


def inherit_runtime_security(
    parent: AvoSecurityConfig | None,
    child_capability: AgentCapability,
) -> AvoSecurityConfig:
    """Return a policy for a child runtime without widening its boundary.

    A runtime created directly by a library caller may not have a resolved
    security config. Delegated runtimes still receive the resolver defaults in
    that case, so the absence of a parent config cannot become host execution.
    """

    if parent is not None:
        return inherit_policy(parent, child_capability)

    from avo.config_resolver import resolve_security_config

    return resolve_security_config(
        environ={},
        user_root=Path("/__avo_no_user_config__"),
    )


__all__ = [
    "ToolCapability",
    "classify_tool",
    "filter_tools",
    "inherit_policy",
    "inherit_runtime_security",
]
