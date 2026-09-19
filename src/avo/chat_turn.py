"""Turn execution for the chat REPL.

Sends one user turn through ``AgentRuntime``, records it on the
session, renders the compact reply summary, and
handles the ``/model`` provider swap plus the session-resume and
prompt-composition entry points the REPL loop calls.

Re-exported from :mod:`avo.chat` for backward compatibility.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from time import monotonic
from typing import TYPE_CHECKING, Any, TextIO

from avo.agent_profiles import AgentProfileError, DelegationRequest
from avo.app_tools.file_tools import bind_workspace
from avo.attachments import AttachmentError, prepare_prompt
from avo.chat_render import render_cooked_footer, render_thought_duration
from avo.chat_session import render_session_row
from avo.chat_stream import LiveAnswerPrinter, TerminalSpinner
from avo.config import (
    ConfigError,
    available_models,
    build_provider_from_env,
    default_model,
    is_known_model,
    supported_providers,
)
from avo.delegation import DelegationCoordinator, DelegationError
from avo.exceptions import AvoError
from avo.model_discovery import discover_provider_models
from avo.providers.streaming import split_thinking
from avo.storage.sqlite import SQLiteEventStore

if TYPE_CHECKING:
    from avo.background import BackgroundJobManager
    from avo.chat import ChatContext
    from avo.chat_session import SessionInfo, SessionLifecycle


def _model_picker_options(
    catalog: tuple[str, ...],
    *,
    current: str,
    recommended: str,
    labels: Mapping[str, str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Build picker labels separately from raw model ids."""

    options: list[tuple[str, str]] = []
    for index, name in enumerate(catalog, start=1):
        title = (labels or {}).get(name, name)
        marker = " (current)" if name == current else ""
        hint = " (recommended)" if name == recommended and not marker else ""
        options.append((f"{index}. {title}{hint}{marker}", name))
    return tuple(options)


async def _select_model_tui(
    options: tuple[tuple[str, str], ...],
    out: TextIO,
    in_stream: TextIO,
) -> str | None:
    """Select a model with the same searchable arrow picker as `/resume`."""

    if not (
        hasattr(in_stream, "isatty")
        and in_stream.isatty()
        and hasattr(out, "isatty")
        and out.isatty()
    ):
        return None

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    by_display = dict(options)

    class ModelCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            query = document.text_before_cursor.lower()
            for display, _model in options:
                if not query or query in display.lower():
                    yield Completion(
                        display,
                        start_position=-len(document.text_before_cursor),
                        display=display,
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

    out.write("\n╭─ Select model ─────────────────────────────────────────────╮\n")
    out.write("│ ↑/↓ choose · Enter select · type to search · Esc cancel   │\n")
    out.write("╰───────────────────────────────────────────────────────────╯\n")
    out.flush()
    session: Any = PromptSession(
        completer=ModelCompleter(),
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
    # Open the list immediately. The user should see the available models as
    # soon as `/model` enters picker mode, without having to press Down first.
    session.default_buffer.start_completion(select_first=True)
    try:
        selected = await session.prompt_async(HTML("<ansicyan>&gt;</ansicyan> "))
    except (EOFError, KeyboardInterrupt):
        out.write("\nModel selection cancelled.\n")
        return None
    return by_display.get(selected.strip())


async def _run_model_command(
    ctx: ChatContext,
    args: list[str],
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
    in_stream: TextIO | None = None,
) -> bool:
    """Handle ``/model`` — pick from the catalog or switch by model id.

    In a real terminal, no arguments opens a searchable arrow picker. Pipes
    and tests retain a plain catalog listing instead of blocking for input.
    """

    provider_name = ctx.provider_name
    catalog = available_models(provider_name)
    model_labels: dict[str, str] = {}
    dynamic_catalog = False
    catalog_result = None
    is_terminal = (
        in_stream is not None
        and hasattr(in_stream, "isatty")
        and in_stream.isatty()
        and hasattr(out, "isatty")
        and out.isatty()
    )
    if is_terminal:
        try:
            catalog_result = await discover_provider_models(
                provider_name,
                environ,
                static_models=catalog,
            )
        except Exception:
            catalog_result = None
        if catalog_result is not None and catalog_result.models:
            catalog = tuple(item.model_id for item in catalog_result.models)
            model_labels = {item.model_id: item.label for item in catalog_result.models}
            dynamic_catalog = catalog_result.source.value != "static"
            source_text = catalog_result.source.value
            stale_text = " · stale" if catalog_result.stale else ""
            out.write(f"Model catalog: {source_text}{stale_text}\n")
            if catalog_result.warning:
                out.write(f"  {catalog_result.warning}\n")
    if not catalog:
        err.write(
            f"provider {provider_name!r} has no model catalog; "
            f"set AVO_MODEL=<name> in your environment to override.\n"
        )
        return False

    if len(args) == 1:
        recommended = (
            next(
                (item.model_id for item in catalog_result.models if item.recommended),
                catalog[0],
            )
            if dynamic_catalog and catalog_result is not None
            else default_model(provider_name)
        )
        options = _model_picker_options(
            catalog,
            current=ctx.model_name,
            recommended=recommended,
            labels=model_labels,
        )
        selected = (
            await _select_model_tui(options, out, in_stream) if in_stream is not None else None
        )
        if selected is None:
            out.write(f"Models for provider {provider_name!r} (current: {ctx.model_name!r}):\n")
            for display, model in options:
                marker = "*" if model == ctx.model_name else " "
                out.write(f"  {marker} {display}\n")
            out.write("Pick a model with: /model NAME\n")
            return False
        args = ["/model", selected]

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

        if target not in catalog and not is_known_model(provider_name, target):
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


async def _run_agent_request(
    ctx: ChatContext,
    request: DelegationRequest,
    out: TextIO,
    err: TextIO,
    *,
    original_task: str,
) -> bool:
    """Execute recognized ``@agent`` mentions and render compact summaries."""

    registry = getattr(ctx, "agent_profiles", None)
    if registry is None:
        err.write("agent profiles are not initialized for this chat context.\n")
        return True
    ctx.session.record_user_turn(ctx.session_id, original_task)
    parent_run_id = f"chat.{ctx.session_id}.{uuid.uuid4().hex[:8]}"
    provider_factory = getattr(ctx, "provider_factory", None)
    try:
        coordinator = DelegationCoordinator(
            ctx.runtime,
            provider_factory=provider_factory,
            event_store_factory=lambda: SQLiteEventStore(ctx.store.path),
        )
        with bind_workspace(ctx.workspace):
            if getattr(request, "is_pipeline", False):
                results = await coordinator.pipeline(parent_run_id, request.parts)
            else:
                results = await coordinator.run(parent_run_id, request.parts)
    except (DelegationError, AgentProfileError) as exc:
        err.write(f"agent delegation error: {exc}\n")
        return True
    except Exception as exc:
        err.write(f"agent delegation failed: {type(exc).__name__}: {exc}\n")
        return True

    summaries: list[str] = []
    for result in results:
        out.write(f"• @{result.agent_name} [{result.status}] · {result.child_run_id}\n")
        if result.output:
            out.write(f"  {result.output}\n")
            summaries.append(f"@{result.agent_name}: {result.output}")
        elif result.error:
            out.write(f"  error: {result.error}\n")
            summaries.append(f"@{result.agent_name}: error: {result.error}")
        else:
            summaries.append(f"@{result.agent_name}: {result.status}")
    status = "completed" if all(result.status == "completed" for result in results) else "partial"
    ctx.session.record_assistant_turn(
        ctx.session_id,
        "\n".join(summaries),
        run_id=parent_run_id,
        status=status,
        stop_reason="completed" if status == "completed" else "internal_error",
        metadata={"delegated_agents": [result.agent_name for result in results]},
    )
    out.flush()
    return True


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
    out.write("\n" + ctx.session.render_transcript(session_id) + "\n")
    return False


async def _run_turn(ctx: ChatContext, task: str, out: TextIO, err: TextIO) -> None:
    """Execute one user turn against ``ctx.runtime``."""

    try:
        prepared = prepare_prompt(task, workspace_root=ctx.workspace.root)
    except AttachmentError as exc:
        err.write(f"attachment error: {exc}\n")
        return

    ctx.session.record_user_turn(ctx.session_id, task)
    effective_task = prepared.text
    message_content = prepared.content if prepared.attachments else None
    system_prompt = ctx.persona.render_system_prompt()
    started_at = monotonic()
    if ctx.pending_preamble is not None:
        effective_task = (
            f"{ctx.pending_preamble}\n\n"
            f"---\n"
            f"User's current message (continue directly without greeting):\n{effective_task}"
        )
        ctx.pending_preamble = None
        if message_content is not None:
            first = dict(message_content[0])
            first["text"] = effective_task
            message_content = [first, *message_content[1:]]

    spinner = TerminalSpinner(out, message="Thinking...")
    printer: LiveAnswerPrinter | None = None
    if ctx.stream_enabled:
        printer = LiveAnswerPrinter(out, on_first_content=spinner.stop_sync)
    # Bound per call rather than on the runtime attributes: a background
    # run() entering the shared runtime while this turn streams can no
    # longer snapshot the chat printer (the reverse race).
    from avo.combo.provider import ComboRouterProvider

    prior_notifier = None
    provider = ctx.runtime.provider
    if isinstance(provider, ComboRouterProvider):
        prior_notifier = provider.notifier

        def _combo_failover_notice(msg: str) -> None:
            spinner.stop_sync()
            color = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")
            notice = f"\033[33m{msg}\033[0m" if color else msg
            out.write(f"\n{notice}\n")
            out.flush()

        provider.notifier = _combo_failover_notice

    try:
        async with spinner:
            with bind_workspace(ctx.workspace):
                result = await ctx.runtime.run(
                    effective_task,
                    system_prompt=system_prompt,
                    message_content=message_content,
                    stream_callback=printer.feed if printer is not None else None,
                    stream_interrupt_callback=(
                        printer.on_interrupt if printer is not None else None
                    ),
                )
    except AvoError as exc:
        err.write(f"runtime error: {exc}\n")
        return
    except Exception as exc:
        err.write(f"unexpected error: {type(exc).__name__}: {exc}\n")
        return
    finally:
        if isinstance(provider, ComboRouterProvider):
            provider.notifier = prior_notifier
        if printer is not None:
            printer.finish()

    streamed_answer = printer is not None and printer.answered
    assistant_content = result.output or ""
    _thought, clean_answer = split_thinking(assistant_content)
    elapsed_seconds = monotonic() - started_at
    color_enabled = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")

    ctx.session.record_assistant_turn(
        ctx.session_id,
        clean_answer or assistant_content,
        run_id=result.run_id,
        status=result.status.value,
        stop_reason=result.stop_reason.value,
    )

    if result.error:
        out.write(f"error: {result.error}\n")
    out.write(render_thought_duration(elapsed_seconds, color=color_enabled))
    if not streamed_answer:
        if clean_answer:
            out.write(f"• {clean_answer}\n")
        elif result.output:
            out.write(f"• {result.output}\n")
    out.write(
        render_cooked_footer(
            elapsed_seconds,
            datetime.now().astimezone(),
            color=color_enabled,
        )
    )

    from avo.context_advisor import evaluate_session_context

    turns = ctx.session.turns(ctx.session_id)
    last_tokens = result.token_usage.total_tokens if result.token_accounting_available else None
    report = evaluate_session_context(
        turns,
        ctx.model_name,
        last_turn_tokens=last_tokens,
    )
    if report.is_warning and report.advice_message:
        out.write(f"\n💡 {report.advice_message}\n")
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
