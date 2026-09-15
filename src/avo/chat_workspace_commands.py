"""Workspace-facing chat commands: shell, git, and file-inspection helpers.

These functions drive the active workspace — running shell commands,
showing diffs, staging commits, and invoking the app_tools file
utilities (grep, glob, map, symbols) through the same bound-workspace
context the model's tools use.

Re-exported from :mod:`avo.chat` for backward compatibility.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from avo.app_tools.file_tools import bind_workspace
from avo.chat_render import _format_file_size

if TYPE_CHECKING:
    from avo.chat import ChatContext


def _run_repl_shell(
    workspace_root: Path,
    command: str,
    out: TextIO,
    err: TextIO,
    *,
    timeout_seconds: float = 120.0,
) -> None:
    """Execute a shell command in workspace and display streaming/captured output.

    ``shell=True`` is deliberate: this is the interactive REPL escape
    (``!COMMAND`` / ``/shell COMMAND``) where the command string comes
    from the typing user themselves, scoped to their own workspace, and
    is never model- or network-supplied.
    """
    import subprocess
    import time

    cmd = command.strip()
    if not cmd:
        err.write("usage: !COMMAND or /shell COMMAND\n")
        err.flush()
        return

    out.write(f"$ {cmd}\n")
    out.flush()
    start = time.perf_counter()

    try:
        proc = subprocess.run(
            cmd,
            shell=True,  # nosec B602 - user-typed REPL escape, not remote input
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        elapsed = time.perf_counter() - start
        if proc.stdout:
            out.write(proc.stdout)
            if not proc.stdout.endswith("\n"):
                out.write("\n")
        if proc.stderr:
            err.write(proc.stderr)
            if not proc.stderr.endswith("\n"):
                err.write("\n")
        if proc.returncode != 0:
            out.write(f"exited with code {proc.returncode} ({elapsed:.2f}s)\n")
        out.flush()
        err.flush()
    except subprocess.TimeoutExpired:
        err.write(f"command timed out after {timeout_seconds}s\n")
        err.flush()
    except Exception as exc:
        err.write(f"failed to run command: {exc}\n")
        err.flush()


def _show_diff_summary(
    workspace_root: Path,
    out: TextIO,
    err: TextIO,
    target_path: str | None = None,
) -> None:
    """Render workspace git status and diff summary or unified diff."""
    from avo.workspace.git import GitError, GitRepository

    repo = GitRepository(workspace_root)
    if not repo.is_repository():
        err.write(f"workspace {workspace_root} is not a git repository or git failed.\n")
        return

    if target_path and target_path.lower() not in ("stat", "--stat"):
        filter_path = None if target_path.lower() in ("full", "--full", "all") else target_path
        try:
            diff_text = repo.diff(path=filter_path)
            if not diff_text.strip():
                out.write("No modifications found in workspace.\n")
            else:
                out.write(diff_text + "\n")
            out.flush()
            return
        except GitError as exc:
            err.write(f"diff failed: {exc}\n")
            return

    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace_root), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status_output = proc.stdout.strip()
        if not status_output:
            out.write("Working tree clean (no modified or staged files).\n")
            return

        out.write(f"Workspace git status ({workspace_root}):\n")
        lines = status_output.splitlines()
        for line in lines[:20]:
            out.write(f"  {line}\n")
        if len(lines) > 20:
            out.write(f"  ... and {len(lines) - 20} more file(s)\n")

        diff_proc = subprocess.run(
            ["git", "-C", str(workspace_root), "diff", "--stat"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if diff_proc.returncode == 0 and diff_proc.stdout.strip():
            out.write("\nDiff stat:\n")
            for diff_line in diff_proc.stdout.strip().splitlines():
                out.write(f"  {diff_line}\n")
        out.flush()
    except Exception as exc:
        err.write(f"could not inspect git status: {exc}\n")


def _undo_workspace(workspace_root: Path, out: TextIO, err: TextIO) -> None:
    """Revert uncommitted working tree modifications in the active workspace."""
    from avo.workspace.git import GitError, GitRepository

    repo = GitRepository(workspace_root)
    if not repo.is_repository():
        err.write(f"workspace {workspace_root} is not a git repository.\n")
        return

    try:
        reverted = repo.rollback()
        if not reverted:
            out.write("Working tree clean. Nothing to undo.\n")
        else:
            out.write(f"Rolled back changes in {len(reverted)} file(s):\n")
            for r_file in reverted:
                out.write(f"  ↶ {r_file}\n")
        out.flush()
    except GitError as exc:
        err.write(f"undo failed: {exc}\n")


def _run_workspace_lint(
    workspace_root: Path,
    out: TextIO,
    err: TextIO,
    target_path: str | None = None,
) -> None:
    """Execute linter on workspace and display concise results."""
    from avo.app_tools.linter import run_linter

    res = run_linter(workspace_root, target_path)
    tool_used = res.get("tool", "linter")
    if res.get("ok"):
        out.write(f"✓ Linter ({tool_used}) passed cleanly: no issues found.\n")
    else:
        count = res.get("issue_count", 0)
        out.write(f"⚠ Linter ({tool_used}) detected {count} issue(s):\n")
        for issue in res.get("issues", []):
            out.write(f"  • {issue}\n")
    out.flush()


def _run_workspace_tests(
    workspace_root: Path,
    out: TextIO,
    err: TextIO,
    target: str | None = None,
) -> None:
    """Execute automated tests on workspace and display concise results."""
    from avo.app_tools.test_runner import run_tests

    out.write(f"Running test suite ({target or 'all'})...\n")
    out.flush()
    res = run_tests(workspace_root, target=target)
    runner = res.get("runner", "test_runner")
    target_name = res.get("target", "all")
    if res.get("ok"):
        out.write(f"✓ Test suite ({runner}) passed cleanly: {res.get('summary')}\n")
    else:
        ret = res.get("returncode")
        out.write(f"✗ Test suite ({runner}) failed for target '{target_name}' (exit code {ret}):\n")
        out.write(f"  Summary: {res.get('summary')}\n")
        failures = res.get("failures", [])
        if failures:
            out.write("  Failure traces:\n")
            for f in failures[:10]:
                out.write(f"    • {f}\n")
    out.flush()


def _run_workspace_commit(
    workspace_root: Path,
    out: TextIO,
    err: TextIO,
    message: str | None = None,
) -> None:
    """Stage changes and create a git commit from REPL."""
    from avo.workspace.git import GitError, GitRepository, generate_commit_message_heuristic

    repo = GitRepository(workspace_root)
    if not repo.is_repository():
        err.write(f"workspace {workspace_root} is not a git repository or git failed.\n")
        return

    status = repo.status()
    if status.is_clean:
        out.write("Working tree is clean. Nothing to commit.\n")
        out.flush()
        return

    commit_msg = (
        message.strip()
        if message and message.strip()
        else generate_commit_message_heuristic(status)
    )

    try:
        commit_hash = repo.commit(message=commit_msg)
        out.write(f"✓ Committed [{commit_hash}]: {commit_msg}\n")
        out.flush()
    except GitError as exc:
        err.write(f"commit failed: {exc}\n")
        err.flush()


async def _run_bench_command(
    ctx: ChatContext,
    prompt: str,
    out: TextIO,
    err: TextIO,
) -> None:
    """Benchmark configured live provider or router routes and display ranking."""
    from avo.bench import benchmark_all_routes, benchmark_route, render_benchmark_table
    from avo.providers.router import BaseRouterProvider

    provider = ctx.runtime.provider
    out.write(f"Benchmarking with prompt: {prompt!r}...\n")
    out.flush()

    try:
        if isinstance(provider, BaseRouterProvider):
            results = await benchmark_all_routes(provider.routes, prompt=prompt)
            out.write(render_benchmark_table(results))
            out.flush()
            return

        res = await benchmark_route(ctx.provider_name, provider, prompt=prompt)
        out.write(render_benchmark_table([res]))
        out.flush()
    except Exception as exc:
        err.write(f"benchmark failed: {exc}\n")
        err.flush()


async def _run_grep_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """Execute workspace regex content search."""
    if len(args) < 2:
        err.write("usage: /grep PATTERN [INCLUDE_GLOB]\n")
        err.flush()
        return

    pattern = args[1]
    include_glob = args[2] if len(args) > 2 else None

    from avo.app_tools.grep_tool import grep_tool

    tool = grep_tool()
    with bind_workspace(ctx.workspace):
        try:
            res = await tool.invoke(
                {"pattern": pattern, "include_glob": include_glob, "max_results": 50}
            )
        except Exception as exc:
            err.write(f"grep error: {exc}\n")
            err.flush()
            return

    matches_raw = res.get("matches", []) if isinstance(res, dict) else []
    matches = matches_raw if isinstance(matches_raw, list) else []
    truncated = bool(res.get("truncated", False)) if isinstance(res, dict) else False

    if not matches:
        out.write(f"No matches found for pattern {pattern!r}")
        if include_glob:
            out.write(f" matching {include_glob!r}")
        out.write(".\n")
        out.flush()
        return

    out.write(f"\nFound {len(matches)}{'+' if truncated else ''} matches for {pattern!r}:\n\n")
    for m in matches:
        if isinstance(m, dict):
            p = m.get("path", "")
            ln = m.get("line_number", 0)
            line = str(m.get("line", "")).rstrip()
            out.write(f"  {p}:{ln}: {line}\n")
    out.write("\n")
    out.flush()


async def _run_find_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """Execute workspace glob file search."""
    pattern = args[1] if len(args) > 1 else "*"
    from avo.app_tools.glob_tool import glob_tool

    tool = glob_tool()
    with bind_workspace(ctx.workspace):
        try:
            res = await tool.invoke({"pattern": pattern, "max_results": 100})
        except Exception as exc:
            err.write(f"find error: {exc}\n")
            err.flush()
            return

    matches_raw = res.get("matches", []) if isinstance(res, dict) else []
    matches = matches_raw if isinstance(matches_raw, list) else []
    truncated = bool(res.get("truncated", False)) if isinstance(res, dict) else False

    if not matches:
        out.write(f"No files matched pattern {pattern!r}.\n")
        out.flush()
        return

    out.write(f"\nMatched {len(matches)}{'+' if truncated else ''} files ({pattern!r}):\n\n")
    for path_str in matches:
        out.write(f"  {path_str}\n")
    out.write("\n")
    out.flush()


async def _run_workspace_map_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """Display workspace file tree and recently modified files."""
    max_entries = 50
    if len(args) > 1 and args[1].isdigit():
        max_entries = max(1, min(5000, int(args[1])))

    from avo.app_tools.workspace_map import workspace_map_tool

    tool = workspace_map_tool()
    with bind_workspace(ctx.workspace):
        try:
            res = await tool.invoke(
                {"max_entries": max_entries, "include_map": True, "include_recent": True}
            )
        except Exception as exc:
            err.write(f"map error: {exc}\n")
            err.flush()
            return

    if not isinstance(res, dict):
        err.write("map error: unexpected response format\n")
        err.flush()
        return

    root_path = res.get("root", str(ctx.workspace.root))
    entry_count = res.get("entry_count", 0)
    truncated = res.get("truncated", False)
    recent_files = res.get("recent", [])
    map_text = res.get("map", "")

    trunc_label = f" (showing {max_entries} entries, truncated)" if truncated else ""
    out.write(f"\nWorkspace Map: {root_path}\n")
    out.write(f"Indexed files: {entry_count}{trunc_label}\n")

    if isinstance(recent_files, list) and recent_files:
        out.write("\nRecently Modified Files:\n")
        for item in recent_files[:8]:
            if isinstance(item, dict):
                p = item.get("path", "")
                raw_sz = item.get("size", 0)
                sz_int = int(raw_sz) if isinstance(raw_sz, (int, str, float)) else 0
                sz = _format_file_size(sz_int)
                out.write(f"  • {p:<45} ({sz})\n")

    if map_text:
        out.write("\nFile Tree:\n")
        for line in str(map_text).splitlines():
            out.write(f"  {line}\n")
    out.write("\n")
    out.flush()


async def _run_symbols_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """Extract code symbols (classes, functions) using AST."""
    target_path = args[1] if len(args) > 1 else "."
    symbol_filter = args[2] if len(args) > 2 else None

    from avo.app_tools.symbols import symbols_tool

    tool = symbols_tool()
    with bind_workspace(ctx.workspace):
        try:
            res = await tool.invoke(
                {"path": target_path, "symbol_name": symbol_filter, "max_symbols": 100}
            )
        except Exception as exc:
            err.write(f"symbols error: {exc}\n")
            err.flush()
            return

    files_raw = res.get("files", []) if isinstance(res, dict) else []
    files_list = files_raw if isinstance(files_raw, list) else []

    if not files_list:
        out.write(f"No symbols found in {target_path!r}.\n")
        out.flush()
        return

    out.write(f"\nSymbols in {target_path!r}:\n")
    for f in files_list:
        if not isinstance(f, dict):
            continue
        rel_file = f.get("file", "")
        err_msg = f.get("syntax_error")
        if err_msg:
            out.write(f"\n  {rel_file}: [Syntax Error: {err_msg}]\n")
            continue

        syms = f.get("symbols", [])
        if not isinstance(syms, list) or not syms:
            continue

        out.write(f"\n  {rel_file}:\n")
        for s in syms:
            if not isinstance(s, dict):
                continue
            kind = s.get("kind", "")
            name = s.get("name", "")
            ls = s.get("line_start", 0)
            le = s.get("line_end", 0)
            sig = s.get("signature", "")
            bases_raw = s.get("bases", [])
            bases = bases_raw if isinstance(bases_raw, list) else []
            base_str = f"({', '.join(str(b) for b in bases)})" if bases else ""

            if kind == "class":
                out.write(f"    class {name}{base_str} (L{ls}-L{le})\n")
                children = s.get("children", [])
                if isinstance(children, list):
                    for c in children:
                        if isinstance(c, dict):
                            c_name = c.get("name", "")
                            c_sig = c.get("signature", "()")
                            c_ls = c.get("line_start", 0)
                            c_kind = "async def" if c.get("kind") == "async_method" else "def"
                            out.write(f"      {c_kind} {c_name}{c_sig} (L{c_ls})\n")
            else:
                fn_kind = "async def" if kind == "async_function" else "def"
                out.write(f"    {fn_kind} {name}{sig} (L{ls}-L{le})\n")
    out.write("\n")
    out.flush()


def _manage_branch(
    workspace_root: Path,
    branch_name: str | None,
    out: TextIO,
    err: TextIO,
    *,
    create: bool = False,
) -> None:
    """List local branches or switch to a branch."""
    from avo.workspace.git import GitError, GitRepository

    repo = GitRepository(workspace_root)
    if not repo.is_repository():
        err.write(f"Workspace {workspace_root} is not a git repository.\n")
        err.flush()
        return

    if not branch_name:
        try:
            branches = repo.list_branches()
        except GitError as exc:
            err.write(f"git error: {exc}\n")
            err.flush()
            return

        out.write("\nGit Branches:\n")
        for b in branches:
            marker = "* " if b["current"] else "  "
            out.write(f"  {marker}{b['name']}\n")
        out.write("\n")
        out.flush()
        return

    try:
        existing = [b["name"] for b in repo.list_branches()]
        should_create = create or (branch_name not in existing)
        repo.switch_branch(branch_name, create=should_create)
        action = "Created and switched to" if should_create else "Switched to"
        out.write(f"✓ {action} branch {branch_name!r}.\n")
        out.flush()
    except GitError as exc:
        err.write(f"git error: {exc}\n")
        err.flush()


def _show_git_log(
    workspace_root: Path,
    out: TextIO,
    err: TextIO,
    max_count: int = 10,
) -> None:
    """Display recent git commits."""
    from avo.workspace.git import GitError, GitRepository

    repo = GitRepository(workspace_root)
    if not repo.is_repository():
        err.write(f"Workspace {workspace_root} is not a git repository.\n")
        err.flush()
        return

    try:
        commits = repo.log(max_count=max_count)
    except GitError as exc:
        err.write(f"git error: {exc}\n")
        err.flush()
        return

    if not commits:
        out.write("No commits found in repository.\n")
        out.flush()
        return

    out.write(f"\nRecent Commits (last {len(commits)}):\n\n")
    for c in commits:
        h = c["hash"]
        subj = c["subject"]
        author = c["author"]
        dt = c["date"][:10] if len(c.get("date", "")) >= 10 else ""
        out.write(f"  {h} - {subj} ({author}, {dt})\n")
    out.write("\n")
    out.flush()


def _manage_stash(
    workspace_root: Path,
    subcmd: str | None,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """Manage git stashes (list, save, pop, or drop)."""
    from avo.workspace.git import GitError, GitRepository

    repo = GitRepository(workspace_root)
    if not repo.is_repository():
        err.write(f"Workspace {workspace_root} is not a git repository.\n")
        err.flush()
        return

    cmd = (subcmd or "list").lower().strip()
    if cmd in ("list", ""):
        try:
            stashes = repo.stash_list()
        except GitError as exc:
            err.write(f"git error: {exc}\n")
            err.flush()
            return

        if not stashes:
            out.write("No stashes found in repository.\n")
            out.flush()
            return

        out.write(f"\nGit Stashes ({len(stashes)}):\n\n")
        for s in stashes:
            out.write(f"  [{s['index']}] {s['ref']}: {s['description']}\n")
        out.write("\n")
        out.flush()
        return

    if cmd in ("save", "push"):
        msg = " ".join(args) if args else None
        try:
            res = repo.stash_save(msg)
            out.write(f"✓ {res}\n")
            out.flush()
        except GitError as exc:
            err.write(f"git error: {exc}\n")
            err.flush()
        return

    if cmd in ("pop", "apply"):
        idx = int(args[0]) if args and args[0].isdigit() else 0
        try:
            repo.stash_pop(idx)
            out.write(f"✓ Applied and removed stash@{{{idx}}}.\n")
            out.flush()
        except GitError as exc:
            err.write(f"git error: {exc}\n")
            err.flush()
        return

    if cmd in ("drop", "delete"):
        idx = int(args[0]) if args and args[0].isdigit() else 0
        try:
            repo.stash_drop(idx)
            out.write(f"✓ Dropped stash@{{{idx}}}.\n")
            out.flush()
        except GitError as exc:
            err.write(f"git error: {exc}\n")
            err.flush()
        return

    err.write(f"Unknown stash subcommand '{subcmd}'. Usage: /stash [list|save|pop|drop]\n")
    err.flush()
