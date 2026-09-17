"""Display helpers for the chat REPL: banner, help text, and status panels.

Pure rendering — every function writes to a caller-provided stream and
mutates no state. The slash-command table lives here because
:data:`_print_slash_help` and the REPL's completion setup are its only
readers.

Re-exported from :mod:`avo.chat` for backward compatibility.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from avo import __version__ as AVO_VERSION

if TYPE_CHECKING:
    from avo.chat import ChatContext


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


def _read_environ() -> dict[str, str]:
    """Snapshot ``os.environ`` so the chat does not see mid-session mutations."""

    return dict(os.environ)


def _resolve_provider_label(environ: dict[str, str]) -> tuple[str, str]:
    """Return ``(provider_name, model_name)`` without exposing secrets."""

    provider = environ.get("AVO_PROVIDER", "").strip()
    if not provider:
        try:
            from avo.oauth.store import get_credential

            for candidate, candidate_provider, def_model in (
                ("claude", "anthropic", "claude-sonnet-4-5"),
                ("codex", "codex", "gpt-5.6-sol"),
                ("gemini", "gemini_cli", "gemini-2.5-pro"),
                ("openrouter", "openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
            ):
                if get_credential(candidate) is not None:
                    environ["AVO_PROVIDER"] = candidate_provider
                    environ.setdefault("AVO_MODEL", def_model)
                    provider = candidate_provider
                    break
        except Exception:
            pass

    provider = provider or "(unset)"
    if provider == "router":
        chain = environ.get("AVO_ROUTER_PROVIDERS", "").strip()
        models = environ.get("AVO_ROUTER_MODELS", "").strip()
        model_display = f"{chain} [{models}]" if chain and models else "fallback-chain"
        return provider, model_display

    if provider == "combo":
        combo_name = (
            environ.get("AVO_COMBO", "").strip()
            or environ.get("AVO_MODEL", "").strip()
            or "default"
        )
        try:
            from avo.combo.store import get_combo

            profile = get_combo(combo_name)
            if profile is not None:
                tiers_display = " -> ".join(t.name for t in profile.tiers)
                return "combo", f"{combo_name} [{tiers_display}]"
        except Exception:
            pass
        return "combo", combo_name

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


def _format_workspace_path(path: Path) -> str:
    """Shorten home directory prefix to ~ for compact terminal rendering."""

    try:
        home = Path.home()
        if path == home or home in path.parents:
            return f"~/{path.relative_to(home)}"
    except Exception:
        pass
    return str(path)


def _resolve_user_identity(provider_name: str) -> str:
    """Return user account or display name for the banner without leaking secrets."""

    try:
        from avo.oauth.store import get_credential

        cred = get_credential(provider_name)
        if cred and cred.account:
            tier = ""
            if cred.subscription:
                tier = " (Subscription)"
            elif cred.kind == "oauth":
                tier = " (OAuth)"
            return f"{cred.account}{tier}"
    except Exception:
        pass

    try:
        res = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        email = res.stdout.strip()
        if email:
            return email
    except Exception:
        pass

    return os.environ.get("USER", "user")


def _render_mascot(*, color: bool = True) -> list[str]:
    """Render the Avo Avocado/Arch ANSI block mascot logo (7 lines)."""

    def _tc(r: int, g: int, b: int, text: str) -> str:
        if not color:
            return text
        return f"\033[38;2;{r};{g};{b}m{text}\033[0m"

    return [
        "     " + _tc(250, 204, 21, "▄██▄") + "     ",
        "   " + _tc(245, 158, 11, "▄██") + _tc(249, 115, 22, "████▄") + "   ",
        "  " + _tc(132, 204, 22, "███") + "    " + _tc(239, 68, 68, "███") + "  ",
        " "
        + _tc(34, 197, 94, "███")
        + "  "
        + _tc(217, 119, 6, "▄▄")
        + "  "
        + _tc(168, 85, 247, "███")
        + " ",
        " "
        + _tc(16, 185, 129, "███")
        + "  "
        + _tc(180, 83, 9, "▀▀")
        + "  "
        + _tc(147, 51, 234, "███")
        + " ",
        "  " + _tc(6, 182, 212, "███") + "    " + _tc(99, 102, 241, "███") + "  ",
        "   " + _tc(14, 165, 233, "▀██") + _tc(59, 130, 246, "████▀") + "   ",
    ]


def _print_boot_banner(out: TextIO) -> None:
    """Render the mascot while the first-run provider setup is loading."""

    color_enabled = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")
    bold_cyan = "\033[1;36m" if color_enabled else ""
    dim = "\033[90m" if color_enabled else ""
    rst = "\033[0m" if color_enabled else ""

    right_col = [
        f"{bold_cyan}Avo CLI {AVO_VERSION}{rst}",
        f"{dim}starting interactive session{rst}",
        f"{dim}checking provider configuration{rst}",
        f"{dim}setup wizard will appear if needed{rst}",
        "",
        "",
        "",
    ]
    out.write("\n")
    for left, right in zip(_render_mascot(color=color_enabled), right_col, strict=True):
        out.write(f"  {left}  {right}\n")
    out.write(f"  {dim}{'─' * 54}{rst}\n")
    out.flush()


def _print_header(
    out: TextIO,
    ctx: ChatContext,
    workspace_root: Path,
    *,
    resumed_from: str | None = None,
) -> None:
    """Render the AVO banner with mascot logo, version, identity, model, and cwd."""

    color_enabled = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")
    bold_cyan = "\033[1;36m" if color_enabled else ""
    dim = "\033[90m" if color_enabled else ""
    rst = "\033[0m" if color_enabled else ""

    mascot = _render_mascot(color=color_enabled)
    identity = _resolve_user_identity(ctx.provider_name)
    workspace_disp = _format_workspace_path(workspace_root)
    provider_model = f"provider: {ctx.provider_name} · model: {ctx.model_name}"

    right_col = [
        f"{bold_cyan}Avo CLI {AVO_VERSION}{rst}",
        f"{dim}{identity}{rst}",
        f"{dim}{provider_model}{rst}",
        f"{dim}workspace: {workspace_disp}{rst}",
    ]
    if resumed_from:
        right_col.append(f"{dim}resumed session: {resumed_from}{rst}")
    else:
        right_col.append(f"{dim}session: {ctx.session_id}{rst}")
    right_col.append(f"{dim}Type /help for commands · /quit to exit{rst}")
    right_col.append("")

    out.write("\n")
    for left, right in zip(mascot, right_col, strict=True):
        out.write(f"  {left}  {right}\n")
    out.write(f"  {dim}{'─' * 54}{rst}\n")
    out.flush()


SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "show this command list"),
    ("/provider", "show provider/model/API-key status"),
    ("/router", "show multi-provider fallback router live status"),
    ("/combo [NAME]", "show active combo routing tiers or switch to NAME"),
    ("/model [NAME]", "list known models, or switch to NAME or PROVIDER/MODEL"),
    ("/context", "display full context snapshot (session, model, workspace, skills)"),
    ("/persona [NAME]", "list known personas or switch active persona (coder, reviewer, etc.)"),
    ("/instructions [TEXT]", "view or set custom workspace instructions"),
    ("/cost", "show token usage and spend breakdown from ledger"),
    ("/permissions [MODE]", "view or switch permission mode (default, accept_edits, etc.)"),
    ("/shell [CMD]", "execute shell command in workspace (or use !CMD)"),
    ("/diff [PATH]", "show git status, diff stat, or unified diff for PATH"),
    ("/undo", "revert uncommitted workspace modifications"),
    ("/grep PATTERN [GLOB]", "search workspace file contents for regex pattern"),
    ("/find [GLOB]", "find files in workspace matching glob pattern (alias: /search)"),
    ("/map [N]", "display workspace file tree and recently modified files (alias: /tree)"),
    ("/symbols [PATH]", "extract code symbols (classes, methods, functions) from PATH"),
    ("/lint [PATH]", "run code linter and syntax checks on workspace files"),
    ("/test [TARGET]", "run automated test suite on workspace files"),
    ("/commit [MSG]", "stage changes and create atomic git commit (auto-message if omitted)"),
    ("/branch [NAME]", "list git branches, or switch/create branch NAME"),
    ("/log [N]", "show recent N git commits in the repository"),
    ("/stash [CMD]", "manage git stash (list, save, pop, or drop)"),
    ("/bench [PROMPT]", "benchmark live routes and display speed ranking"),
    ("/clear", "clear the terminal screen"),
    ("/export [PATH]", "export current chat session to Markdown file"),
    ("/compact [N]", "compact session context window, preserving first and last N turns"),
    ("/history [QUERY]", "view recent session turns or search past conversation history"),
    ("/draft [show|save|clear]", "view, save, or discard persistent draft prompt"),
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
    from avo.context_advisor import evaluate_session_context

    turns = ctx.session.turns(ctx.session_id)
    skills_list = ctx.skills.names()
    running_jobs = ctx.background.running_count
    report = evaluate_session_context(turns, ctx.model_name, environ=environ)

    rows: list[tuple[str, str]] = [
        ("Session ID", ctx.session_id),
        ("Turn Count", f"{len(turns)} turn(s) recorded"),
        ("Provider", ctx.provider_name),
        ("Model", ctx.model_name),
        ("Workspace", str(ctx.workspace.root)),
        ("Context Window", f"{report.context_limit:,} tokens (~{report.usage_percent:.1f}% used)"),
        (
            "Skills Active",
            f"{len(skills_list)} installed ({', '.join(skills_list[:3])})"
            if skills_list
            else "none",
        ),
        ("Background Jobs", f"{running_jobs} active running"),
        ("Persona", ctx.persona.active_persona or "(default)"),
        (
            "Instructions",
            f"{len(ctx.persona.custom_instructions)} chars active"
            if ctx.persona.custom_instructions
            else "none",
        ),
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


def _clear_screen(out: TextIO) -> None:
    """Clear the terminal screen and reset cursor."""

    out.write("\033[2J\033[H")
    out.flush()


def _format_file_size(size_bytes: int) -> str:
    """Format byte sizes into human-readable units."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def render_first_run_message(out: TextIO) -> None:
    """Print the canonical first-run configuration hint."""

    out.write(_FIRST_RUN_MESSAGE)
    out.flush()
