"""Interactive chat REPL wiring AgentRuntime + app_tools + SQLite.

The setup wizard lives in :mod:`avo.chat_setup` and the
shell-rc persistence helpers live in :mod:`avo.chat_shell_rc`.
Display helpers live in :mod:`avo.chat_render`, workspace/git/shell
commands in :mod:`avo.chat_workspace_commands`, the slash dispatcher
and session-management commands in :mod:`avo.chat_commands`, and turn
execution in :mod:`avo.chat_turn`. All of those modules are
re-exported here for backward compatibility with existing imports
(``from avo.chat import interactive_first_run_setup``) so the public
surface stays stable.

Conversation threading lives in :mod:`avo.chat_session`. The
REPL persists every user input + assistant reply through
:class:`avo.storage.conversations.ConversationStore`, sharing the
SQLite file with the event store so a single file holds both the run
history and the chat thread.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from avo.app_tools import (
    batch_replace_tool,
    edit_file_tool,
    git_commit_tool,
    git_diff_tool,
    git_status_tool,
    glob_tool,
    grep_tool,
    lint_tool,
    symbols_tool,
    test_runner_tool,
    workspace_map_tool,
)
from avo.app_tools.file_tools import read_file_tool, write_file_tool
from avo.app_tools.workspace import Workspace
from avo.background import BackgroundJobManager
from avo.chat_commands import (  # re-export
    _compact_session_history,
    _export_session_markdown,
    _manage_draft,
    _manage_instructions,
    _manage_permissions,
    _manage_persona,
    _run_slash,
    _show_chat_history,
    _show_router_status,
)
from avo.chat_render import (  # re-export
    _FIRST_RUN_MESSAGE,
    SLASH_COMMANDS,
    _clear_screen,
    _format_file_size,
    _new_session_id,
    _print_active_context,
    _print_boot_banner,
    _print_header,
    _print_provider_summary,
    _print_slash_help,
    _read_environ,
    _resolve_provider_label,
    _show_cost_breakdown,
    render_first_run_message,
)
from avo.chat_session import SessionLifecycle
from avo.chat_setup import interactive_first_run_setup  # re-export
from avo.chat_shell_rc import (  # re-export
    _detect_shell_rc_path,
    _offer_persist_to_shell_rc,
    _quote_for_shell,
    persist_env_to_shell_rc,
)
from avo.chat_stream import chat_stream_enabled
from avo.chat_turn import (  # re-export
    _maybe_offer_resume_prompt,
    _prompt_with_jobs,
    _resume_chat_session,
    _run_model_command,
    _run_turn,
)
from avo.chat_workspace_commands import (  # re-export
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
from avo.config import ConfigError, build_provider_from_env
from avo.exceptions import AvoError
from avo.permissions import (
    PermissionPolicy,
    build_approval_callback,
    permission_policy_from_env,
)
from avo.persona import PersonaManager
from avo.repl_history import ReplHistoryManager
from avo.runtime import AgentRuntime, ApprovalCallback
from avo.skills import SkillRegistry
from avo.storage.sqlite import SQLiteEventStore

REPO_LOGO_PATH = Path(__file__).resolve().parents[3] / "public" / "logo.webp"


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
    permission_policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    persona: PersonaManager = field(default_factory=PersonaManager)
    history: ReplHistoryManager | None = None
    stream_enabled: bool = False


def build_chat_context(
    *,
    database_path: Path,
    workspace_root: Path,
    environ: dict[str, str],
    session_id: str | None = None,
    force_new_session: bool = False,
    approval_callback: ApprovalCallback | None = None,
    permission_policy: PermissionPolicy | None = None,
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
    resolved_policy = (
        permission_policy if permission_policy is not None else permission_policy_from_env(environ)
    )
    resolved_approval_callback = approval_callback
    if resolved_approval_callback is None:
        resolved_approval_callback = build_approval_callback(resolved_policy)
    runtime = AgentRuntime(
        provider=provider,
        event_store=store,
        tools=[
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
            git_status_tool(),
            git_diff_tool(),
            git_commit_tool(),
        ],
        approval_callback=resolved_approval_callback,
    )

    skills_root = workspace_root / ".avo" / "skills"
    # Ensure the skills directory exists for first-run use, but the
    # registry itself walks a path — body lookup happens lazily.
    skills_root.mkdir(parents=True, exist_ok=True)
    skills = SkillRegistry(skills_root)
    session = SessionLifecycle.open(db_path)
    persona_mgr = PersonaManager(workspace_root=workspace.root)
    history_mgr = ReplHistoryManager(workspace_root=workspace.root)
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
            permission_policy=resolved_policy,
            persona=persona_mgr,
            history=history_mgr,
            stream_enabled=chat_stream_enabled(environ),
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
        permission_policy=resolved_policy,
        persona=persona_mgr,
        history=history_mgr,
        stream_enabled=chat_stream_enabled(environ),
    )


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

    policy = permission_policy_from_env(env)
    approval_cb = build_approval_callback(policy, stdin=in_stream, stdout=out_stream)

    try:
        ctx = build_chat_context(
            database_path=database_path,
            workspace_root=workspace_root,
            environ=env,
            session_id=session_id,
            force_new_session=force_new_session,
            approval_callback=approval_cb,
            permission_policy=policy,
        )
    except ConfigError:
        _print_boot_banner(out_stream)
        new_env = interactive_first_run_setup(in_stream, out_stream, secret_reader=secret_reader)
        if new_env is None:
            return 2
        env = {**env, **new_env}
        policy = permission_policy_from_env(env)
        approval_cb = build_approval_callback(policy, stdin=in_stream, stdout=out_stream)
        try:
            ctx = build_chat_context(
                database_path=database_path,
                workspace_root=workspace_root,
                environ=env,
                session_id=session_id,
                force_new_session=force_new_session,
                approval_callback=approval_cb,
                permission_policy=policy,
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

    is_interactive = (
        (stdin is None or stdin is sys.stdin)
        and hasattr(in_stream, "isatty")
        and in_stream.isatty()
    )
    if ctx.history is not None:
        slash_names = [cmd_tuple[0].split()[0] for cmd_tuple in SLASH_COMMANDS]
        aliases = ["/search", "/tree", "/perm", "/prompt", "/exit"]
        ctx.history.setup(commands=[*slash_names, *aliases])
        draft = ctx.history.load_draft()
        if draft:
            draft_snippet = draft.splitlines()[0]
            if len(draft_snippet) > 50:
                draft_snippet = draft_snippet[:47] + "..."
            out_stream.write(f"💡 Saved draft found: {draft_snippet!r} (type /draft to view)\n\n")
            out_stream.flush()

    try:
        while True:
            try:
                active_prompt = prompt
                if is_interactive and prompt == "You > ":
                    active_prompt = "\033[36m>\033[0m " if not os.environ.get("NO_COLOR") else "> "
                prompt_text = _prompt_with_jobs(active_prompt, ctx.background)
                if is_interactive:
                    try:
                        line = await asyncio.to_thread(input, prompt_text) + "\n"
                    except EOFError:
                        line = ""
                else:
                    out_stream.write(prompt_text)
                    out_stream.flush()
                    line = in_stream.readline()
            except KeyboardInterrupt:
                out_stream.write("\n(interrupted - type /quit or Ctrl+D to exit)\n")
                out_stream.flush()
                continue

            if not line:
                out_stream.write("\n")
                if ctx.history is not None:
                    ctx.history.save_history()
                return 0
            stripped = line.strip()
            if not stripped:
                continue

            if ctx.history is not None:
                ctx.history.append_history(stripped)

            if stripped.startswith("!"):
                cmd_text = stripped[1:].strip()
                _run_repl_shell(ctx.workspace.root, cmd_text, out_stream, err_stream)
                continue

            if stripped.startswith("/"):
                try:
                    args = shlex.split(stripped)
                except ValueError as exc:
                    err_stream.write(f"parse error: {exc}\n")
                    continue
                should_exit = await _run_slash(
                    ctx, args, out_stream, err_stream, env, in_stream=in_stream
                )
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
            if ctx.history is not None and stripped == ctx.history.load_draft():
                ctx.history.clear_draft()
    finally:
        if ctx.history is not None:
            ctx.history.save_history()
        await ctx.background.wait_all()
        await ctx.store.close()
        ctx.session.close()


__all__ = [
    "REPO_LOGO_PATH",
    "SLASH_COMMANDS",
    "_FIRST_RUN_MESSAGE",
    "ChatContext",
    "_clear_screen",
    "_compact_session_history",
    "_detect_shell_rc_path",
    "_export_session_markdown",
    "_format_file_size",
    "_manage_branch",
    "_manage_draft",
    "_manage_instructions",
    "_manage_permissions",
    "_manage_persona",
    "_manage_stash",
    "_maybe_offer_resume_prompt",
    "_new_session_id",
    "_offer_persist_to_shell_rc",
    "_print_active_context",
    "_print_header",
    "_print_provider_summary",
    "_print_slash_help",
    "_prompt_with_jobs",
    "_quote_for_shell",
    "_read_environ",
    "_resolve_provider_label",
    "_resume_chat_session",
    "_run_bench_command",
    "_run_find_command",
    "_run_grep_command",
    "_run_model_command",
    "_run_repl_shell",
    "_run_slash",
    "_run_symbols_command",
    "_run_turn",
    "_run_workspace_commit",
    "_run_workspace_lint",
    "_run_workspace_map_command",
    "_run_workspace_tests",
    "_show_chat_history",
    "_show_cost_breakdown",
    "_show_diff_summary",
    "_show_git_log",
    "_show_router_status",
    "_undo_workspace",
    "build_chat_context",
    "interactive_first_run_setup",
    "persist_env_to_shell_rc",
    "render_first_run_message",
    "run_repl",
]
