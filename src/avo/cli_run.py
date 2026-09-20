"""Direct autonomous prompt execution for the Avo CLI (`avo run` / `avo "task"`)."""

from __future__ import annotations

import logging
import os
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from avo.app_tools import (
    batch_replace_tool,
    bind_workspace,
    edit_file_tool,
    git_commit_tool,
    git_diff_tool,
    git_status_tool,
    glob_tool,
    grep_tool,
    lint_tool,
    read_file_tool,
    run_terminal_tool,
    symbols_tool,
    test_runner_tool,
    workspace_map_tool,
    write_file_tool,
)
from avo.app_tools.workspace import Workspace
from avo.app_tools.worktree import GitWorktreeManager
from avo.config import build_provider_from_env
from avo.config_resolver import resolve_security_config
from avo.models import RunResult
from avo.permissions import PermissionMode, PermissionPolicy, build_approval_callback
from avo.policies import LoopPolicy
from avo.providers.base import ModelProvider
from avo.runtime import AgentRuntime
from avo.storage.sqlite import SQLiteEventStore
from avo.tools import Tool

_LOG = logging.getLogger(__name__)


async def run_cli_task(
    task: str,
    *,
    workspace_root: Path,
    database_path: Path,
    provider: ModelProvider | None = None,
    use_worktree: bool = False,
    auto_merge: bool = False,
    json_output: bool = False,
    max_steps: int = 30,
    token_budget: int = 150_000,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> RunResult:
    """Execute a single autonomous task prompt directly from the CLI."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    env = dict(os.environ if environ is None else environ)

    run_id = f"task-{secrets.token_hex(4)}"
    active_root = workspace_root.resolve()  # noqa: ASYNC240
    worktree_manager: GitWorktreeManager | None = None

    if use_worktree:
        try:
            mgr = GitWorktreeManager(repo_root=active_root)
            worktree_manager = mgr
            active_root = mgr.create_worktree(run_id=run_id)
            if not json_output:
                out.write(f"🌿 Worktree isolated at: {active_root}\n")
                out.flush()
        except Exception as exc:
            _LOG.warning("Could not initialize worktree isolation: %s", exc)
            if not json_output:
                err.write(
                    f"Notice: Git worktree isolation unavailable ({exc}). Running in place.\n"
                )
                err.flush()

    workspace = Workspace(active_root, create=False)
    bind_workspace(workspace)

    store = SQLiteEventStore(database_path.resolve())  # noqa: ASYNC240
    resolved_provider = provider if provider is not None else build_provider_from_env(env)

    security_config = resolve_security_config(environ=env, workspace_root=active_root)
    policy = PermissionPolicy(mode=PermissionMode.ACCEPT_EDITS)
    approval_cb = build_approval_callback(policy, stdin=sys.stdin, stdout=out)

    tools: list[Tool] = [
        read_file_tool(),
        write_file_tool(),
        edit_file_tool(),
        batch_replace_tool(),
        grep_tool(),
        glob_tool(),
        symbols_tool(),
        workspace_map_tool(),
        lint_tool(),
        test_runner_tool(),
        run_terminal_tool(security_config=security_config),
        git_status_tool(),
        git_diff_tool(),
        git_commit_tool(),
    ]

    def stream_chunk(token: str) -> None:
        if not json_output:
            out.write(token)
            out.flush()

    runtime = AgentRuntime(
        provider=resolved_provider,
        event_store=store,
        tools=tools,
        policy=LoopPolicy(max_steps=max_steps, max_total_tokens=token_budget),
        approval_callback=approval_cb,
    )

    if not json_output:
        out.write(f"🥑 Running task: {task}\n\n")
        out.flush()

    try:
        result = await runtime.run(
            task=task,
            run_id=run_id,
            stream_callback=stream_chunk,
        )

        if not json_output:
            out.write(f"\n\n✓ Task finished with status: {result.status.value}\n")
            out.write(f"Stop reason: {result.stop_reason.value} | Steps: {result.steps}\n")
            out.flush()

        if worktree_manager is not None and use_worktree:
            if auto_merge and result.status.value in ("green", "completed"):
                if not json_output:
                    out.write("Merging worktree changes into main branch...\n")
                worktree_manager.cleanup_worktree(run_id, merge=True)
            elif not auto_merge:
                if not json_output:
                    out.write(
                        f"Isolated worktree preserved for review: {active_root}\n"
                        f"To clean up: git worktree remove {active_root}\n"
                    )

        return result
    finally:
        await store.close()
