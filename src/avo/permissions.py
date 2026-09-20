"""Permission modes and policy-driven approval callbacks.

The runtime exposes a single ``approval_callback(call) -> bool`` hook;
this module is the policy layer that decides whether a given tool call
needs approval under the operator-selected permission mode.

Modes:

* ``default`` — every tool call asks the operator.
* ``accept_edits`` — read-only and mutating file tools are auto-approved;
  shell tools (``run_shell``) still ask.
* ``plan`` — read-only tools are auto-approved; mutating tools require
  the agent to have called ``submit_plan`` first in this run. Shell
  tools always ask regardless of plan state.
* ``bypass_permissions`` — every tool call is auto-approved.

Plan state is tracked per run id. ``submit_plan_tool`` flips the bit
for the active run; ``clear_plan`` removes it once a run terminates so
unrelated runs are unaffected. The runtime itself stays untouched —
the chat REPL calls ``set_active_run`` before each ``runtime.run`` and
``clear_active_run`` afterwards.

Mode is selected via ``AVO_PERMISSION_MODE`` in the environment
(default ``default``).
"""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TextIO

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from avo.models import ToolCall
from avo.runtime import ApprovalCallback

_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "git_diff",
        "git_status",
        "grep",
        "glob",
        "symbols",
        "workspace_map",
    }
)
_EXECUTION_TOOLS: frozenset[str] = frozenset({"run_shell", "run_terminal", "test_runner", "lint"})
_NETWORK_TOOLS: frozenset[str] = frozenset({"web_fetch", "web_search", "http_fetch"})
_MUTATING_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "batch_replace", "git_commit"}
)
_SHELL_TOOLS: frozenset[str] = frozenset({"run_shell"})
_PLAN_TOOL_NAME = "submit_plan"


class PermissionMode(StrEnum):
    """Operator-selected tool-call approval policy."""

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypass_permissions"


class PermissionPolicy(BaseModel):
    """Resolved permission configuration for one REPL session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: PermissionMode = PermissionMode.DEFAULT
    require_approval: tuple[str, ...] = Field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Active-run + plan tracker (module-level; Avo runs one agent at a time)
# ---------------------------------------------------------------------------

_active_run_id: str | None = None
_plan_submitted: set[str] = set()


def set_active_run(run_id: str | None) -> None:
    """Mark ``run_id`` as the run the approval callback should reason about."""

    global _active_run_id
    _active_run_id = run_id


def clear_active_run(run_id: str) -> None:
    """Drop ``run_id`` from the active slot if it matches; clear plan state."""

    global _active_run_id
    if _active_run_id == run_id:
        _active_run_id = None
    _plan_submitted.discard(run_id)


def mark_plan_submitted(run_id: str) -> None:
    """Record that ``run_id`` has called ``submit_plan`` at least once."""

    _plan_submitted.add(run_id)


def is_plan_submitted(run_id: str | None) -> bool:
    """Return whether ``run_id`` has an outstanding plan submission."""

    if run_id is None:
        return False
    return run_id in _plan_submitted


def active_run_id() -> str | None:
    """Return the currently active run id, or ``None`` if no run is in flight."""

    return _active_run_id


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def should_require_approval(
    tool_name: str,
    mode: PermissionMode,
    *,
    plan_submitted: bool = False,
    require_approval: tuple[str, ...] = (),
) -> bool:
    """Return whether ``tool_name`` needs operator approval under ``mode``.

    ``plan_submitted`` only matters when ``mode is PLAN``. ``require_approval``
    forces a confirmation regardless of mode (except ``bypass_permissions``).
    """

    if mode is PermissionMode.BYPASS_PERMISSIONS:
        return False
    if tool_name in require_approval:
        return True
    if tool_name in _SHELL_TOOLS or tool_name in _EXECUTION_TOOLS or tool_name in _NETWORK_TOOLS:
        return True  # shell always asks in non-bypass modes
    if mode is PermissionMode.ACCEPT_EDITS:
        return tool_name not in _READ_ONLY_TOOLS and tool_name not in _MUTATING_TOOLS
    if mode is PermissionMode.PLAN:
        if tool_name in _READ_ONLY_TOOLS:
            return False
        if tool_name in _MUTATING_TOOLS:
            return not plan_submitted
        return True
    # DEFAULT: every tool asks.
    return True


def build_deny_by_default_callback() -> ApprovalCallback:
    """Return the safe callback used by runtimes without an app policy.

    Read-only tools are safe to inspect. Mutating, executable, network, and
    unknown tools are denied until the caller supplies an explicit policy.
    """

    def callback(call: ToolCall) -> bool:
        from avo.capabilities import ToolCapability, classify_tool

        return classify_tool(call.name) is ToolCapability.READ

    return callback


# ---------------------------------------------------------------------------
# Console prompter
# ---------------------------------------------------------------------------

ConsolePrompter = Callable[[ToolCall, TextIO, TextIO], bool | Awaitable[bool]]


def render_tool_call_preview(call: ToolCall, color: bool = True) -> str:
    """Render an informative preview (diff for file edits, command box for terminal)."""
    name = call.name
    args = call.arguments if isinstance(call.arguments, dict) else {}

    c_red = "\033[31m" if color else ""
    c_green = "\033[32m" if color else ""
    c_bold = "\033[1m" if color else ""
    c_dim = "\033[2m" if color else ""
    c_reset = "\033[0m" if color else ""

    lines: list[str] = []
    if name == "edit_file":
        path = args.get("path", "")
        old_text = str(args.get("old_text", ""))
        new_text = str(args.get("new_text", ""))
        lines.append(f"{c_bold}── edit_file: {path} ─────────────────────────────{c_reset}")
        for o_line in old_text.splitlines():
            lines.append(f"{c_red}- {o_line}{c_reset}")
        for n_line in new_text.splitlines():
            lines.append(f"{c_green}+ {n_line}{c_reset}")
        lines.append(f"{c_dim}──────────────────────────────────────────────────{c_reset}")
        return "\n".join(lines)

    if name == "write_file":
        path = args.get("path", "")
        content = str(args.get("content", ""))
        lines.append(f"{c_bold}── write_file: {path} ────────────────────────────{c_reset}")
        content_lines = content.splitlines()
        preview_lines = content_lines[:15]
        for c_line in preview_lines:
            lines.append(f"{c_green}+ {c_line}{c_reset}")
        if len(content_lines) > 15:
            lines.append(f"{c_dim}... ({len(content_lines) - 15} more lines){c_reset}")
        lines.append(f"{c_dim}──────────────────────────────────────────────────{c_reset}")
        return "\n".join(lines)

    if name == "run_terminal":
        cmd = str(args.get("command", ""))
        lines.append(f"{c_bold}── run_terminal ──────────────────────────────────{c_reset}")
        lines.append(f"$ {cmd}")
        lines.append(f"{c_dim}──────────────────────────────────────────────────{c_reset}")
        return "\n".join(lines)

    return ""


async def _default_console_prompter(call: ToolCall, stdin: TextIO, stdout: TextIO) -> bool:
    """Print an informative preview and prompt operator for approval."""
    color = hasattr(stdout, "isatty") and stdout.isatty()
    preview = render_tool_call_preview(call, color=color)
    if preview:
        stdout.write(f"\n{preview}\n")
    else:
        stdout.write("\n")
    stdout.write(f"Approve {call.name}({_summarise_args(call.arguments)})? [y/N]: ")
    stdout.flush()
    line = stdin.readline()
    if not line:
        return False
    return line.strip().lower() in ("y", "yes")


def _summarise_args(arguments: dict[str, JsonValue]) -> str:
    """Render tool arguments compactly for the approval prompt."""

    parts: list[str] = []
    for key, value in arguments.items():
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Callback builder
# ---------------------------------------------------------------------------


def build_approval_callback(
    policy: PermissionPolicy,
    *,
    prompter: ConsolePrompter | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> ApprovalCallback:
    """Build a callback that enforces ``policy`` and delegates to ``prompter``.

    The returned callback is async-safe: a synchronous ``prompter`` is
    invoked directly, an async one is awaited. ``submit_plan`` always
    returns ``True`` and marks the active run's plan as submitted so the
    caller can audit the decision.
    """

    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    actual_prompter = prompter or _default_console_prompter

    async def callback(call: ToolCall) -> bool:
        if call.name == _PLAN_TOOL_NAME:
            run_id = _active_run_id
            if run_id is not None:
                mark_plan_submitted(run_id)
            return True

        plan_ok = is_plan_submitted(_active_run_id)
        if not should_require_approval(
            call.name,
            policy.mode,
            plan_submitted=plan_ok,
            require_approval=policy.require_approval,
        ):
            return True

        # In plan mode, mutating tools without a plan are hard-denied
        # rather than prompted — there is nothing to authorise yet, and
        # the agent should call submit_plan first.
        if policy.mode is PermissionMode.PLAN and not plan_ok and call.name in _MUTATING_TOOLS:
            return False

        value = actual_prompter(call, in_stream, out_stream)
        if inspect.isawaitable(value):
            return await value
        return value

    return callback


def parse_permission_mode(value: object, *, source: str) -> PermissionMode:
    """Parse one permission mode and include its origin in failures."""

    if not isinstance(value, str) or not value.strip():
        allowed = ", ".join(item.value for item in PermissionMode)
        raise ValueError(
            f"AVO_PERMISSION_MODE from {source} must be one of {allowed}; got {value!r}"
        )
    raw = value.strip().lower()
    if raw == "bypass":
        raw = PermissionMode.BYPASS_PERMISSIONS.value
    try:
        return PermissionMode(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in PermissionMode)
        raise ValueError(
            f"AVO_PERMISSION_MODE from {source} must be one of {allowed}; got {value!r}"
        ) from exc


def permission_policy_from_env(environ: dict[str, str] | None = None) -> PermissionPolicy:
    """Build a :class:`PermissionPolicy` from the AVO_PERMISSION_MODE env var."""

    env = environ if environ is not None else dict(os.environ)
    raw = env.get("AVO_PERMISSION_MODE", "").strip()
    if not raw:
        return PermissionPolicy()
    mode = parse_permission_mode(raw, source="environment")
    extra_raw = env.get("AVO_TOOLS_REQUIRE_APPROVAL", "").strip()
    require_approval = tuple(item.strip() for item in extra_raw.split(",") if item.strip())
    return PermissionPolicy(mode=mode, require_approval=require_approval)


__all__ = [
    "ConsolePrompter",
    "PermissionMode",
    "PermissionPolicy",
    "active_run_id",
    "build_approval_callback",
    "build_deny_by_default_callback",
    "clear_active_run",
    "is_plan_submitted",
    "mark_plan_submitted",
    "parse_permission_mode",
    "permission_policy_from_env",
    "set_active_run",
    "should_require_approval",
]
