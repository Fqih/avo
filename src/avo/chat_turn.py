"""Turn execution for the chat REPL.

Sends one user turn through ``AgentRuntime``, records it on the
session, renders the reply (including split thinking output), and
handles the ``/model`` provider swap plus the session-resume and
prompt-composition entry points the REPL loop calls.

Re-exported from :mod:`avo.chat` for backward compatibility.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, TextIO

from avo.app_tools.file_tools import bind_workspace
from avo.chat_session import render_session_row
from avo.chat_stream import LiveAnswerPrinter
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

if TYPE_CHECKING:
    from avo.background import BackgroundJobManager
    from avo.chat import ChatContext
    from avo.chat_session import SessionInfo, SessionLifecycle


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
    system_prompt = ctx.persona.render_system_prompt()
    if system_prompt:
        effective_task = f"[System Context]\n{system_prompt}\n\n---\n{effective_task}"
    if ctx.pending_preamble is not None:
        effective_task = (
            f"{ctx.pending_preamble}\n\n"
            f"---\n"
            f"User's current message (continue directly without greeting):\n{effective_task}"
        )
        ctx.pending_preamble = None

    printer: LiveAnswerPrinter | None = None
    if ctx.stream_enabled:
        printer = LiveAnswerPrinter(out)
        ctx.runtime.stream_callback = printer.feed
        ctx.runtime.stream_interrupt_callback = printer.on_interrupt
    try:
        with bind_workspace(ctx.workspace):
            result = await ctx.runtime.run(effective_task)
    except AvoError as exc:
        err.write(f"runtime error: {exc}\n")
        return
    except Exception as exc:
        err.write(f"unexpected error: {type(exc).__name__}: {exc}\n")
        return
    finally:
        if printer is not None:
            ctx.runtime.stream_callback = None
            ctx.runtime.stream_interrupt_callback = None
            printer.finish()

    streamed_answer = printer is not None and printer.answered
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
    if not streamed_answer:
        if clean_answer:
            out.write(f"Avo> {clean_answer}\n")
        elif result.output:
            out.write(f"Avo> {result.output}\n")

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
