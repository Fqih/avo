"""Interactive chat REPL wiring AgentRuntime + app_tools + SQLite.

The setup wizard lives in :mod:`avo.chat_setup` and the
shell-rc persistence helpers live in :mod:`avo.chat_shell_rc`.
Both modules are re-exported here for backward compatibility with
existing imports (``from avo.chat import interactive_first_run_setup``)
so the public surface stays stable.

Conversation threading lives in :mod:`avo.chat_session`. The
REPL persists every user input + assistant reply through
:class:`avo.storage.conversations.ConversationStore`, sharing the
SQLite file with the event store so a single file holds both the run
history and the chat thread.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from avo import __version__ as AVO_VERSION
from avo import runtime as _runtime  # noqa: F401  (typing hook)
from avo.app_tools import git_commit_tool, git_diff_tool, lint_tool, test_runner_tool
from avo.app_tools.file_tools import bind_workspace, read_file_tool, write_file_tool
from avo.app_tools.workspace import Workspace
from avo.background import BackgroundJobManager, render_job_detail, render_job_row
from avo.chat_session import (
    SessionInfo,
    SessionLifecycle,
    render_session_picker,
    render_session_row,
    resolve_session_id,
)
from avo.chat_setup import interactive_first_run_setup  # re-export
from avo.chat_shell_rc import (  # re-export
    _detect_shell_rc_path,
    _offer_persist_to_shell_rc,
    _quote_for_shell,
    persist_env_to_shell_rc,
)
from avo.config import (
    ConfigError,
    available_models,
    build_provider_from_env,
    default_model,
    is_known_model,
    supported_providers,
)
from avo.exceptions import AvoError
from avo.providers.streaming import split_thinking
from avo.runtime import AgentRuntime
from avo.skills import SkillRegistry
from avo.storage.sqlite import SQLiteEventStore
from avo.tracing import TraceInspector

REPO_LOGO_PATH = Path(__file__).resolve().parents[3] / "public" / "logo.webp"

_FIRST_RUN_MESSAGE = (
    "Avo is not configured yet.\n"
    "\n"
    "No provider has been configured.\n"
    "\n"
    "Configure one of:\n"
    "\n"
    "  AVO_PROVIDER=ollama\n"
    "  AVO_PROVIDER=openrouter\n"
    "  AVO_PROVIDER=router\n"
    "  AVO_PROVIDER=minimax\n"
    "  AVO_PROVIDER=anthropic\n"
    "  AVO_PROVIDER=openai\n"
    "\n"
    "with the matching provider-specific keys and model. Or run `avo login` "
    "to authenticate via OAuth. See .env.example for all variables.\n"
    "\n"
    "Then retry:\n"
    "\n"
    "  avo chat\n"
)


@dataclass
class ChatContext:
    """Everything the REPL keeps alive across turns."""

    runtime: AgentRuntime
    store: SQLiteEventStore
    workspace: Workspace
    provider_name: str
    model_name: str
    skills: SkillRegistry
    session: SessionLifecycle
    session_id: str
    pending_preamble: str | None = None
    background: BackgroundJobManager = field(default_factory=BackgroundJobManager)


def _read_environ() -> dict[str, str]:
    """Snapshot ``os.environ`` so the chat does not see mid-session mutations."""

    return dict(os.environ)


def _resolve_provider_label(environ: dict[str, str]) -> tuple[str, str]:
    """Return ``(provider_name, model_name)`` without exposing secrets."""

    provider = environ.get("AVO_PROVIDER", "").strip() or "(unset)"
    if provider == "router":
        chain = environ.get("AVO_ROUTER_PROVIDERS", "").strip()
        models = environ.get("AVO_ROUTER_MODELS", "").strip()
        model_display = f"{chain} [{models}]" if chain and models else "fallback-chain"
        return provider, model_display

    model = (
        environ.get("AVO_MODEL")
        or environ.get("MODEL_MINIMAX")
        or environ.get("OPENAI_MODEL")
        or "(unset)"
    )
    return provider, model


def _new_session_id() -> str:
    """Generate a short, human-readable session id."""

    return uuid.uuid4().hex[:12]


def _print_header(
    out: TextIO,
    ctx: ChatContext,
    workspace_root: Path,
    *,
    resumed_from: str | None = None,
) -> None:
    """Render the AVO banner with version, model, session, and cwd.

    The header is a compact ASCII box so it survives every terminal
    width without word-wrap damage. Labels are fixed-width so the
    values line up. ``cwd`` is the resolved absolute path of the active
    workspace; ``session`` is the chat thread id (uuid-prefix); and
    ``model`` is whatever the runtime actually selected from the
    provider config.
    """

    cwd = Path.cwd()
    rows: list[tuple[str, str]] = [
        ("provider", f"{ctx.provider_name}"),
        ("model", f"{ctx.model_name}"),
        ("session", ctx.session_id),
        ("workspace", str(workspace_root)),
        ("cwd", str(cwd)),
        ("python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
    ]
    if resumed_from:
        rows.append(("resumed", resumed_from))

    label_width = max(len(label) for label, _ in rows)
    title_prefix = f" AVO v{AVO_VERSION} "
    max_value_width = max(len(value) for _, value in rows)
    inner_width = max(
        len(title_prefix) + 4,
        label_width + 3 + max_value_width,  # "label : value"
    )
    inner_width = max(inner_width, 40)
    inner_width = min(inner_width, 100)

    def _fit(value: str) -> str:
        budget = inner_width - label_width - 3
        if len(value) >= budget:
            return value[: max(budget - 3, 0)] + "..."
        return value.ljust(budget)

    title_dash_count = inner_width - len(title_prefix)
    out.write("\n")
    out.write(f"╭{title_prefix}{'─' * title_dash_count}╮\n")
    for label, value in rows:
        padded_label = label.ljust(label_width)
        out.write(f"│{padded_label} : {_fit(value)}│\n")
    out.write(f"╰{'─' * inner_width}╯\n")
    out.write("Type /help for the full slash command list.\n")
    out.write("Enter a task to run one AgentRuntime turn. Ctrl+D or /quit to exit.\n")
    out.flush()


SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "show this command list"),
    ("/provider", "show provider/model/API-key status"),
    ("/router", "show multi-provider fallback router live status"),
    ("/model [NAME]", "list known models, or switch to NAME or PROVIDER/MODEL"),
    ("/context", "display full context snapshot (session, model, workspace, skills)"),
    ("/cost", "show token usage and spend breakdown from ledger"),
    ("/diff [PATH]", "show git status, diff stat, or unified diff for PATH"),
    ("/undo", "revert uncommitted workspace modifications"),
    ("/lint [PATH]", "run code linter and syntax checks on workspace files"),
    ("/test [TARGET]", "run automated test suite on workspace files"),
    ("/commit [MSG]", "stage changes and create atomic git commit (auto-message if omitted)"),
    ("/bench [PROMPT]", "benchmark live routes and display speed ranking"),
    ("/clear", "clear the terminal screen"),
    ("/export [PATH]", "export current chat session to Markdown file"),
    ("/sessions", "list past chat sessions"),
    ("/resume [ID]", "resume a chat session (no arg = picker) or a recorded run"),
    ("/session", "show the current session id and turn count"),
    ("/new", "close the current session and start a fresh thread"),
    ("/inspect RUN_ID", "render the trace for one recorded run"),
    ("/skills", "list skills available in the current workspace"),
    ("/skill NAME", "load a skill body as the next turn"),
    ("/jobs", "list background tasks"),
    ("/job ID", "show one background task"),
    ("/cancel ID", "cancel a running background task"),
    ("/quit (or /exit, Ctrl+D)", "leave the chat"),
)


def _print_slash_help(out: TextIO) -> None:
    """Render the slash-command reference box."""

    width = max(len(name) for name, _ in SLASH_COMMANDS) + 2
    out.write("\n")
    out.write("╭─ Slash commands " + "─" * max(width - len("─ Slash commands"), 1) + "╮\n")
    for name, summary in SLASH_COMMANDS:
        out.write(f"│ {name.ljust(width - 1)} {summary} │\n".rstrip() + "\n")
    out.write("╰" + "─" * (width + max(len(summary) for _, summary in SLASH_COMMANDS) + 2) + "╯\n")
    out.flush()


def _print_provider_summary(out: TextIO, ctx: ChatContext, environ: dict[str, str]) -> None:
    """Display provider/model without leaking API keys or tokens."""

    out.write(f"Provider: {ctx.provider_name}\n")
    out.write(f"Model: {ctx.model_name}\n")
    if ctx.provider_name == "router":
        chain = environ.get("AVO_ROUTER_PROVIDERS", "ollama,openrouter")
        models = environ.get("AVO_ROUTER_MODELS", "default")
        out.write(f"Router Fallback Chain: {chain}\n")
        out.write(f"Router Models Chain:   {models}\n")
    base_url = environ.get(f"AVO_{ctx.provider_name.upper()}_BASE_URL", "") or "(default)"
    out.write(f"Base URL: {base_url}\n")
    has_key = bool(
        environ.get(f"AVO_{ctx.provider_name.upper()}_API_KEY", "").strip()
        or (ctx.provider_name == "openrouter" and environ.get("OPENROUTER_API_KEY", "").strip())
    )
    out.write(f"API key configured: {'yes' if has_key else 'no'}\n")
    out.flush()


def _print_active_context(out: TextIO, ctx: ChatContext, environ: dict[str, str]) -> None:
    """Render a comprehensive snapshot of the active chat context."""

    turns = ctx.session.turns(ctx.session_id)
    skills_list = ctx.skills.names()
    running_jobs = ctx.background.running_count

    rows: list[tuple[str, str]] = [
        ("Session ID", ctx.session_id),
        ("Turn Count", f"{len(turns)} turn(s) recorded"),
        ("Provider", ctx.provider_name),
        ("Model", ctx.model_name),
        ("Workspace", str(ctx.workspace.root)),
        (
            "Skills Active",
            f"{len(skills_list)} installed ({', '.join(skills_list[:3])})"
            if skills_list
            else "none",
        ),
        ("Background Jobs", f"{running_jobs} active running"),
    ]

    label_w = max(len(k) for k, _ in rows)
    out.write("\n╭─ Active Context ────────────────────────────────────────╮\n")
    for k, v in rows:
        out.write(f"│ {k.ljust(label_w)} : {v}\n")
    out.write("╰─────────────────────────────────────────────────────────╯\n")
    out.flush()


def _show_cost_breakdown(database_path: Path, out: TextIO) -> None:
    """Display aggregated token usage and USD spend from persistent ledger."""
    from avo.cost import aggregate_costs

    report = aggregate_costs(database_path)
    out.write(report.to_text())
    out.flush()


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


def _clear_screen(out: TextIO) -> None:
    """Clear the terminal screen and reset cursor."""

    out.write("\033[2J\033[H")
    out.flush()


def _export_session_markdown(
    ctx: ChatContext,
    target_path_str: str | None,
    out: TextIO,
    err: TextIO,
) -> None:
    """Export the current session thread to a markdown file."""
    turns = ctx.session.turns(ctx.session_id)
    if not turns:
        err.write(f"Session {ctx.session_id!r} has no turns to export.\n")
        return

    if target_path_str:
        target = Path(target_path_str)
        if not target.is_absolute():
            target = ctx.workspace.root / target
    else:
        target = ctx.workspace.root / f"avo-session-{ctx.session_id}.md"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        content = ctx.session.export_markdown(
            ctx.session_id,
            title=f"Avo Chat Session ({ctx.session_id})",
        )
        target.write_text(content, encoding="utf-8")
        out.write(
            f"Exported session {ctx.session_id} ({len(turns)} turns) to: {target.resolve()}\n"
        )
        out.flush()
    except Exception as exc:
        err.write(f"Failed to export session: {exc}\n")
        err.flush()


def _show_router_status(ctx: ChatContext, out: TextIO) -> None:
    """Display real-time router circuit breaker and health status."""
    from avo.providers.router import BaseRouterProvider

    provider = ctx.runtime.provider
    if not isinstance(provider, BaseRouterProvider):
        out.write(
            f"Router is not active (current provider: {ctx.provider_name!r}).\n"
            "To use multi-provider fallback or race routing, start with:\n"
            "  AVO_PROVIDER=router avo chat\n"
        )
        out.flush()
        return

    status = provider.get_health_status()
    routes = provider.routes
    strategy_label = provider.strategy.capitalize()
    out.write(
        f"Multi-Provider {strategy_label} Router Status "
        f"(cooldown: {provider.cooldown_seconds}s):\n\n"
    )
    col_hdr = (
        f"  {'Route':<12} {'Role':<8} {'Status':<9} {'Cooldown':<8} {'Latency':<8} {'Fails':<5}\n"
    )
    col_div = f"  {'-' * 12} {'-' * 8} {'-' * 9} {'-' * 8} {'-' * 8} {'-' * 5}\n"
    out.write(col_hdr)
    out.write(col_div)
    for i, (name, _) in enumerate(routes):
        if provider.strategy == "race":
            role = "Racer"
        elif i == 0:
            role = "Primary"
        else:
            role = "Fallback"
        info = status.get(name, {})
        healthy = info.get("healthy", True)
        in_cooling = info.get("in_cooldown", False)
        status_label = "COOLING" if in_cooling else ("HEALTHY" if healthy else "UNHEALTHY")
        cooldown_rem = f"{info.get('cooldown_remaining_seconds', 0.0)}s" if in_cooling else "0s"
        latency_val = info.get("last_latency_ms")
        latency_str = f"{latency_val:.1f}ms" if latency_val is not None else "-"
        fails = str(info.get("consecutive_failures", 0))

        row = (
            f"  {name:<12} {role:<8} {status_label:<9} "
            f"{cooldown_rem:<8} {latency_str:<8} {fails:<5}\n"
        )
        out.write(row)
    out.write("\n")
    out.flush()


def build_chat_context(
    *,
    database_path: Path,
    workspace_root: Path,
    environ: dict[str, str],
    session_id: str | None = None,
    force_new_session: bool = False,
) -> ChatContext:
    """Construct the runtime + store + workspace bound together.

    ``session_id`` optionally binds the chat thread to an existing
    session (used by ``/resume`` and the ``--session`` CLI flag). When
    ``force_new_session`` is true the chat always opens a fresh uuid
    thread even if ``session_id`` was provided. Raises
    :class:`AvoError` (or a subclass) on bad paths; ``ConfigError``
    propagates from :func:`build_provider_from_env`.
    """

    if database_path is None:
        raise AvoError("database_path must be a Path, not None; the CLI is misconfigured.")
    db_path = Path(database_path)

    provider_name, model_name = _resolve_provider_label(environ)
    provider = build_provider_from_env(environ)
    workspace = Workspace(workspace_root, create=False)
    store = SQLiteEventStore(db_path)
    runtime = AgentRuntime(
        provider=provider,
        event_store=store,
        tools=[
            read_file_tool(),
            write_file_tool(),
            lint_tool(),
            test_runner_tool(),
            git_diff_tool(),
            git_commit_tool(),
        ],
    )
    skills_root = workspace_root / ".avo" / "skills"
    # Ensure the skills directory exists for first-run use, but the
    # registry itself walks a path — body lookup happens lazily.
    skills_root.mkdir(parents=True, exist_ok=True)
    skills = SkillRegistry(skills_root)
    session = SessionLifecycle.open(db_path)
    if force_new_session or session_id is None:
        return ChatContext(
            runtime=runtime,
            store=store,
            workspace=workspace,
            provider_name=provider_name,
            model_name=model_name,
            skills=skills,
            session=session,
            session_id=_new_session_id(),
        )
    if not session.session_exists(session_id):
        session.close()
        raise AvoError(f"session {session_id!r} does not exist; nothing to resume.")
    preamble = session.build_preamble(session_id)
    return ChatContext(
        runtime=runtime,
        store=store,
        workspace=workspace,
        provider_name=provider_name,
        model_name=model_name,
        skills=skills,
        session=session,
        session_id=session_id,
        pending_preamble=preamble,
    )


def render_first_run_message(out: TextIO) -> None:
    """Print the canonical first-run configuration hint."""

    out.write(_FIRST_RUN_MESSAGE)
    out.flush()


async def _run_slash(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
) -> bool:
    """Dispatch a slash command. Returns True if the REPL should exit."""

    if not args:
        err.write("usage: try /help to list slash commands\n")
        return False

    cmd = args[0]

    if cmd == "/help":
        _print_slash_help(out)
        return False

    if cmd in ("/quit", "/exit"):
        return True

    if cmd == "/provider":
        _print_provider_summary(out, ctx, environ)
        return False

    if cmd == "/router":
        _show_router_status(ctx, out)
        return False

    if cmd == "/export":
        target = args[1] if len(args) > 1 else None
        _export_session_markdown(ctx, target, out, err)
        return False

    if cmd == "/context":
        _print_active_context(out, ctx, environ)
        return False

    if cmd == "/cost":
        _show_cost_breakdown(ctx.store.path, out)
        return False

    if cmd == "/diff":
        target = args[1] if len(args) > 1 else None
        _show_diff_summary(ctx.workspace.root, out, err, target)
        return False

    if cmd == "/undo":
        _undo_workspace(ctx.workspace.root, out, err)
        return False

    if cmd == "/lint":
        target = args[1] if len(args) > 1 else None
        _run_workspace_lint(ctx.workspace.root, out, err, target)
        return False

    if cmd == "/test":
        target = args[1] if len(args) > 1 else None
        _run_workspace_tests(ctx.workspace.root, out, err, target)
        return False

    if cmd == "/commit":
        msg = " ".join(args[1:]) if len(args) > 1 else None
        _run_workspace_commit(ctx.workspace.root, out, err, msg)
        return False

    if cmd == "/bench":
        prompt_arg = " ".join(args[1:]) if len(args) > 1 else "Explain recursion in 10 words."
        await _run_bench_command(ctx, prompt_arg, out, err)
        return False

    if cmd == "/clear":
        _clear_screen(out)
        return False

    if cmd == "/model":
        return await _run_model_command(ctx, args, out, err, environ)

    if cmd == "/sessions":
        infos = ctx.session.list_sessions()
        out.write(render_session_picker(infos))
        return False

    if cmd == "/session":
        last = ctx.session.last_turn(ctx.session_id)
        turn_count = len(ctx.session.turns(ctx.session_id))
        out.write(f"Current session: {ctx.session_id}\n")
        out.write(f"Turns so far: {turn_count}\n")
        if last is not None:
            out.write(f"Last activity: {last.created_at.isoformat()}\n")
        return False

    if cmd == "/new":
        old = ctx.session_id
        ctx.session_id = _new_session_id()
        ctx.pending_preamble = None
        out.write(f"Closed session {old}; started fresh session {ctx.session_id}.\n")
        return False

    if cmd == "/inspect":
        if len(args) != 2:
            err.write("usage: /inspect RUN_ID\n")
            return False
        try:
            trace = await TraceInspector(ctx.store).inspect(args[1])
        except AvoError as exc:
            err.write(f"inspect failed: {exc}; check /runs list for valid ids\n")
            return False
        out.write(trace.to_text())
        out.write("\n")
        return False

    if cmd == "/resume":
        # Two distinct resume shapes coexist here:
        #   /resume RUN_ID        -- resume a persisted runtime run (existing behaviour)
        #   /resume SESSION_ID    -- load a chat session thread for the next turn
        #   /resume (no args)     -- interactive picker over past chat sessions
        # /resume SESSION_ID wins over /resume RUN_ID when the arg
        # resolves to a known session — runtime-run ids never collide
        # with the short hex session ids we mint in ``_new_session_id``.
        if len(args) == 1:
            infos = ctx.session.list_sessions()
            if not infos:
                err.write("no previous chat sessions to resume.\n")
                return False
            out.write(render_session_picker(infos))
            return False
        if len(args) == 2:
            arg = args[1]
            infos = ctx.session.list_sessions()
            resolved = resolve_session_id(arg, infos)
            if resolved is not None:
                return await _resume_chat_session(ctx, resolved, out, err)
            # Fall back to runtime-run resume (existing behaviour).
            try:
                result = await ctx.runtime.resume(arg)
            except AvoError as exc:
                err.write(f"resume failed: {exc}\n")
                err.write(
                    "hint: /resume SESSION_ID resumes a chat thread; "
                    "/resume RUN_ID replays a recorded run\n"
                )
                return False
            out.write(
                f"Resumed run {result.run_id}: status={result.status.value} "
                f"stop_reason={result.stop_reason.value} steps={result.steps}\n"
            )
            if result.output:
                out.write(f"output: {result.output}\n")
            return False
        err.write("usage: /resume [SESSION_ID|RUN_ID]\n")
        return False

    if cmd == "/skills":
        names = ctx.skills.names()
        if not names:
            out.write(f"No skills found under {ctx.skills.root}\n")
            return False
        for name in names:
            out.write(f"  {name}\n")
        return False

    if cmd == "/skill":
        if len(args) != 2:
            err.write("usage: /skill NAME\n")
            return False
        try:
            body = ctx.skills.load(args[1])
        except AvoError as exc:
            err.write(f"skill load failed: {exc}; try /skills to list installed skills\n")
            return False
        # Skill bodies are injected as a user message so the runtime
        # treats them like any other turn input — no separate channel.
        await _run_turn(ctx, body, out, err)
        return False

    if cmd == "/jobs":
        jobs = ctx.background.list_jobs()
        if not jobs:
            out.write("No background jobs.\n")
            return False
        for j in jobs:
            out.write(render_job_row(j) + "\n")
        return False

    if cmd == "/job":
        if len(args) != 2:
            err.write("usage: /job JOB_ID\n")
            return False
        job = ctx.background.get(args[1])
        if job is None:
            err.write(f"unknown job id: {args[1]}; try /jobs to list ids\n")
            return False
        out.write(render_job_detail(job) + "\n")
        return False

    if cmd == "/cancel":
        if len(args) != 2:
            err.write("usage: /cancel JOB_ID\n")
            return False
        cancelled = await ctx.background.cancel(args[1])
        if not cancelled:
            err.write(
                f"could not cancel {args[1]}: unknown id or already terminal; "
                "try /jobs to list ids and statuses\n"
            )
            return False
        out.write(f"Cancellation requested for job {args[1]}.\n")
        return False

    err.write(f"unknown command: {cmd}; try /help to list slash commands\n")
    return False


async def _run_model_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
) -> bool:
    """Handle ``/model`` — list the catalog or switch to a specific model.

    No arguments renders a numbered picker (current model marked with
    ``*``); a single argument swaps the runtime's provider in place so
    the very next turn talks to the new model.
    """

    provider_name = ctx.provider_name
    catalog = available_models(provider_name)
    if not catalog:
        err.write(
            f"provider {provider_name!r} has no model catalog; "
            f"set AVO_MODEL=<name> in your environment to override.\n"
        )
        return False

    if len(args) == 1:
        out.write(f"Models for provider {provider_name!r} (current: {ctx.model_name!r}):\n")
        recommended = default_model(provider_name)
        for index, name in enumerate(catalog, start=1):
            marker = "*" if name == ctx.model_name else " "
            hint = " (recommended)" if name == recommended else ""
            out.write(f"  {marker} {index}. {name}{hint}\n")
        out.write("Pick a model with: /model NAME\n")
        return False

    if len(args) == 2:
        target = args[1].strip()
        if not target:
            err.write("usage: /model [NAME|PROVIDER/MODEL]\n")
            return False

        if "/" in target:
            new_prov, new_mod = target.split("/", 1)
            new_prov = new_prov.strip().lower()
            new_mod = new_mod.strip()
            if new_prov not in supported_providers():
                supported_list = ", ".join(supported_providers())
                err.write(f"unknown provider {new_prov!r}. Supported: {supported_list}\n")
                return False
            environ["AVO_PROVIDER"] = new_prov
            environ["AVO_MODEL"] = new_mod
            os.environ["AVO_PROVIDER"] = new_prov
            os.environ["AVO_MODEL"] = new_mod
            try:
                ctx.runtime.provider = build_provider_from_env(environ)
            except Exception as exc:
                err.write(f"provider switch failed: {exc}\n")
                return False
            ctx.provider_name = new_prov
            ctx.model_name = new_mod
            out.write(
                f"Switched to provider {new_prov!r} and model {new_mod!r}. "
                f"Next turn will use the new model.\n"
            )
            out.flush()
            return False

        if not is_known_model(provider_name, target):
            err.write(
                f"{target!r} is not in the {provider_name!r} catalog. "
                f"Run /model to see the available list.\n"
            )
            return False
        environ["AVO_MODEL"] = target
        os.environ["AVO_MODEL"] = target
        try:
            ctx.runtime.provider = build_provider_from_env(environ)
        except ConfigError as exc:
            err.write(f"model switch failed: {exc}\n")
            return False
        ctx.model_name = target
        out.write(
            f"Switched provider {provider_name!r} to model {target!r}. "
            f"Next turn will use the new model.\n"
        )
        return False

    err.write("usage: /model [NAME]\n")
    return False


def _prompt_with_jobs(prompt: str, manager: BackgroundJobManager) -> str:
    """Render the REPL prompt with a trailing ``[jobs: N]`` counter.

    Inactive when no background tasks are running so the prompt stays
    clean during normal usage.
    """

    running = manager.running_count
    if running == 0:
        return prompt
    return f"{prompt}[jobs: {running} running] "


async def _resume_chat_session(
    ctx: ChatContext,
    session_id: str,
    out: TextIO,
    err: TextIO,
) -> bool:
    """Switch the current thread to ``session_id`` and stage its preamble."""

    if session_id == ctx.session_id:
        out.write(f"Already on session {session_id}.\n")
        return False
    if not ctx.session.session_exists(session_id):
        err.write(f"session {session_id!r} does not exist.\n")
        return False
    try:
        preamble = ctx.session.build_preamble(session_id)
    except AvoError as exc:
        err.write(f"could not build resume preamble: {exc}\n")
        return False
    ctx.session_id = session_id
    ctx.pending_preamble = preamble
    turns = ctx.session.turns(session_id)
    out.write(
        f"Resumed session {session_id} with {len(turns)} prior turn(s). "
        "Next user message will be sent as a continuation.\n"
    )
    return False


async def _run_turn(ctx: ChatContext, task: str, out: TextIO, err: TextIO) -> None:
    """Execute one user turn against ``ctx.runtime``."""

    ctx.session.record_user_turn(ctx.session_id, task)
    effective_task = task
    if ctx.pending_preamble is not None:
        effective_task = (
            f"{ctx.pending_preamble}\n\n"
            f"---\n"
            f"User's current message (continue directly without greeting):\n{task}"
        )
        ctx.pending_preamble = None

    with bind_workspace(ctx.workspace):
        try:
            result = await ctx.runtime.run(effective_task)
        except AvoError as exc:
            err.write(f"runtime error: {exc}\n")
            return
        except Exception as exc:
            err.write(f"unexpected error: {type(exc).__name__}: {exc}\n")
            return

    assistant_content = result.output or ""
    thought, clean_answer = split_thinking(assistant_content)

    ctx.session.record_assistant_turn(
        ctx.session_id,
        clean_answer or assistant_content,
        run_id=result.run_id,
        status=result.status.value,
        stop_reason=result.stop_reason.value,
    )

    out.write(
        f"Avo [{result.status.value}/{result.stop_reason.value}] "
        f"steps={result.steps} run_id={result.run_id}\n"
    )
    if result.error:
        out.write(f"error: {result.error}\n")
    if thought:
        out.write("\n💭 Thought process:\n")
        for thought_line in thought.splitlines():
            out.write(f"  │ {thought_line}\n")
        out.write("\n")
    if clean_answer:
        out.write(f"Avo> {clean_answer}\n")
    elif result.output:
        out.write(f"Avo> {result.output}\n")
    out.flush()


def _maybe_offer_resume_prompt(
    session: SessionLifecycle,
    out: TextIO,
    in_stream: TextIO,
) -> SessionInfo | None:
    """If the last session is recent, offer to resume it. Return the chosen row.

    Used as a best-effort UX hook on REPL startup. Returns ``None``
    when there are no recent sessions or the operator declines. Does
    not block on EOF.
    """

    recent = session.find_resumable(limit=1)
    if not recent:
        return None
    info = recent[0]
    out.write(f"Resume session {info.session_id} ({render_session_row(info)})? [Y/n] ")
    out.flush()
    try:
        answer = in_stream.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        out.write("\n")
        return None
    if answer in ("", "y", "yes"):
        return info
    return None


async def run_repl(
    *,
    database_path: Path,
    workspace_root: Path,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environ: dict[str, str] | None = None,
    prompt: str = "You > ",
    secret_reader: Callable[[str], str] | None = None,
    session_id: str | None = None,
    force_new_session: bool = False,
) -> int:
    """Run the interactive chat REPL until EOF, /quit, or fatal init error.

    ``session_id`` and ``force_new_session`` mirror the ``--session``
    and ``--new-session`` CLI flags. When both are ``None``/``False``
    the REPL offers to resume the most-recent session before booting.
    """

    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    env = environ if environ is not None else _read_environ()

    try:
        ctx = build_chat_context(
            database_path=database_path,
            workspace_root=workspace_root,
            environ=env,
            session_id=session_id,
            force_new_session=force_new_session,
        )
    except ConfigError:
        new_env = interactive_first_run_setup(in_stream, out_stream, secret_reader=secret_reader)
        if new_env is None:
            return 2
        env = {**env, **new_env}
        try:
            ctx = build_chat_context(
                database_path=database_path,
                workspace_root=workspace_root,
                environ=env,
                session_id=session_id,
                force_new_session=force_new_session,
            )
        except (AvoError, OSError) as exc:
            err_stream.write(f"avo chat: {exc}\n")
            return 2
        except ConfigError as exc:
            err_stream.write(f"configuration still invalid after setup: {exc}\n")
            return 2
    except (AvoError, OSError) as exc:
        err_stream.write(f"avo chat: {exc}\n")
        return 2

    resumed_from: str | None = None
    if session_id is not None and not force_new_session:
        resumed_from = session_id
    elif session_id is None and not force_new_session:
        offered = _maybe_offer_resume_prompt(ctx.session, out_stream, in_stream)
        if offered is not None:
            ctx.session_id = offered.session_id
            try:
                ctx.pending_preamble = ctx.session.build_preamble(offered.session_id)
            except AvoError:
                ctx.pending_preamble = None
            resumed_from = offered.session_id

    _print_header(out_stream, ctx, workspace_root, resumed_from=resumed_from)
    out_stream.write("\n")
    out_stream.flush()

    try:
        while True:
            try:
                out_stream.write(_prompt_with_jobs(prompt, ctx.background))
                out_stream.flush()
                line = in_stream.readline()
            except KeyboardInterrupt:
                out_stream.write("\n(interrupted - type /quit or Ctrl+D to exit)\n")
                out_stream.flush()
                continue

            if not line:
                out_stream.write("\n")
                return 0
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("/"):
                try:
                    args = shlex.split(stripped)
                except ValueError as exc:
                    err_stream.write(f"parse error: {exc}\n")
                    continue
                should_exit = await _run_slash(ctx, args, out_stream, err_stream, env)
                if should_exit:
                    return 0
                continue

            background_requested = stripped.endswith("&") and not stripped.endswith("&&")
            if background_requested:
                task_text = stripped[:-1].rstrip()
                job = ctx.background.submit(ctx, task_text)
                out_stream.write(
                    f"Backgrounded job {job.job_id}: {task_text}\n"
                    f"  watch with /jobs, inspect with /job {job.job_id}, "
                    f"cancel with /cancel {job.job_id}.\n"
                )
                out_stream.flush()
                continue

            await _run_turn(ctx, stripped, out_stream, err_stream)
    finally:
        await ctx.background.wait_all()
        await ctx.store.close()
        ctx.session.close()


__all__ = [
    "REPO_LOGO_PATH",
    "ChatContext",
    "_detect_shell_rc_path",
    "_offer_persist_to_shell_rc",
    "_print_header",
    "_print_provider_summary",
    "_quote_for_shell",
    "_read_environ",
    "_resolve_provider_label",
    "_run_slash",
    "_run_turn",
    "build_chat_context",
    "interactive_first_run_setup",
    "persist_env_to_shell_rc",
    "render_first_run_message",
    "run_repl",
]
