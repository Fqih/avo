"""Tests for capability classification and child-policy narrowing."""

from __future__ import annotations

from pathlib import Path

from avo.agent_profiles import AgentCapability
from avo.capabilities import (
    ToolCapability,
    classify_tool,
    filter_tools,
    inherit_policy,
)
from avo.config_resolver import resolve_security_config
from avo.models import ToolMetadata
from avo.tools import FunctionTool
from tests.helpers import ValueArguments, value_tool


def test_builtin_tool_names_have_stable_capabilities() -> None:
    assert classify_tool("read_file") is ToolCapability.READ
    assert classify_tool("git_diff") is ToolCapability.READ
    assert classify_tool("write_file") is ToolCapability.MUTATE
    assert classify_tool("git_commit") is ToolCapability.MUTATE
    assert classify_tool("run_shell") is ToolCapability.EXECUTE
    assert classify_tool("run_terminal") is ToolCapability.EXECUTE
    assert classify_tool("web_fetch") is ToolCapability.NETWORK


def test_unknown_tools_default_to_read() -> None:
    metadata = ToolMetadata(name="vendor_tool", description="vendor", input_schema={})
    assert metadata.capability is ToolCapability.READ
    assert classify_tool("vendor_tool") is ToolCapability.READ


def test_function_tool_infers_capability_without_breaking_custom_tools() -> None:
    tool = FunctionTool(
        name="write_file",
        description="write",
        arguments_model=ValueArguments,
        function=lambda _args: {"ok": True},
    )
    assert tool.metadata.capability is ToolCapability.MUTATE


def test_read_only_filter_cannot_advertise_higher_capabilities() -> None:
    tools = [
        value_tool(name="read_file"),
        value_tool(name="write_file"),
        value_tool(name="run_shell"),
        value_tool(name="web_fetch"),
    ]
    assert [tool.metadata.name for tool in filter_tools(tools, read_only=True)] == ["read_file"]
    assert [tool.metadata.name for tool in filter_tools(tools, maximum=ToolCapability.MUTATE)] == [
        "read_file",
        "write_file",
    ]


def test_child_policy_can_only_become_more_restrictive(tmp_path: Path) -> None:
    parent = resolve_security_config(
        explicit={
            "permission_mode": "bypass_permissions",
            "sandbox_required": False,
            "sandbox_network": True,
            "plugin_editable": True,
            "plugin_activation": True,
        },
        user_root=tmp_path,
    )

    child = inherit_policy(parent, AgentCapability.READ_ONLY)

    assert child.permission_mode.value is parent.permission_mode.value
    assert child.sandbox_required.value is True
    assert child.sandbox_network.value is False
    assert child.plugin_editable.value is False
    assert child.plugin_activation.value is False


def test_inherited_child_keeps_parent_posture() -> None:
    parent = resolve_security_config(explicit={"sandbox_network": True})
    assert inherit_policy(parent, AgentCapability.INHERITED) == parent
