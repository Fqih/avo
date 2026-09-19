"""Slash-command dispatcher and session-management commands.

:data:`_run_slash` routes every ``/command`` to its handler: the
render helpers, the workspace commands, and the turn-level commands
that live in sibling modules. Persona, instructions, draft, permission
management plus history export/compact/search stay here with the
dispatcher.

Re-exported from :mod:`avo.chat` for backward compatibility.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from avo.agent_profiles import AgentProfileError
from avo.background import render_job_detail, render_job_row
from avo.chat_render import (
    _clear_screen,
    _new_session_id,
    _print_active_context,
    _print_provider_summary,
    _print_slash_help,
    _show_cost_breakdown,
)
from avo.chat_session import SessionInfo, render_session_picker, resolve_session_id
from avo.chat_turn import _resume_chat_session, _run_agent_request, _run_model_command, _run_turn
from avo.chat_workspace_commands import (
    _manage_branch,
    _manage_stash,
    _run_bench_command,
    _run_find_command,
    _run_grep_command,
    _run_repl_shell,
    _run_symbols_command,
    _run_workspace_commit,
    _run_workspace_lint,
    _run_workspace_map_command,
    _run_workspace_tests,
    _show_diff_summary,
    _show_git_log,
    _undo_workspace,
)
from avo.exceptions import AvoError
from avo.permissions import PermissionMode, PermissionPolicy, build_approval_callback
from avo.replay import replay_run
from avo.tracing import TraceInspector

if TYPE_CHECKING:
    from avo.chat import ChatContext


def _manage_persona(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """List available personas, register new custom persona, or switch active persona."""
    if not args:
        current = ctx.persona.active_persona or "(default)"
        out.write(f"Active persona: {current}\n\n")
        out.write("Available built-in personas:\n")
        for p_name, p_desc in ctx.persona.available_personas().items():
            first_line = p_desc.splitlines()[0]
            marker = " (active)" if p_name == ctx.persona.active_persona else ""
            out.write(f"  • {p_name:<12} - {first_line}{marker}\n")
        out.write(
            "\nUsage:\n"
            "  /persona <NAME>                 switch active persona\n"
            "  /persona add <NAME> <PROMPT>    create custom workspace persona\n"
            "  /persona clear                  reset to default\n"
        )
        out.flush()
        return

    sub = args[0].lower()
    if sub == "add":
        if len(args) < 3:
            err.write("usage: /persona add <NAME> <PROMPT>\n")
            err.flush()
            return
        p_name = args[1]
        p_prompt = " ".join(args[2:])
        try:
            ctx.persona.register_persona(p_name, p_prompt, persist=True)
            ctx.persona.set_persona(p_name)
            out.write(f"✓ Created and activated custom persona {p_name!r}.\n")
            out.flush()
        except ValueError as exc:
            err.write(f"error: {exc}\n")
            err.flush()
        return

    if sub in ("clear", "reset", "none", "off"):
        ctx.persona.set_persona(None)
        out.write("✓ Reset persona to default.\n")
        out.flush()
        return

    try:
        ctx.persona.set_persona(sub)
        out.write(f"✓ Switched persona to: {sub}\n")
        out.flush()
    except ValueError as exc:
        err.write(f"{exc}\n")
        err.flush()


def _manage_instructions(
    ctx: ChatContext,
    text_arg: str | None,
    out: TextIO,
    err: TextIO,
) -> None:
    """View or set custom workspace instructions."""
    if not text_arg:
        current = ctx.persona.custom_instructions
        if current:
            out.write("Active workspace instructions:\n")
            out.write(f"{current}\n")
        else:
            out.write("No custom workspace instructions configured.\n")
            out.write("Tip: create .avo/instructions.md or set with /instructions <TEXT>\n")
        out.flush()
        return

    if text_arg.lower() in ("clear", "reset", "none", "off"):
        ctx.persona.set_custom_instructions(None)
        out.write("✓ Cleared workspace instructions.\n")
        out.flush()
        return

    ctx.persona.set_custom_instructions(text_arg)
    out.write("✓ Updated custom workspace instructions for this session.\n")
    out.flush()


def _manage_draft(
    ctx: ChatContext,
    subcommand: str | None,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """View, save, or clear persistent workspace draft prompt."""
    if ctx.history is None:
        err.write("draft manager is not initialized.\n")
        return

    sub = (subcommand or "show").lower()
    if sub == "show":
        draft = ctx.history.load_draft()
        if not draft:
            out.write("No saved draft prompt.\n")
        else:
            out.write("╭─ Saved Draft Prompt ────────────────────────────────╮\n")
            for line in draft.splitlines():
                out.write(f"│ {line}\n")
            out.write("╰─────────────────────────────────────────────────────╯\n")
            out.write("Tip: run '/draft clear' to discard.\n")
        out.flush()
        return

    if sub == "save":
        text = " ".join(args).strip()
        if not text:
            err.write("usage: /draft save PROMPT_TEXT\n")
            return
        ctx.history.save_draft(text)
        out.write(f"✓ Saved draft ({len(text)} chars) to {ctx.history.draft_file.name}\n")
        out.flush()
        return

    if sub in ("clear", "discard", "delete"):
        cleared = ctx.history.clear_draft()
        if cleared:
            out.write("✓ Draft cleared.\n")
        else:
            out.write("No draft to clear.\n")
        out.flush()
        return

    err.write(f"unknown draft subcommand {subcommand!r}; choose show, save, or clear\n")


def _manage_agent_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """Unified agent command for persona, workspace instructions, and active profile."""
    if len(args) <= 1:
        current_persona = ctx.persona.active_persona or "(default)"
        skills = ctx.skills.names()
        skills_str = ", ".join(skills[:5]) if skills else "(none)"
        instr_len = len(ctx.persona.custom_instructions or "")

        out.write("╭─ Agent Configuration ───────────────────────────────────╮\n")
        out.write(f"│ Active Persona : {current_persona:<39} │\n")
        out.write(f"│ Instructions   : {str(instr_len) + ' chars active':<39} │\n")
        active_skills = f"{len(skills)} installed ({skills_str})"
        out.write(f"│ Active Skills  : {active_skills:<39} │\n")
        out.write("╰─────────────────────────────────────────────────────────╯\n")
        out.write("Commands:\n")
        out.write("  /agent persona [NAME]        switch or list personas\n")
        out.write("  /agent instructions [TEXT]   view or set custom instructions\n")
        out.write("  /agent clear                 reset persona and instructions to default\n")
        out.flush()
        return

    sub = args[1].lower()
    if sub == "add":
        registry = getattr(ctx, "agent_profiles", None)
        if registry is None:
            err.write("agent profiles are not initialized for this chat context.\n")
            return
        if len(args) < 4:
            err.write("usage: /agent add NAME DESCRIPTION\n")
            return
        try:
            profile = registry.create(args[2], " ".join(args[3:]))
        except (AgentProfileError, OSError) as exc:
            err.write(f"agent creation failed: {exc}\n")
            return
        out.write(f"✓ Created @{profile.name}: {profile.description}\n")
        out.flush()
        return
    if sub == "persona":
        _manage_persona(ctx, args[2:], out, err)
        return
    if sub in ("instructions", "prompt"):
        text_arg = " ".join(args[2:]) if len(args) > 2 else None
        _manage_instructions(ctx, text_arg, out, err)
        return
    if sub in ("clear", "reset"):
        ctx.persona.set_persona(None)
        ctx.persona.set_custom_instructions(None)
        out.write("✓ Reset agent persona and workspace instructions to defaults.\n")
        out.flush()
        return
    _manage_persona(ctx, args[1:], out, err)


def _manage_agents_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """List named agent profiles available to the current workspace."""

    registry = getattr(ctx, "agent_profiles", None)
    if registry is None:
        err.write("agent profiles are not initialized for this chat context.\n")
        return
    if args and args[0].lower() not in {"list", "show"}:
        err.write("usage: /agents [list]\n")
        return
    out.write("Available agents:\n")
    for profile in registry.list():
        capability = "read-only" if profile.capability == "read_only" else "workspace"
        out.write(f"  @{profile.name:<12} {capability:<10} {profile.description}\n")
    if registry.warnings:
        out.write("Warnings:\n")
        for warning in registry.warnings:
            out.write(f"  {warning}\n")
    out.write("Use: @agent task  or  @agent one | @agent two\n")
    out.flush()


async def _manage_list_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
) -> None:
    """Browse catalog of sessions, models, skills, plugins, tools, or jobs."""
    if len(args) <= 1:
        out.write("Usage: /list [sessions|models|skills|plugins|tools|jobs|agents]\n\n")
        out.write("Available categories:\n")
        out.write("  /list sessions   - list conversation sessions\n")
        out.write("  /list models     - list available models for current provider\n")
        out.write("  /list skills     - list installed workspace skills\n")
        out.write("  /list plugins    - list installed CLI plugins\n")
        out.write("  /list tools      - list registered runtime app tools\n")
        out.write("  /list jobs       - list active background tasks\n")
        out.write("  /list agents     - list named agent profiles\n")
        out.flush()
        return

    cat = args[1].lower()
    if cat in ("sessions", "session"):
        infos = ctx.session.list_sessions()
        out.write(render_session_picker(infos))
        return
    if cat in ("models", "model"):
        await _run_model_command(ctx, ["/model"], out, err, environ)
        return
    if cat in ("skills", "skill"):
        names = ctx.skills.names()
        if not names:
            out.write(f"No skills found under {ctx.skills.root}\n")
        else:
            out.write(f"Installed skills ({len(names)}):\n")
            for name in names:
                out.write(f"  • {name}\n")
        out.flush()
        return
    if cat in ("plugins", "plugin"):
        _manage_plugin_command(ctx, ["/plugin", "list"], out, err)
        return
    if cat in ("tools", "tool"):
        tools = ctx.runtime.tools.metadata
        out.write(f"Registered tools ({len(tools)}):\n")
        for tool in tools:
            first_desc = tool.description.splitlines()[0] if tool.description else ""
            out.write(f"  • {tool.name:<20} {first_desc[:55]}\n")
        out.flush()
        return
    if cat in ("jobs", "job"):
        jobs = ctx.background.list_jobs()
        if not jobs:
            out.write("No background jobs.\n")
        else:
            for j in jobs:
                out.write(render_job_row(j) + "\n")
        out.flush()
        return
    if cat in ("agents", "agent"):
        _manage_agents_command(ctx, ["list"], out, err)
        return
    err.write(f"unknown list category: {cat!r}; try /list to see options\n")


def _manage_plugin_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
) -> None:
    """Manage third-party plugins (~/.avo/plugins) from inside chat REPL."""
    from avo.cli_plugins import PLUGIN_ROOT, _read_index

    if len(args) <= 1 or args[1].lower() == "list":
        index = _read_index()
        if not index:
            out.write(f"No plugins installed under {PLUGIN_ROOT}.\n")
            out.write("Install one with: /plugin install <git-url-or-path>\n")
            out.flush()
            return
        out.write(f"Installed plugins ({len(index)}):\n")
        for name, entry in sorted(index.items()):
            source = entry.get("source", "")
            editable = " (editable)" if entry.get("editable") else ""
            out.write(f"  • {name:<18} {source}{editable}\n")
        out.flush()
        return

    sub = args[1].lower()
    if sub == "show":
        if len(args) < 3:
            err.write("usage: /plugin show <NAME>\n")
            return
        from avo.cli_plugins import show as _show_plugin

        plugin = _show_plugin(args[2])
        out.write(f"name: {plugin.name}\nsource: {plugin.source}\npath: {plugin.path}\n")
        return

    if sub == "install":
        if len(args) < 3:
            err.write("usage: /plugin install <SOURCE>\n")
            return
        from avo.cli_plugins import install as _install_plugin

        try:
            plugin = _install_plugin(
                args[2], name=args[3] if len(args) > 3 else None, editable=True
            )
            out.write(f"Installed plugin {plugin.name!r} from {plugin.source} → {plugin.path}\n")
        except Exception as exc:
            err.write(f"plugin install failed: {exc}\n")
        return

    if sub in ("remove", "uninstall", "rm"):
        if len(args) < 3:
            err.write("usage: /plugin remove <NAME>\n")
            return
        from avo.cli_plugins import remove as _remove_plugin

        try:
            _remove_plugin(args[2])
            out.write(f"Removed plugin {args[2]!r}.\n")
        except Exception as exc:
            err.write(f"plugin remove failed: {exc}\n")
        return

    err.write(f"unknown plugin action: {sub!r}; try /plugin list\n")


def _manage_setup_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
    in_stream: TextIO | None = None,
) -> None:
    """Inspect or run setup for ~/.avo global configuration."""
    from avo.cli_setup import load_global_avo_config, render_setup_card, setup_global_avo

    color_enabled = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")
    try:
        report = setup_global_avo()
        configured_env = load_global_avo_config(report.base_dir)
        environ.update(configured_env)
        os.environ.update(configured_env)
        out.write(render_setup_card(report, color=color_enabled))
        out.flush()
    except (AvoError, OSError) as exc:
        err.write(f"setup failed: {exc}\n")
        err.flush()


def _manage_permissions(
    ctx: ChatContext,
    mode_arg: str | None,
    out: TextIO,
    err: TextIO,
    *,
    in_stream: TextIO | None = None,
) -> None:
    """Display or update the active tool permission mode."""
    if not mode_arg:
        current = ctx.permission_policy.mode.value
        out.write(f"Current permission mode: {current}\n")
        out.write("Available modes: default, accept_edits, plan, bypass_permissions\n")
        out.write("Switch mode with: /permissions <MODE>\n")
        out.flush()
        return

    try:
        new_mode = PermissionMode(mode_arg.lower())
    except ValueError:
        allowed = ", ".join(m.value for m in PermissionMode)
        err.write(f"Unknown permission mode '{mode_arg}'. Must be one of: {allowed}\n")
        err.flush()
        return

    new_policy = PermissionPolicy(
        mode=new_mode,
        require_approval=ctx.permission_policy.require_approval,
    )
    ctx.permission_policy = new_policy
    if new_mode is PermissionMode.BYPASS_PERMISSIONS:
        ctx.runtime._approval_callback = lambda call: True
    else:
        ctx.runtime._approval_callback = build_approval_callback(
            new_policy, stdin=in_stream, stdout=out
        )
    out.write(f"✓ Switched permission mode to: {new_mode.value}\n")
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


async def _compact_session_history(
    ctx: ChatContext,
    keep_last: int,
    out: TextIO,
    err: TextIO,
) -> None:
    """Compact chat session context by summarizing earlier turns."""
    from pydantic import JsonValue

    from avo.compact import compact_messages
    from avo.models import ModelRequest

    if keep_last < 1:
        err.write("keep_last must be at least 1\n")
        err.flush()
        return

    turns = ctx.session.turns(ctx.session_id)
    if not turns:
        out.write(f"Session {ctx.session_id} has no turns to compact.\n")
        out.flush()
        return

    if len(turns) <= keep_last + 1:
        out.write(
            f"Session {ctx.session_id} has {len(turns)} turn(s); already compact "
            f"(budget: {keep_last + 1}).\n"
        )
        out.flush()
        return

    out.write(f"Compacting session {ctx.session_id} ({len(turns)} turns)...\n")
    out.flush()

    messages: list[dict[str, JsonValue]] = [{"role": t.role, "content": t.content} for t in turns]

    async def _summarizer(middle: list[dict[str, Any]]) -> str:
        convo_snippet = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in middle[:15])
        prompt = (
            "Summarize the key tasks, decisions, and technical context from these "
            f"conversation turns in 2-4 concise bullet points:\n{convo_snippet}"
        )
        try:
            req = ModelRequest(
                run_id=f"compact-{ctx.session_id}-{int(time.time() * 1000)}",
                step=1,
                messages=[{"role": "user", "content": prompt}],
            )
            resp = await ctx.runtime.provider.generate(req)
            return (resp.content or "").strip()
        except Exception:
            return f"{len(middle)} earlier turn(s) covering: " + ", ".join(
                str(m.get("content", ""))[:40] for m in middle[:3]
            )

    compacted = await compact_messages(
        messages,
        keep_last=keep_last,
        keep_first_user=True,
        summarize_callable=_summarizer,
    )

    preamble_lines = [
        "You are continuing a compacted conversation. Summary of earlier context is below:",
        "",
    ]
    for m in compacted:
        role_label = (
            "User"
            if m.get("role") == "user"
            else ("Assistant" if m.get("role") == "assistant" else "System")
        )
        preamble_lines.append(f"{role_label}: {m.get('content')}")

    ctx.pending_preamble = "\n".join(preamble_lines)
    out.write(
        f"✓ Compacted session {ctx.session_id} from {len(turns)} turns to "
        f"{len(compacted)} context entries (keep_last={keep_last}).\n"
        "Summary of earlier conversation staged in active preamble for the next turn.\n"
    )
    out.flush()


def _show_chat_history(ctx: ChatContext, args: list[str], out: TextIO) -> None:
    """Display recent session history or search conversation turns."""
    query = " ".join(args).strip() if args else ""

    if query.isdigit():
        limit = max(1, int(query))
        query = ""
    else:
        limit = 10

    if query:
        matched = ctx.session.search_history(query, limit=25)
        if not matched:
            out.write(f"\nNo conversation turns matched {query!r}.\n\n")
            out.flush()
            return

        out.write(f"\n╭─ Search History: {query!r} (found {len(matched)} turns) ─╮\n")
        for t in matched:
            role_badge = f"[{t.role.upper()}]"
            date_str = t.created_at.strftime("%Y-%m-%d %H:%M:%S")
            snippet = t.content.strip()
            if len(snippet) > 160:
                snippet = snippet[:157] + "..."
            sid_short = t.session_id[:10]
            out.write(f"│ {role_badge:<11} seq={t.sequence:<3} sess={sid_short:<10} {date_str}\n")
            snippet_indented = snippet.replace("\n", " ")
            out.write(f"│   {snippet_indented}\n│\n")
        out.write("╰" + "─" * 65 + "╯\n\n")
        out.flush()
        return

    turns = ctx.session.turns(ctx.session_id)
    if not turns:
        out.write(f"\nNo turns recorded yet in active session {ctx.session_id!r}.\n\n")
        out.flush()
        return

    recent = turns[-limit:]
    out.write(f"\n╭─ Active Session History ({len(recent)} of {len(turns)} turns) ─╮\n")
    for t in recent:
        role_badge = f"[{t.role.upper()}]"
        date_str = t.created_at.strftime("%H:%M:%S")
        snippet = t.content.strip()
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        snippet_indented = snippet.replace("\n", " ")
        out.write(f"│ {role_badge:<13} seq={t.sequence:<3} {date_str}\n")
        out.write(f"│   {snippet_indented}\n│\n")
    out.write("╰" + "─" * 55 + "╯\n\n")
    out.flush()


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


def _show_combo_status(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
) -> None:
    """Display live health status of active combo tiers, or switch active combo profile."""
    import os

    from avo.combo.provider import ComboRouterProvider
    from avo.combo.store import get_combo, load_combos
    from avo.config import build_provider_from_env

    if len(args) > 1:
        target = args[1].strip()
        profile = get_combo(target)
        if profile is None:
            err.write(f"combo profile {target!r} not found.\n")
            err.flush()
            return
        environ["AVO_PROVIDER"] = "combo"
        environ["AVO_COMBO"] = target
        environ["AVO_MODEL"] = target
        os.environ["AVO_PROVIDER"] = "combo"
        os.environ["AVO_COMBO"] = target
        os.environ["AVO_MODEL"] = target
        try:
            ctx.runtime.provider = build_provider_from_env(environ)
        except Exception as exc:
            err.write(f"combo switch failed: {exc}\n")
            err.flush()
            return
        ctx.provider_name = "combo"
        ctx.model_name = target
        out.write(
            f"Switched to combo profile {target!r}. Next turn will route through its tiers.\n"
        )
        out.flush()
        return

    all_combos = load_combos()
    available = ", ".join(sorted(all_combos.keys()))

    provider = ctx.runtime.provider
    if not isinstance(provider, ComboRouterProvider):
        out.write(
            f"Combo routing is not active (current provider: {ctx.provider_name!r}).\n"
            f"Available profiles: {available}\n"
            "To activate a combo, run:\n"
            "  /combo <NAME>\n"
        )
        out.flush()
        return

    profile = provider.profile
    status = provider.get_health_status()
    out.write(f"Active Combo Profile: {profile.name}\n")
    if profile.description:
        out.write(f"Description: {profile.description}\n")
    out.write("\nTiers (priority order):\n")
    col_hdr = (
        f"  {'Tier':<14} {'Provider':<12} {'Model':<24} "
        f"{'Status':<9} {'Cooldown':<9} {'Latency':<8} {'Fails':<5}\n"
    )
    col_div = f"  {'-' * 14} {'-' * 12} {'-' * 24} {'-' * 9} {'-' * 9} {'-' * 8} {'-' * 5}\n"
    out.write(col_hdr)
    out.write(col_div)
    for tier, _ in provider.tiers:
        info = status.get(tier.name, {})
        healthy = info.get("healthy", True)
        in_cooling = info.get("in_cooldown", False)
        status_label = "COOLING" if in_cooling else ("HEALTHY" if healthy else "UNHEALTHY")
        cooldown_rem = f"{info.get('cooldown_remaining_seconds', 0.0):.1f}s" if in_cooling else "0s"
        latency_val = info.get("last_latency_ms")
        latency_str = f"{latency_val:.1f}ms" if latency_val is not None else "-"
        fails = str(info.get("consecutive_failures", 0))

        row = (
            f"  {tier.name:<14} {tier.provider:<12} {tier.model:<24} {status_label:<9} "
            f"{cooldown_rem:<9} {latency_str:<8} {fails:<5}\n"
        )
        out.write(row)

    out.write(f"\nAvailable combo profiles: {available}\n")
    out.write("Switch with: /combo <NAME>\n\n")
    out.flush()


def _start_loop(ctx: ChatContext, args: list[str], out: TextIO, err: TextIO) -> None:
    """Start a recurring background loop prompt."""
    if len(args) < 3:
        err.write("usage: /loop <CADENCE> <PROMPT> (e.g. /loop 5m run tests)\n")
        err.flush()
        return

    cadence = args[1]
    prompt = " ".join(args[2:])

    from avo.loop.runner import LoopRunner, LoopState
    from avo.loop.schedule import parse_schedule

    if ctx.active_loop_runner is not None and ctx.active_loop_runner.state in (
        LoopState.RUNNING,
        LoopState.IDLE,
        LoopState.PAUSED,
    ):
        err.write("A loop is already active. Run /unloop first.\n")
        err.flush()
        return

    try:
        from avo.budget import resolve_budget_config

        schedule = parse_schedule(cadence)
        budget = resolve_budget_config()
        runner = LoopRunner(ctx.runtime, schedule=schedule, prompt=prompt, budget_config=budget)
        ctx.active_loop_runner = runner
        ctx.active_loop_task = asyncio.create_task(runner.run_forever(), name="avo-autonomous-loop")
        out.write(f"✓ Started autonomous loop every {cadence}: {prompt!r}\n")
        out.flush()
    except Exception as exc:
        err.write(f"Failed to start loop: {exc}\n")
        err.flush()


def _stop_loop(ctx: ChatContext, out: TextIO, err: TextIO) -> None:
    """Stop the active background loop."""
    if ctx.active_loop_runner is None:
        err.write("No active loop running.\n")
        err.flush()
        return

    ctx.active_loop_runner.stop()
    if ctx.active_loop_task and not ctx.active_loop_task.done():
        ctx.active_loop_task.cancel()
    ctx.active_loop_runner = None
    ctx.active_loop_task = None
    out.write("✓ Stopped autonomous loop.\n")
    out.flush()


def _show_loop_status(ctx: ChatContext, out: TextIO) -> None:
    """Display metrics and status of the current loop."""
    runner = ctx.active_loop_runner
    if runner is None:
        out.write("No autonomous loop currently registered.\n")
        out.flush()
        return

    out.write(f"Autonomous Loop: {runner.state.value.upper()}\n")
    out.write(f"Prompt: {runner.prompt!r}\n")
    out.write(f"Completed ticks: {len(runner.ticks)}\n")
    out.write(f"Total tokens used: {runner.cumulative_usage.total_tokens}\n")
    if runner.ticks:
        last = runner.ticks[-1]
        out.write(f"Last tick #{last.tick_number} [{last.status}] ({last.duration_ms:.1f}ms)\n")
        if last.output:
            out.write(f"  Output: {last.output[:200]}...\n")
        if last.error:
            out.write(f"  Error: {last.error}\n")
    out.flush()


async def _run_slash(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
    in_stream: TextIO | None = None,
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

    if cmd == "/combo":
        _show_combo_status(ctx, args, out, err, environ)
        return False

    if cmd == "/export":
        target = args[1] if len(args) > 1 else None
        _export_session_markdown(ctx, target, out, err)
        return False

    if cmd == "/compact":
        keep_last = int(args[1]) if len(args) > 1 and args[1].isdigit() else 6
        await _compact_session_history(ctx, keep_last, out, err)
        return False

    if cmd == "/history":
        _show_chat_history(ctx, args[1:], out)
        return False

    if cmd == "/draft":
        sub = args[1] if len(args) > 1 else None
        sub_args = args[2:] if len(args) > 2 else []
        _manage_draft(ctx, sub, sub_args, out, err)
        return False

    if cmd == "/context":
        _print_active_context(out, ctx, environ)
        return False

    if cmd == "/persona":
        _manage_persona(ctx, args[1:], out, err)
        return False

    if cmd in ("/instructions", "/prompt"):
        text_arg = " ".join(args[1:]) if len(args) > 1 else None
        _manage_instructions(ctx, text_arg, out, err)
        return False

    if cmd == "/cost":
        _show_cost_breakdown(ctx.store.path, out)
        return False

    if cmd in ("/permissions", "/perm"):
        mode_arg = args[1] if len(args) > 1 else None
        _manage_permissions(ctx, mode_arg, out, err, in_stream=in_stream)
        return False

    if cmd == "/shell":
        cmd_text = " ".join(args[1:]) if len(args) > 1 else ""
        if not cmd_text:
            err.write("usage: /shell COMMAND (or use !COMMAND)\n")
            err.flush()
            return False
        _run_repl_shell(ctx.workspace.root, cmd_text, out, err)
        return False

    if cmd == "/diff":
        target = args[1] if len(args) > 1 else None
        _show_diff_summary(ctx.workspace.root, out, err, target)
        return False

    if cmd == "/undo":
        _undo_workspace(ctx.workspace.root, out, err)
        return False

    if cmd == "/grep":
        await _run_grep_command(ctx, args, out, err)
        return False

    if cmd in ("/find", "/search"):
        await _run_find_command(ctx, args, out, err)
        return False

    if cmd in ("/map", "/tree"):
        await _run_workspace_map_command(ctx, args, out, err)
        return False

    if cmd == "/symbols":
        await _run_symbols_command(ctx, args, out, err)
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

    if cmd == "/branch":
        create_flag = False
        target_branch = None
        if len(args) > 1:
            if args[1] in ("-c", "-b", "--create"):
                create_flag = True
                target_branch = args[2] if len(args) > 2 else None
            else:
                target_branch = args[1]
        _manage_branch(ctx.workspace.root, target_branch, out, err, create=create_flag)
        return False

    if cmd == "/log":
        count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        _show_git_log(ctx.workspace.root, out, err, max_count=count)
        return False

    if cmd == "/stash":
        sub = args[1] if len(args) > 1 else None
        sub_args = args[2:] if len(args) > 2 else []
        _manage_stash(ctx.workspace.root, sub, sub_args, out, err)
        return False

    if cmd == "/bench":
        prompt_arg = " ".join(args[1:]) if len(args) > 1 else "Explain recursion in 10 words."
        await _run_bench_command(ctx, prompt_arg, out, err)
        return False

    if cmd == "/clear":
        _clear_screen(out)
        return False

    if cmd == "/model":
        return await _run_model_command(ctx, args, out, err, environ, in_stream=in_stream)

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

    if cmd == "/setup":
        _manage_setup_command(ctx, args, out, err, environ, in_stream=in_stream)
        return False

    if cmd == "/agent":
        _manage_agent_command(ctx, args, out, err)
        return False

    if cmd in ("/agents", "/agent-list"):
        if (
            cmd == "/agents"
            and len(args) == 1
            and in_stream is not None
            and _can_use_session_picker(in_stream, out)
        ):
            registry = getattr(ctx, "agent_profiles", None)
            if registry is not None:
                selected = await _select_agent_tui(registry.list(), out, in_stream)
                if selected is not None:
                    profile = registry.get(selected)
                    if profile is not None:
                        out.write(
                            f"Selected @{profile.name} ({profile.capability}): "
                            f"{profile.description}\n"
                        )
                        out.flush()
                return False
        _manage_agents_command(ctx, args[1:], out, err)
        return False

    if cmd == "/delegate":
        if len(args) < 2:
            _manage_agents_command(ctx, ["list"], out, err)
            err.write("usage: /delegate @agent TASK or /delegate @agent TASK | @agent TASK\n")
            return False
        text = " ".join(args[1:])
        registry = getattr(ctx, "agent_profiles", None)
        if registry is None:
            err.write("agent profiles are not initialized for this chat context.\n")
            return False
        try:
            request = registry.parse_prompt(text)
        except AgentProfileError as exc:
            err.write(f"agent delegation error: {exc}\n")
            return False
        if request is None:
            err.write("usage: /delegate @agent TASK or /delegate @agent TASK | @agent TASK\n")
            return False
        await _run_agent_request(ctx, request, out, err, original_task=text)
        return False

    if cmd == "/list":
        await _manage_list_command(ctx, args, out, err, environ)
        return False

    if cmd in ("/plugin", "/plugins"):
        _manage_plugin_command(ctx, args, out, err)
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

    if cmd == "/replay":
        if len(args) != 2:
            err.write("usage: /replay RUN_ID\n")
            return False
        report = await replay_run(ctx.store, args[1])
        out.write(report.to_text() + "\n")
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
            if in_stream is not None:
                selected = await _select_session(infos, out, in_stream)
                if selected is not None:
                    await _resume_chat_session(ctx, selected, out, err)
            else:
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

    if cmd == "/loop":
        _start_loop(ctx, args, out, err)
        return False

    if cmd == "/unloop":
        _stop_loop(ctx, out, err)
        return False

    if cmd in ("/loop-status", "/loop_status"):
        _show_loop_status(ctx, out)
        return False

    err.write(f"unknown command: {cmd}; try /help to list slash commands\n")
    return False


async def _select_session(
    infos: tuple[SessionInfo, ...],
    out: TextIO,
    in_stream: TextIO,
) -> str | None:
    """Interactively search and select a chat session from the picker."""

    if _can_use_session_picker(in_stream, out):
        return await _select_session_tui(infos, in_stream, out)

    filtered = infos
    out.write(render_session_picker(filtered))
    while True:
        out.write("Select a session by number, ID, or search text (q to cancel): ")
        out.flush()
        query = in_stream.readline()
        if not query:
            return None
        query = query.strip()
        if not query or query.lower() in {"q", "quit", "cancel"}:
            out.write("Resume cancelled.\n")
            return None

        if query.isdigit():
            index = int(query)
            if 1 <= index <= len(filtered):
                return filtered[index - 1].session_id
            out.write(f"Choose a number from 1 to {len(filtered)}.\n")
            continue

        exact = next((info for info in filtered if info.session_id == query), None)
        if exact is not None:
            return exact.session_id

        matches = tuple(
            info
            for info in infos
            if query.lower()
            in " ".join(
                (
                    info.session_id,
                    info.first_user_preview,
                    info.last_user_preview,
                )
            ).lower()
        )
        if not matches:
            out.write(f"No sessions match {query!r}. Try another search.\n")
            continue
        filtered = matches
        out.write(render_session_picker(filtered))


def _can_use_session_picker(in_stream: TextIO, out: TextIO) -> bool:
    """Return whether the terminal supports the arrow-key picker."""

    return bool(
        hasattr(in_stream, "isatty")
        and in_stream.isatty()
        and hasattr(out, "isatty")
        and out.isatty()
    )


async def _select_session_tui(
    infos: tuple[SessionInfo, ...],
    in_stream: TextIO,
    out: TextIO,
) -> str | None:
    """Use prompt-toolkit completion as an arrow-key session selector."""

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except ImportError:
        return _select_session_fallback(infos, out, in_stream)

    options = tuple(
        f"{index}. {info.first_user_preview[:48]}  ·  {info.turn_count} turns  ·  {info.session_id}"
        for index, info in enumerate(infos, start=1)
    )
    by_option = dict(zip(options, infos, strict=True))

    class SessionCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            query = document.text_before_cursor.lower()
            for option in options:
                if not query or query in option.lower():
                    yield Completion(
                        option,
                        start_position=-len(document.text_before_cursor),
                        display=option,
                    )

    bindings = KeyBindings()

    @bindings.add("down")
    def _start_or_next(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is None:
            buffer.start_completion(select_first=True)
        else:
            buffer.complete_next()

    @bindings.add("up")
    def _previous(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is None:
            buffer.start_completion(select_first=True)
        else:
            buffer.complete_previous()

    @bindings.add("escape")
    def _cancel(event: Any) -> None:
        event.app.exit(exception=KeyboardInterrupt)

    out.write("\n╭─ Resume session ─────────────────────────────────────────────╮\n")
    out.write("│ ↑/↓ choose · Enter select · type to search · Esc cancel     │\n")
    out.write("╰─────────────────────────────────────────────────────────────╯\n")
    out.flush()
    session: Any = PromptSession()
    try:
        selected = await session.prompt_async(
            HTML("<ansicyan>&gt;</ansicyan> "),
            completer=SessionCompleter(),
            complete_while_typing=True,
            key_bindings=bindings,
            style=Style.from_dict(
                {
                    "completion-menu.completion": "bg:#20242b #d8dee9",
                    "completion-menu.completion.current": "bg:#42b883 #101418 bold",
                    "scrollbar.background": "bg:#20242b",
                    "scrollbar.button": "bg:#42b883",
                }
            ),
        )
    except (EOFError, KeyboardInterrupt):
        out.write("\nResume cancelled.\n")
        return None
    selected_info = by_option.get(selected.strip())
    return selected_info.session_id if selected_info is not None else None


async def _select_agent_tui(
    profiles: tuple[Any, ...],
    out: TextIO,
    in_stream: TextIO,
) -> str | None:
    """Use a searchable prompt-toolkit picker for named agents."""

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    options = tuple(f"@{profile.name} · {profile.description}" for profile in profiles)
    by_option = dict(zip(options, profiles, strict=True))

    class AgentCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            query = document.text_before_cursor.lower()
            for option in options:
                if not query or query in option.lower():
                    yield Completion(
                        option,
                        start_position=-len(document.text_before_cursor),
                        display=option,
                    )

    bindings = KeyBindings()

    @bindings.add("down")
    def _next(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is None:
            buffer.start_completion(select_first=True)
        else:
            buffer.complete_next()

    @bindings.add("up")
    def _previous(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is None:
            buffer.start_completion(select_first=True)
        else:
            buffer.complete_previous()

    @bindings.add("escape")
    def _cancel(event: Any) -> None:
        event.app.exit(exception=KeyboardInterrupt)

    out.write("\n╭─ Select agent ────────────────────────────────────────────╮\n")
    out.write("│ ↑/↓ choose · Enter select · type to search · Esc cancel  │\n")
    out.write("╰───────────────────────────────────────────────────────────╯\n")
    out.flush()
    session: Any = PromptSession(
        completer=AgentCompleter(),
        complete_while_typing=True,
        reserve_space_for_menu=min(8, max(1, len(options))),
        erase_when_done=True,
        key_bindings=bindings,
        style=Style.from_dict(
            {
                "completion-menu.completion": "bg:#20242b #d8dee9",
                "completion-menu.completion.current": "bg:#42b883 #101418 bold",
                "scrollbar.background": "bg:#20242b",
                "scrollbar.button": "bg:#42b883",
            }
        ),
    )
    session.default_buffer.start_completion(select_first=True)
    try:
        selected = await session.prompt_async(HTML("<ansicyan>&gt;</ansicyan> "))
    except (EOFError, KeyboardInterrupt):
        out.write("\nAgent selection cancelled.\n")
        return None
    profile = by_option.get(selected.strip())
    return profile.name if profile is not None else None


def _select_session_fallback(
    infos: tuple[SessionInfo, ...],
    out: TextIO,
    in_stream: TextIO,
) -> str | None:
    """Provide a non-prompt-toolkit selector for terminal fallbacks."""

    out.write(render_session_picker(infos))
    query = in_stream.readline().strip()
    if not query or query.lower() in {"q", "quit", "cancel"}:
        out.write("Resume cancelled.\n")
        return None
    if query.isdigit() and 1 <= int(query) <= len(infos):
        return infos[int(query) - 1].session_id
    resolved = resolve_session_id(query, infos)
    return resolved
