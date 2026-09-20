"""Regression tests for runtime policy inheritance and secure defaults."""

from __future__ import annotations

from avo.agent_profiles import AgentCapability
from avo.capabilities import inherit_runtime_security
from avo.config_resolver import ConfigSource, resolve_security_config
from avo.models import ToolCall
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime


def test_child_policy_defaults_to_sandbox_when_parent_has_no_config() -> None:
    child = inherit_runtime_security(None, AgentCapability.INHERITED)

    assert child.sandbox_required.value is True
    assert child.sandbox_network.value is False
    assert child.plugin_activation.value is False
    assert child.sandbox_required.source is ConfigSource.DEFAULT


def test_read_only_child_policy_cannot_enable_network_or_plugins() -> None:
    parent = resolve_security_config(
        explicit={
            "sandbox_required": True,
            "sandbox_network": True,
            "plugin_activation": True,
            "sandbox_timeout_seconds": 45,
        },
        environ={},
        user_root=None,
    )

    child = inherit_runtime_security(parent, AgentCapability.READ_ONLY)

    assert child.sandbox_required.value is True
    assert child.sandbox_network.value is False
    assert child.plugin_activation.value is False
    assert child.sandbox_timeout_seconds.value == 45


def test_direct_runtime_default_denies_execution_and_allows_read_only() -> None:
    runtime = AgentRuntime(provider=FakeProvider(responses=[]))

    assert runtime.approval_callback(ToolCall(name="run_terminal")) is False
    assert runtime.approval_callback(ToolCall(name="web_fetch")) is False
    assert runtime.approval_callback(ToolCall(name="read_file")) is True
