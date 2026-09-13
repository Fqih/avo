"""Dependency-free command-line inspection for SQLite run databases."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from avo import __version__
from avo.chat import run_repl
from avo.doctor import main as doctor_main
from avo.exceptions import AvoError
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime
from avo.storage.sqlite import SQLiteEventStore
from avo.tracing import TraceInspector


def _tail_argv(command: str) -> list[str]:
    """Return argv after the leading ``avo <command>`` tokens.

    Used by the delegated plugin/mcp/skill subcommands. Falls back to
    an empty list when argv is unavailable (for example when the CLI is
    invoked programmatically with ``argv=None``).
    """

    argv = sys.argv[1:]
    if argv and argv[0] == command:
        argv = argv[1:]
    return argv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo",
        description="Inspect, resume, and chat with Avo SQLite runs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"avo {__version__}",
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=Path("avo.db"),
        help="SQLite database path (default: avo.db).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    runs = commands.add_parser("runs", help="Manage persisted runs.")
    run_commands = runs.add_subparsers(dest="runs_command", required=True)
    run_commands.add_parser("list", help="List runs.")
    inspect_parser = run_commands.add_parser("inspect", help="Render one run trace.")
    inspect_parser.add_argument("run_id")
    resume_parser = run_commands.add_parser("resume", help="Resume a scripted fake-provider run.")
    resume_parser.add_argument("run_id")
    diff_parser = run_commands.add_parser(
        "diff", help="Compare two persisted runs (see `avo runs diff --help`)."
    )
    diff_parser.add_argument("run_a")
    diff_parser.add_argument("run_b")
    diff_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable table.",
    )

    chat = commands.add_parser(
        "chat",
        help="Start an interactive chat REPL that drives one AgentRuntime turn per input.",
    )
    chat.add_argument(
        "--database",
        "-d",
        type=Path,
        default=Path("avo.db"),
        help="SQLite database path (default: avo.db).",
    )
    chat.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace directory file tools are bound to (default: current working directory).",
    )
    chat.add_argument(
        "--session",
        type=str,
        default=None,
        metavar="SESSION_ID",
        help="Resume an existing chat session by id (default: start a fresh thread; "
        "without --session the REPL offers to resume the most recent thread).",
    )
    chat.add_argument(
        "--new-session",
        action="store_true",
        help="Always start a fresh chat session, ignoring any prior threads.",
    )

    commands.add_parser(
        "doctor",
        help="Verify AVO_ provider configuration without making an HTTP call.",
    )

    # Plugin / MCP / skill subcommands delegate to their own modules.
    commands.add_parser(
        "plugin",
        help="Manage avo plugins (see `avo plugin --help`).",
    )
    commands.add_parser(
        "mcp",
        help="Manage MCP server registrations (see `avo mcp --help`).",
    )
    commands.add_parser(
        "skill",
        help="Manage skill packs (see `avo skill --help`).",
    )

    commands.add_parser(
        "init",
        help="Scaffold .avo/skills/ and AGENTS.md in the current directory.",
    )

    commands.add_parser(
        "bench",
        help="Run a deterministic benchmark against the FakeProvider (see `avo bench --help`).",
    )

    commands.add_parser(
        "sandbox",
        help="Run a command inside an ephemeral docker sandbox (see `avo sandbox --help`).",
    )

    commands.add_parser(
        "cost",
        help="Aggregate token usage and USD cost across recorded Avo runs (see `avo cost --help`).",
    )

    serve_mcp = commands.add_parser(
        "serve-mcp",
        help="Expose the default application tool registry over MCP stdio (see docs/mcp.md).",
    )
    serve_mcp.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace directory exposed via MCP resources (default: current working directory).",
    )

    commands.add_parser(
        "login",
        add_help=False,
        help="Manage OAuth and API credentials (see `avo login --help`).",
    )
    commands.add_parser(
        "ui",
        add_help=False,
        help="Launch the local Web UI dashboard (see `avo ui --help`).",
    )

    return parser


async def _execute(args: argparse.Namespace, rest: list[str] | None = None) -> int:
    tail = rest if rest is not None else []
    if args.command == "ui":
        from avo.web_ui import main as web_ui_main

        return web_ui_main(tail or _tail_argv("ui"))

    if args.command == "login":
        from avo.auth import main_login

        return main_login(tail or _tail_argv("login"))

    if args.command == "doctor":
        # ``doctor_main`` has already-consumed argv; pass an empty list
        # so the inner argparse does not re-read sys.argv and complain
        # about the parent command's tail.
        return doctor_main([])

    if args.command == "plugin":
        # Plugin subcommands re-parse argv themselves. Pull the tail
        # off ``sys.argv`` minus the leading ``avo plugin`` tokens.
        from avo.cli_plugins import main as plugin_main

        return plugin_main(_tail_argv("plugin"))

    if args.command == "mcp":
        from avo.cli_mcp import main as mcp_main

        return mcp_main(_tail_argv("mcp"))

    if args.command == "skill":
        from avo.cli_skills import main as skill_main

        return skill_main(_tail_argv("skill"))

    if args.command == "init":
        from avo.cli_init import main as init_main

        return init_main(_tail_argv("init"))

    if args.command == "bench":
        from avo.bench import main as bench_main

        return bench_main(_tail_argv("bench"))

    if args.command == "sandbox":
        from avo.cli_sandbox import main as sandbox_main

        return sandbox_main(_tail_argv("sandbox"))

    if args.command == "cost":
        from avo.cost import main as cost_main

        return cost_main(_tail_argv("cost"))

    if args.command == "serve-mcp":
        from avo.mcp_server import build_default_registry
        from avo.mcp_server.server import AvoMcpServer

        workspace_root = (args.workspace_root or Path.cwd()).resolve()
        registry = build_default_registry()
        server = AvoMcpServer(registry=registry, workspace_root=workspace_root)
        server.run_forever()
        return 0

    if args.command == "chat":
        workspace_root = (args.workspace_root or Path.cwd()).resolve()
        db_path = args.database if args.database is not None else Path("avo.db")
        return await run_repl(
            database_path=db_path,
            workspace_root=workspace_root,
            session_id=args.session,
            force_new_session=args.new_session,
        )

    store = SQLiteEventStore(args.database)
    try:
        if args.runs_command == "list":
            runs = await store.list_runs()
            if not runs:
                print("No runs found.")
                return 0
            print(f"{'RUN ID':36}  {'STATE':20}  {'STOP REASON':24}  STEPS")
            for run in runs:
                reason = run.stop_reason.value if run.stop_reason is not None else "-"
                print(f"{run.run_id:36}  {run.state.value:20}  {reason:24}  {run.steps}")
            return 0

        if args.runs_command == "inspect":
            trace = await TraceInspector(store).inspect(args.run_id)
            print(trace.to_text())
            return 0

        if args.runs_command == "diff":
            from avo.diff import diff_runs

            report = diff_runs(store, run_a=args.run_a, run_b=args.run_b)
            if args.json:
                print(report.to_json())
            else:
                print(report.to_text(), end="")
            return 0

        if args.runs_command == "resume":
            checkpoint = await store.get_latest_checkpoint(args.run_id)
            if checkpoint is None:
                raise AvoError(f"Run {args.run_id!r} has no checkpoint and cannot be resumed.")
            if checkpoint.provider_metadata.get("provider_type") != "fake":
                raise AvoError(
                    "CLI resume can reconstruct only the built-in FakeProvider. "
                    "Resume real providers from application code with the configured adapter."
                )
            if (
                checkpoint.pending_response is not None
                and checkpoint.pending_response.tool_call is not None
            ):
                raise AvoError(
                    "CLI resume cannot reconstruct application tool callables. "
                    "Resume this run from application code with its ToolRegistry."
                )
            provider = FakeProvider.from_snapshot(checkpoint.provider_metadata)
            runtime = AgentRuntime(provider=provider, event_store=store)
            result = await runtime.resume(args.run_id)
            print(f"Run {result.run_id}: {result.status.value} ({result.stop_reason.value})")
            return 0
        raise AvoError(f"Unknown runs command: {args.runs_command!r}.")
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Avo CLI and return a process exit status."""

    parser = _parser()
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    args, rest = parser.parse_known_args(effective_argv)
    if rest and args.command not in {
        "login",
        "ui",
        "plugin",
        "mcp",
        "skill",
        "init",
        "bench",
        "sandbox",
        "cost",
    }:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")
    try:
        return asyncio.run(_execute(args, rest=rest))
    except (AvoError, OSError) as exc:
        print(f"avo: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
