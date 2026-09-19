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
import shutil
import sys
from collections.abc import Callable, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False


if _HAS_PROMPT_TOOLKIT:

    class SlashCompleter(Completer):
        """Interactive dropdown menu completion for slash commands."""

        def __init__(self, commands: Sequence[tuple[str, str]]) -> None:
            self._items: list[tuple[str, str]] = []
            for raw, desc in commands:
                cmd = raw.split()[0]
                self._items.append((cmd, desc))
            aliases = [
                ("/search", "alias for /find"),
                ("/tree", "alias for /map"),
                ("/perm", "alias for /permissions"),
                ("/prompt", "alias for /instructions"),
                ("/exit", "alias for /quit"),
                ("/plugins", "alias for /plugin"),
            ]
            for cmd, desc in aliases:
                if not any(c == cmd for c, _ in self._items):
                    self._items.append((cmd, desc))

        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = document.text_before_cursor
            if text.startswith("/"):
                prefix = text.split()[0] if " " in text else text
                for cmd, desc in self._items:
                    if cmd.startswith(prefix):
                        yield Completion(
                            cmd,
                            start_position=-len(prefix),
                            display=cmd,
                            display_meta=desc,
                        )


from avo.agent_profiles import AgentProfileError, AgentProfileRegistry
from avo.app_tools import (
    batch_replace_tool,
    edit_file_tool,
    git_commit_tool,
    git_diff_tool,
    git_status_tool,
    glob_tool,
    grep_tool,
    lint_tool,
    run_terminal_tool,
    symbols_tool,
    test_runner_tool,
    workspace_map_tool,
)
from avo.app_tools.file_tools import read_file_tool, write_file_tool
from avo.app_tools.workspace import Workspace
from avo.auth import AuthError, login_provider_in_browser
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
    render_chat_toolbar,
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
    _run_agent_request,
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
from avo.config_resolver import resolve_security_config
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
    agent_profiles: AgentProfileRegistry | None = None
    provider_factory: Callable[[], Any] | None = None
    active_loop_runner: Any | None = None
    active_loop_task: asyncio.Task[None] | None = None


def _position_prompt_at_bottom(out: TextIO, *, terminal_rows: int | None = None) -> None:
    """Move the next prompt to the rows immediately above the status toolbar."""

    rows = terminal_rows or shutil.get_terminal_size(fallback=(80, 24)).lines
    # The prompt consumes one row and the toolbar consumes two (separator plus
    # status). Start on the row immediately above those toolbar rows.
    target_row = max(1, rows - 2)
    out.write(f"\033[{target_row};1H")
    out.flush()


def _highlight_prompt_input(prompt_session: Any) -> None:
    """Highlight the single-line input window without stretching its height."""

    from prompt_toolkit.layout.controls import BufferControl

    for window in prompt_session.layout.find_all_windows():
        if (
            isinstance(window.content, BufferControl)
            and window.content.buffer is prompt_session.default_buffer
        ):
            window.style = "class:avo-input"


def _prompt_toolkit_prompt(prompt_text: str, color_enabled: bool) -> Any:
    """Convert an ANSI prompt into prompt-toolkit formatted text."""

    if color_enabled and _HAS_PROMPT_TOOLKIT:
        return ANSI(prompt_text)
    return prompt_text


def _accept_slash_completion(buffer: Any) -> bool:
    """Accept the selected slash command before submitting the prompt."""

    if not buffer.text.startswith("/"):
        return False
    state = buffer.complete_state
    if state is None or state.current_completion is None:
        return False
    buffer.apply_completion(state.current_completion)
    return True


def _find_prompt_float_container(prompt_session: Any) -> Any | None:
    """Find prompt-toolkit's input float without depending on its tree depth."""

    from prompt_toolkit.layout.containers import (
        ConditionalContainer,
        DynamicContainer,
        FloatContainer,
    )

    def find_float_container(container: Any, seen: set[int]) -> Any | None:
        marker = id(container)
        if marker in seen:
            return None
        seen.add(marker)
        if isinstance(container, DynamicContainer):
            return find_float_container(container.get_container(), seen)
        if isinstance(container, ConditionalContainer):
            selected = container.content if container.filter() else container.alternative_content
            if selected is None:
                return None
            return find_float_container(selected, seen)
        if isinstance(container, FloatContainer):
            return container
        for child in getattr(container, "children", ()):
            found = find_float_container(child, seen)
            if found is not None:
                return found
        content = getattr(container, "content", None)
        if content is not None:
            return find_float_container(content, seen)
        return None

    return find_float_container(prompt_session.layout.container, set())


def _place_completion_menu_above(prompt_session: Any) -> None:
    """Anchor prompt-toolkit's slash completion menu above the input line.

    The input is intentionally kept at the terminal bottom. With no reserved
    menu rows, prompt-toolkit's cursor-relative float can otherwise render
    below the cursor and disappear behind the toolbar. Moving only the menu
    float keeps the editor one line high when no completion is active.
    """

    float_container = _find_prompt_float_container(prompt_session)
    if float_container is None or not float_container.floats:
        return
    completion_float = float_container.floats[0]
    completion_float.top = None
    completion_float.bottom = 1
    completion_float.left = 0
    completion_float.right = None
    completion_float.xcursor = False
    completion_float.ycursor = False
    completion_float.allow_cover_cursor = True


def _install_command_palette(prompt_session: Any, commands: Sequence[tuple[str, str]]) -> None:
    """Keep prompt-toolkit's native completion menu as the sole palette.

    The hook remains for compatibility with callers that used the old custom
    palette installer. The native menu already provides filtering, selection,
    descriptions, and highlight rendering without a second layout float.
    """

    return


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
    security_config = resolve_security_config(
        environ=environ,
        workspace_root=workspace.root,
    )
    plugin_tools: list[Any] = []
    if security_config.plugin_activation.value:
        from avo.cli_plugins import active_plugin_site_packages
        from avo.plugins import TOOL_GROUP, discover_from_paths
        from avo.tools import Tool

        try:
            for entry in discover_from_paths(TOOL_GROUP, active_plugin_site_packages()):
                produced = entry.factory()
                candidates = produced if isinstance(produced, (list, tuple)) else (produced,)
                for tool in candidates:
                    if tool is not None and not isinstance(tool, Tool):
                        raise AvoError(
                            f"plugin {entry.name!r} returned an object that is not an Avo tool"
                        )
                    if tool is not None:
                        plugin_tools.append(tool)
        except Exception as exc:
            if isinstance(exc, AvoError):
                raise
            raise AvoError(f"failed to activate plugin tools: {exc}") from exc
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
            run_terminal_tool(security_config=security_config),
            git_status_tool(),
            git_diff_tool(),
            git_commit_tool(),
            *plugin_tools,
        ],
        approval_callback=resolved_approval_callback,
        security_config=security_config,
    )

    skills_root = workspace_root / ".avo" / "skills"
    # Ensure the skills directory exists for first-run use, but the
    # registry itself walks a path — body lookup happens lazily.
    skills_root.mkdir(parents=True, exist_ok=True)
    skills = SkillRegistry(skills_root)
    session = SessionLifecycle.open(db_path)
    persona_mgr = PersonaManager(workspace_root=workspace.root)
    history_mgr = ReplHistoryManager(workspace_root=workspace.root)
    agent_profiles = AgentProfileRegistry(workspace.root)

    def provider_factory() -> Any:
        return build_provider_from_env(environ)

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
            agent_profiles=agent_profiles,
            provider_factory=provider_factory,
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
        agent_profiles=agent_profiles,
        provider_factory=provider_factory,
    )


@contextmanager
def _alternate_screen(out_stream: TextIO, *, enabled: bool) -> Any:
    """Temporarily run Avo in the terminal's alternate screen buffer.

    The alternate buffer gives the CLI an application-like canvas without
    destroying the shell's scrollback.  The ``finally`` block is important:
    it restores the shell even when the REPL exits through EOF, Ctrl+C, or an
    unexpected exception.
    """

    if not enabled:
        yield
        return

    out_stream.write("\033[?1049h\033[2J\033[H")
    out_stream.flush()
    try:
        yield
    finally:
        out_stream.write("\033[?1049l")
        out_stream.flush()


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
    resume_latest: bool = False,
) -> int:
    """Run the REPL and restore the caller's terminal buffer on exit."""

    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    is_interactive = (
        (stdin is None or stdin is sys.stdin)
        and hasattr(in_stream, "isatty")
        and in_stream.isatty()
    )
    with _alternate_screen(out_stream, enabled=is_interactive):
        return await _run_repl(
            database_path=database_path,
            workspace_root=workspace_root,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            environ=environ,
            prompt=prompt,
            secret_reader=secret_reader,
            session_id=session_id,
            force_new_session=force_new_session,
            resume_latest=resume_latest,
        )


async def _run_repl(
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
    resume_latest: bool = False,
) -> int:
    """Run the interactive chat REPL until EOF, /quit, or fatal init error.

    ``session_id`` and ``force_new_session`` mirror the ``--session``
    and ``--new-session`` CLI flags. The default always starts a fresh
    thread. ``resume_latest`` is reserved for the explicit ``avo resume``
    command and resumes the most recent eligible thread without prompting.
    """

    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    env = environ if environ is not None else _read_environ()
    is_interactive = (
        (stdin is None or stdin is sys.stdin)
        and hasattr(in_stream, "isatty")
        and in_stream.isatty()
    )

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
    except (ConfigError, ValueError, AuthError):
        _print_boot_banner(out_stream)
        new_env = interactive_first_run_setup(
            in_stream,
            out_stream,
            secret_reader=secret_reader,
            vendor_login=lambda provider: login_provider_in_browser(
                provider,
                output_writer=out_stream.write,
            ),
        )
        if new_env is None:
            return 2
        env = {**env, **new_env}
        if is_interactive:
            # Keep the selected route for the next `avo` process. Only the
            # provider/model flags are persisted; credentials stay in auth.json.
            with suppress(OSError):
                from avo.cli_setup import remember_last_provider

                remember_last_provider(env)
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
        except (ConfigError, ValueError, AuthError) as exc:
            err_stream.write(f"configuration still invalid after setup: {exc}\n")
            return 2
        except (AvoError, OSError) as exc:
            err_stream.write(f"avo chat: {exc}\n")
            return 2
    except (AvoError, OSError) as exc:
        err_stream.write(f"avo chat: {exc}\n")
        return 2

    resumed_from: str | None = None
    if session_id is not None and not force_new_session:
        resumed_from = session_id
    elif session_id is None and not force_new_session and resume_latest:
        recent = ctx.session.find_resumable(limit=1)
        if recent:
            resumed = recent[0]
            ctx.session_id = resumed.session_id
            try:
                ctx.pending_preamble = ctx.session.build_preamble(resumed.session_id)
            except AvoError:
                ctx.pending_preamble = None
            resumed_from = resumed.session_id
        else:
            out_stream.write("No resumable sessions found; starting a new session.\n")

    if is_interactive:
        # The public wrapper already put interactive sessions in a clean
        # alternate screen buffer, so no separator is needed here.
        out_stream.flush()
    _print_header(out_stream, ctx, workspace_root, resumed_from=resumed_from)
    if resumed_from is not None:
        with suppress(AvoError):
            out_stream.write("\n" + ctx.session.render_transcript(resumed_from) + "\n")
    out_stream.write("\n")
    out_stream.flush()

    if ctx.history is not None:
        slash_names = [cmd_tuple[0].split()[0] for cmd_tuple in SLASH_COMMANDS]
        aliases = ["/search", "/tree", "/perm", "/prompt", "/exit", "/plugins"]
        ctx.history.setup(commands=[*slash_names, *aliases])
        draft = ctx.history.load_draft()
        if draft:
            draft_snippet = draft.splitlines()[0]
            if len(draft_snippet) > 50:
                draft_snippet = draft_snippet[:47] + "..."
            out_stream.write(f"💡 Saved draft found: {draft_snippet!r} (type /draft to view)\n\n")
            out_stream.flush()

    pt_session: Any = None
    completer: Any = None
    if is_interactive and _HAS_PROMPT_TOOLKIT:
        try:
            pt_history = (
                FileHistory(str(ctx.history.history_file)) if ctx.history is not None else None
            )
            completer = SlashCompleter(SLASH_COMMANDS)
            from prompt_toolkit.key_binding import KeyBindings

            pt_bindings = KeyBindings()

            @pt_bindings.add("enter")
            def _accept_command_completion(event: Any) -> None:
                buffer = event.current_buffer
                _accept_slash_completion(buffer)
                buffer.validate_and_handle()

            @pt_bindings.add("/")
            def _start_slash_completion(event: Any) -> None:
                buffer = event.current_buffer
                buffer.insert_text("/")
                buffer.start_completion(select_first=True)

            pt_style = Style.from_dict(
                {
                    "avo-input": "bg:#303030 #f0f6fc",
                    # Keep the toolbar transparent. Prompt Toolkit's default
                    # toolbar style is reverse-video, which turns the whole
                    # status area into a bright block unless overridden.
                    "bottom-toolbar": "noreverse #a6b3c2",
                    "bottom-toolbar.text": "noreverse #a6b3c2",
                    "prompt": "bg:#303030 #00d7af bold",
                    "prompt-continuation": "bg:#303030 #00d7af",
                    "completion-menu.completion": "bg:#1e1e1e #ffffff",
                    "completion-menu.completion.current": "bg:#008b8b #ffffff bold",
                    "completion-menu.meta.completion": "bg:#2a2a2a #a0a0a0",
                    "completion-menu.meta.completion.current": "bg:#006868 #ffffff bold",
                    "scrollbar.background": "bg:#222222",
                    "scrollbar.button": "bg:#555555",
                }
            )
            pt_session = PromptSession(
                history=pt_history,
                completer=completer,
                complete_while_typing=True,
                reserve_space_for_menu=0,
                erase_when_done=True,
                key_bindings=pt_bindings,
                style=pt_style,
            )
            _highlight_prompt_input(pt_session)
            _place_completion_menu_above(pt_session)
            _install_command_palette(pt_session, tuple(completer._items))
        except Exception:
            pt_session = None

    color_enabled = (
        hasattr(out_stream, "isatty") and out_stream.isatty() and not os.environ.get("NO_COLOR")
    )
    cyan = "\033[1;36m" if color_enabled else ""
    rst = "\033[0m" if color_enabled else ""
    prompt_marker = chr(0x276F)

    def _bottom_toolbar_text() -> str:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        dim = "\033[90m" if color_enabled else ""
        reset = "\033[0m" if color_enabled else ""
        border = f"{dim}{'─' * max(columns, 1)}{reset}"
        status = render_chat_toolbar(
            ctx.provider_name,
            ctx.model_name,
            workspace_root,
            running_jobs=ctx.background.running_count,
            color=color_enabled,
        )
        return f"{border}\n{status}"

    def _bottom_toolbar() -> Any:
        toolbar = _bottom_toolbar_text()
        return ANSI(toolbar) if color_enabled else toolbar

    pin_prompt_to_bottom = is_interactive
    try:
        while True:
            try:
                if pin_prompt_to_bottom:
                    _position_prompt_at_bottom(out_stream)
                    # The first prompt is anchored below the banner. After a
                    # turn, output must flow naturally; re-anchoring would
                    # make Prompt Toolkit erase the response area on redraw.
                    pin_prompt_to_bottom = False

                active_prompt = prompt
                if is_interactive and prompt == "You > ":
                    active_prompt = f"{cyan}{prompt_marker}{rst} " if color_enabled else "> "
                prompt_text = _prompt_with_jobs(active_prompt, ctx.background)
                if is_interactive and pt_session is not None:
                    try:
                        line = (
                            await pt_session.prompt_async(
                                _prompt_toolkit_prompt(prompt_text, color_enabled),
                                completer=completer,
                                complete_while_typing=True,
                                bottom_toolbar=_bottom_toolbar,
                            )
                            + "\n"
                        )
                        pt_session.reserve_space_for_menu = 0
                    except EOFError:
                        line = ""
                elif is_interactive:
                    try:
                        out_stream.write(_bottom_toolbar_text() + "\n")
                        out_stream.flush()
                        line = await asyncio.to_thread(input, prompt_text) + "\n"
                    except EOFError:
                        line = ""
                else:
                    out_stream.write(_bottom_toolbar_text() + "\n")
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

            if is_interactive and pt_session is not None:
                # Prompt Toolkit erases its temporary editor after Enter. Echo
                # the accepted turn as durable chat history before the model
                # response is rendered.
                out_stream.write(
                    f"{cyan}{prompt_marker}{rst} {stripped}\n"
                    if color_enabled
                    else f"> {stripped}\n"
                )
                out_stream.flush()

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

            if ctx.agent_profiles is not None:
                try:
                    agent_request = ctx.agent_profiles.parse_prompt(stripped)
                except AgentProfileError as exc:
                    err_stream.write(f"agent mention error: {exc}\n")
                    continue
                if agent_request is not None:
                    await _run_agent_request(
                        ctx,
                        agent_request,
                        out_stream,
                        err_stream,
                        original_task=stripped,
                    )
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

            try:
                await _run_turn(ctx, stripped, out_stream, err_stream)
            except (KeyboardInterrupt, asyncio.CancelledError):
                out_stream.write("\n(interrupted - type /quit or Ctrl+D to exit)\n")
                out_stream.flush()
                continue
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
    "_accept_slash_completion",
    "_alternate_screen",
    "_clear_screen",
    "_compact_session_history",
    "_detect_shell_rc_path",
    "_export_session_markdown",
    "_format_file_size",
    "_highlight_prompt_input",
    "_manage_branch",
    "_manage_draft",
    "_manage_instructions",
    "_manage_permissions",
    "_manage_persona",
    "_manage_stash",
    "_maybe_offer_resume_prompt",
    "_new_session_id",
    "_offer_persist_to_shell_rc",
    "_place_completion_menu_above",
    "_position_prompt_at_bottom",
    "_print_active_context",
    "_print_header",
    "_print_provider_summary",
    "_print_slash_help",
    "_prompt_with_jobs",
    "_quote_for_shell",
    "_read_environ",
    "_resolve_provider_label",
    "_resume_chat_session",
    "_run_agent_request",
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
