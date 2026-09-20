"""Autonomous test repair engine for Avo CLI (`avo fix` / `/fix`)."""

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
from avo.app_tools.test_runner import run_tests
from avo.app_tools.workspace import Workspace
from avo.app_tools.worktree import GitWorktreeManager
from avo.config import build_provider_from_env
from avo.config_resolver import resolve_security_config
from avo.permissions import PermissionMode, PermissionPolicy, build_approval_callback
from avo.policies import LoopPolicy
from avo.providers.base import ModelProvider
from avo.runtime import AgentRuntime
from avo.storage.sqlite import SQLiteEventStore
from avo.tools import Tool

_LOG = logging.getLogger(__name__)


async def run_cli_fix(
    workspace_root: Path,
    database_path: Path,
    target: str | None = None,
    *,
    max_steps: int = 25,
    token_budget: int = 120_000,
    auto_merge: bool = True,
    provider: ModelProvider | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> bool:
    """Execute autonomous test-driven diagnosis and repair in an isolated worktree."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    env = dict(os.environ if environ is None else environ)
    base_root = workspace_root.resolve()  # noqa: ASYNC240

    # 1. Initial test verification on current workspace
    out.write(f"🧪 Running test suite{f' for target: {target}' if target else ''}...\n")
    out.flush()

    initial_run = run_tests(base_root, target=target)
    if initial_run.get("ok"):
        out.write(
            f"✓ All tests are already passing ({initial_run.get('summary')}). Nothing to fix.\n"
        )
        out.flush()
        return True

    summary = initial_run.get("summary", "Tests failed")
    failures = initial_run.get("failures", [])
    output_preview = initial_run.get("output", "")
    if len(output_preview) > 1500:
        output_preview = output_preview[:1450] + "\n... [truncated]"

    out.write(f"❌ Tests failed: {summary}\n")
    if failures:
        out.write("Failure signatures:\n")
        for f in failures[:5]:
            out.write(f"  • {f}\n")
    out.write("🌿 Initializing isolated Git worktree for safe autonomous repair...\n")
    out.flush()

    # 2. Setup isolated worktree
    run_id = f"fix-{secrets.token_hex(4)}"
    worktree_manager: GitWorktreeManager | None = None
    active_root = base_root

    try:
        mgr = GitWorktreeManager(repo_root=base_root)
        worktree_manager = mgr
        active_root = mgr.create_worktree(run_id=run_id)
        out.write(f"🌿 Worktree isolated at: {active_root}\n\n")
        out.flush()
    except Exception as exc:
        _LOG.warning("Could not initialize worktree isolation: %s", exc)
        err.write(f"Notice: Git worktree unavailable ({exc}). Running repair in place.\n")
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

    runtime = AgentRuntime(
        provider=resolved_provider,
        event_store=store,
        tools=tools,
        policy=LoopPolicy(max_steps=max_steps, max_total_tokens=token_budget),
        approval_callback=approval_cb,
    )

    repair_prompt = (
        f"The test suite failed with summary: {summary}\n\n"
        f"Test output snippet:\n```\n{output_preview}\n```\n\n"
        "Your task is to fix the code so all tests pass cleanly:\n"
        "1. Inspect the failing test and related implementation files.\n"
        "2. Identify the root cause bug or regression.\n"
        "3. Apply surgical edits to fix the implementation (do not delete tests unless obsolete).\n"
        "4. Use the test_runner tool to verify that tests are green."
    )

    def _stream_cb(token: str) -> None:
        out.write(token)
        out.flush()

    try:
        out.write(f"🥑 Starting repair agent turn ({run_id})...\n\n")
        out.flush()

        await runtime.run(
            task=repair_prompt,
            run_id=run_id,
            stream_callback=_stream_cb,
        )

        out.write("\n\n🧪 Verifying test suite after repair...\n")
        out.flush()

        # 3. Post-repair verification
        post_run = run_tests(active_root, target=target)
        if post_run.get("ok"):
            out.write(f"✓ Successfully resolved test failure: {post_run.get('summary')}!\n")
            if worktree_manager is not None:
                if auto_merge:
                    out.write("Merging repair into base branch...\n")
                    out.flush()
                    worktree_manager.cleanup_worktree(run_id, merge=True)
                    out.write("✓ Repair cleanly merged into repository.\n")
                else:
                    out.write(
                        f"Isolated repair branch preserved for manual review: {active_root}\n"
                    )
            out.flush()
            return True

        out.write(f"⚠️ Tests are still failing after repair: {post_run.get('summary')}\n")
        if worktree_manager is not None:
            out.write(f"Worktree preserved for manual inspection: {active_root}\n")
        out.flush()
        return False
    finally:
        await store.close()
