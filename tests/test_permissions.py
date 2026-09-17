"""Tests for the permission policy and approval-callback builder."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from io import StringIO

import pytest

from avo.models import ToolCall
from avo.permissions import (
    PermissionMode,
    PermissionPolicy,
    active_run_id,
    build_approval_callback,
    clear_active_run,
    is_plan_submitted,
    mark_plan_submitted,
    permission_policy_from_env,
    set_active_run,
    should_require_approval,
)

# ---------------------------------------------------------------------------
# should_require_approval
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS, PermissionMode.PLAN],
)
def test_shell_always_asks_except_bypass(mode: PermissionMode) -> None:
    assert should_require_approval("run_shell", mode) is True


def test_bypass_permissions_approves_everything() -> None:
    for name in ("read_file", "write_file", "edit_file", "run_shell"):
        assert should_require_approval(name, PermissionMode.BYPASS_PERMISSIONS) is False


def test_default_mode_asks_every_tool() -> None:
    for name in ("read_file", "write_file", "edit_file", "run_shell"):
        assert should_require_approval(name, PermissionMode.DEFAULT) is True


def test_accept_edits_auto_approves_read_and_write() -> None:
    assert should_require_approval("read_file", PermissionMode.ACCEPT_EDITS) is False
    assert should_require_approval("workspace_map", PermissionMode.ACCEPT_EDITS) is False
    assert should_require_approval("git_status", PermissionMode.ACCEPT_EDITS) is False
    assert should_require_approval("write_file", PermissionMode.ACCEPT_EDITS) is False
    assert should_require_approval("edit_file", PermissionMode.ACCEPT_EDITS) is False


def test_plan_mode_requires_plan_before_writes() -> None:
    assert should_require_approval("read_file", PermissionMode.PLAN) is False
    assert should_require_approval("workspace_map", PermissionMode.PLAN) is False
    assert should_require_approval("git_status", PermissionMode.PLAN) is False
    assert should_require_approval("write_file", PermissionMode.PLAN, plan_submitted=False) is True
    assert should_require_approval("write_file", PermissionMode.PLAN, plan_submitted=True) is False
    assert should_require_approval("edit_file", PermissionMode.PLAN, plan_submitted=True) is False


def test_require_approval_overrides_mode() -> None:
    # read_file is auto-approved in accept_edits; explicit require forces a confirmation.
    assert (
        should_require_approval(
            "read_file",
            PermissionMode.ACCEPT_EDITS,
            require_approval=("read_file",),
        )
        is True
    )


def test_require_approval_does_not_override_bypass() -> None:
    assert (
        should_require_approval(
            "read_file",
            PermissionMode.BYPASS_PERMISSIONS,
            require_approval=("read_file",),
        )
        is False
    )


# ---------------------------------------------------------------------------
# Plan tracker
# ---------------------------------------------------------------------------


def test_plan_tracker_isolated_per_run() -> None:
    set_active_run("run-a")
    mark_plan_submitted("run-a")
    set_active_run("run-b")
    assert is_plan_submitted("run-a") is True
    assert is_plan_submitted("run-b") is False
    clear_active_run("run-b")
    assert is_plan_submitted("run-a") is True
    clear_active_run("run-a")
    assert is_plan_submitted("run-a") is False


def test_plan_tracker_clear_only_drops_target() -> None:
    set_active_run("run-x")
    mark_plan_submitted("run-x")
    set_active_run("run-y")
    mark_plan_submitted("run-y")
    clear_active_run("run-x")
    assert is_plan_submitted("run-x") is False
    assert is_plan_submitted("run-y") is True
    clear_active_run("run-y")


# ---------------------------------------------------------------------------
# build_approval_callback
# ---------------------------------------------------------------------------


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(name=name, arguments=arguments)


async def test_callback_submits_plan_without_prompting() -> None:
    set_active_run("run-1")
    try:
        asked: list[ToolCall] = []

        async def prompter(call: ToolCall, _in: object, _out: object) -> bool:
            asked.append(call)
            return True

        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        callback = build_approval_callback(policy, prompter=prompter)
        assert await callback(_call("submit_plan", plan_text="do thing")) is True
        assert asked == []
        assert is_plan_submitted("run-1") is True
    finally:
        clear_active_run("run-1")


async def test_callback_bypass_approves_without_prompting() -> None:
    asked: list[ToolCall] = []

    async def prompter(call: ToolCall, _in: object, _out: object) -> bool:
        asked.append(call)
        return True

    policy = PermissionPolicy(mode=PermissionMode.BYPASS_PERMISSIONS)
    callback = build_approval_callback(policy, prompter=prompter)
    assert await callback(_call("run_shell", command="rm -rf /")) is True
    assert asked == []


async def test_callback_accept_edits_auto_approves_file_writes() -> None:
    asked: list[ToolCall] = []

    async def prompter(call: ToolCall, _in: object, _out: object) -> bool:
        asked.append(call)
        return True

    policy = PermissionPolicy(mode=PermissionMode.ACCEPT_EDITS)
    callback = build_approval_callback(policy, prompter=prompter)
    assert await callback(_call("write_file", path="x", content="y")) is True
    assert asked == []


async def test_callback_accept_edits_still_prompts_for_shell() -> None:
    asked: list[ToolCall] = []

    async def prompter(call: ToolCall, _in: object, _out: object) -> bool:
        asked.append(call)
        return False

    policy = PermissionPolicy(mode=PermissionMode.ACCEPT_EDITS)
    callback = build_approval_callback(policy, prompter=prompter)
    assert await callback(_call("run_shell", command="echo hi")) is False
    assert len(asked) == 1


async def test_callback_default_uses_console_prompter() -> None:
    stdin = StringIO("y\n")
    stdout = StringIO()
    policy = PermissionPolicy(mode=PermissionMode.DEFAULT)
    callback = build_approval_callback(policy, stdin=stdin, stdout=stdout)
    assert await callback(_call("write_file", path="x", content="y")) is True
    assert "Approve" in stdout.getvalue()


async def test_callback_default_rejects_empty_input() -> None:
    stdin = StringIO("\n")
    stdout = StringIO()
    policy = PermissionPolicy(mode=PermissionMode.DEFAULT)
    callback = build_approval_callback(policy, stdin=stdin, stdout=stdout)
    assert await callback(_call("write_file", path="x", content="y")) is False


async def test_callback_uses_sync_prompter() -> None:
    def prompter(call: ToolCall, _in: object, _out: object) -> bool:
        return call.name != "run_shell"

    policy = PermissionPolicy(mode=PermissionMode.DEFAULT)
    callback = build_approval_callback(policy, prompter=prompter)
    assert await callback(_call("write_file", path="x", content="y")) is True
    assert await callback(_call("run_shell", command="x")) is False


async def test_callback_plan_mode_denies_writes_before_plan() -> None:
    set_active_run("run-2")
    try:
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        callback = build_approval_callback(policy, prompter=_never_called_prompter())
        assert await callback(_call("write_file", path="x", content="y")) is False
    finally:
        clear_active_run("run-2")


async def test_callback_plan_mode_allows_writes_after_plan() -> None:
    set_active_run("run-3")
    try:
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        callback = build_approval_callback(policy, prompter=_never_called_prompter())
        await callback(_call("submit_plan", plan_text="plan"))
        assert await callback(_call("write_file", path="x", content="y")) is True
    finally:
        clear_active_run("run-3")


def _never_called_prompter() -> Callable[[ToolCall, object, object], Awaitable[bool]]:
    async def prompter(call: ToolCall, _in: object, _out: object) -> bool:
        pytest.fail(f"prompter should not be called for {call.name}")
        return False  # pragma: no cover

    return prompter


# ---------------------------------------------------------------------------
# Env loader
# ---------------------------------------------------------------------------


def test_permission_policy_from_env_defaults_to_default_mode() -> None:
    policy = permission_policy_from_env({})
    assert policy.mode is PermissionMode.DEFAULT
    assert policy.require_approval == ()


def test_permission_policy_from_env_reads_mode_and_extras() -> None:
    policy = permission_policy_from_env(
        {
            "AVO_PERMISSION_MODE": "plan",
            "AVO_TOOLS_REQUIRE_APPROVAL": "write_file, edit_file",
        }
    )
    assert policy.mode is PermissionMode.PLAN
    assert set(policy.require_approval) == {"write_file", "edit_file"}


def test_permission_policy_from_env_accepts_legacy_bypass_alias() -> None:
    policy = permission_policy_from_env({"AVO_PERMISSION_MODE": "bypass"})
    assert policy.mode is PermissionMode.BYPASS_PERMISSIONS


def test_permission_policy_from_env_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="AVO_PERMISSION_MODE"):
        permission_policy_from_env({"AVO_PERMISSION_MODE": "nuclear"})


def test_active_run_id_returns_current_slot() -> None:
    set_active_run(None)
    assert active_run_id() is None
    set_active_run("alpha")
    assert active_run_id() == "alpha"
    clear_active_run("alpha")
    assert active_run_id() is None


def test_clear_active_run_ignores_other_runs() -> None:
    set_active_run("alpha")
    clear_active_run("beta")
    assert active_run_id() == "alpha"
    clear_active_run("alpha")
    assert active_run_id() is None
