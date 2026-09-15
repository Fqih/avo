"""`avo mcp` — register and inspect MCP server commands.

Configuration lives in ``~/.avo/mcp.json`` as a JSON object keyed by
server name. Each value is the argv that the chat REPL will spawn
when the server needs to be reached — exactly the same shape as
Claude Code's ``.mcp.json`` and Codex's ``~/.codex/mcp.json``.

Example ``mcp.json``::

    {
      "filesystem": ["avo-filesystem", "--root", "/srv/agent"],
      "github":     ["mcp-server-github", "--token", "${GITHUB_TOKEN}"]
    }

``add`` writes the entry; ``remove`` deletes it; ``list`` prints a
table. The chat REPL reads the same file when it boots and attaches
every registered server.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from avo.exceptions import AvoError

MCP_CONFIG_PATH = Path.home() / ".avo" / "mcp.json"


class McpCliError(AvoError):
    """User-facing failure in `avo mcp`."""


@dataclass(frozen=True)
class McpServerEntry:
    """One registered MCP server."""

    name: str
    command: tuple[str, ...]
    env: dict[str, str]


def _read_config() -> dict[str, dict[str, object]]:
    if not MCP_CONFIG_PATH.exists():
        return {}
    try:
        loaded = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise McpCliError(f"corrupt MCP config at {MCP_CONFIG_PATH}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise McpCliError(f"MCP config at {MCP_CONFIG_PATH} must be a JSON object.")
    return loaded


def _write_config(config: dict[str, dict[str, object]]) -> None:
    MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MCP_CONFIG_PATH.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def _validate_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise McpCliError(f"invalid MCP server name {name!r}; use letters, digits, '.', '-', '_'.")
    return name


_SECRET_KEY_TOKENS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "AUTH", "CREDENTIAL")


def _looks_secret(name: str) -> bool:
    """Return True if ``name`` looks like it carries a secret value."""

    upper = name.upper()
    return any(token in upper for token in _SECRET_KEY_TOKENS)


def add(name: str, command: Sequence[str], *, env: dict[str, str] | None = None) -> McpServerEntry:
    """Register an MCP server by name with the given argv."""

    name = _validate_name(name)
    if not command:
        raise McpCliError("command must contain at least one argv entry.")
    resolved_env = dict(env or {})
    config = _read_config()
    config[name] = {"command": list(command), "env": resolved_env}
    _write_config(config)
    return McpServerEntry(name=name, command=tuple(command), env=resolved_env)


def remove(name: str) -> None:
    """Remove an MCP server by name."""

    name = _validate_name(name)
    config = _read_config()
    if name not in config:
        raise McpCliError(f"MCP server {name!r} is not registered.")
    config.pop(name, None)
    _write_config(config)


def list_servers() -> tuple[McpServerEntry, ...]:
    """Return all registered MCP servers (sorted by name)."""

    out: list[McpServerEntry] = []
    for name, entry in sorted(_read_config().items()):
        if not isinstance(entry, dict):
            continue
        command_raw = entry.get("command") or []
        env_raw = entry.get("env") or {}
        if not isinstance(command_raw, list):
            continue
        command = tuple(str(part) for part in command_raw)
        env = {str(k): str(v) for k, v in env_raw.items()} if isinstance(env_raw, dict) else {}
        out.append(McpServerEntry(name=name, command=command, env=env))
    return tuple(out)


def load_servers(path: Path | None = None) -> tuple[McpServerEntry, ...]:
    """Load MCP servers from ``path`` (default :data:`MCP_CONFIG_PATH`).

    Returns an empty tuple when the file does not exist. Used by the
    chat REPL so it can attach every server on boot.
    """

    target = path or MCP_CONFIG_PATH
    if not target.exists():
        return ()
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise McpCliError(f"corrupt MCP config at {target}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise McpCliError(f"MCP config at {target} must be a JSON object.")
    out: list[McpServerEntry] = []
    for name, entry in loaded.items():
        if not isinstance(entry, dict):
            continue
        command_raw = entry.get("command") or []
        env_raw = entry.get("env") or {}
        if not isinstance(command_raw, list):
            continue
        command = tuple(str(part) for part in command_raw)
        env = {str(k): str(v) for k, v in env_raw.items()} if isinstance(env_raw, dict) else {}
        out.append(McpServerEntry(name=str(name), command=command, env=env))
    return tuple(out)


def resolve_env(entry: McpServerEntry) -> dict[str, str]:
    """Resolve ``${VAR}`` placeholders in an MCP server env map."""

    resolved: dict[str, str] = {}
    for key, value in entry.env.items():
        resolved[key] = os.path.expandvars(value)
    return resolved


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo mcp",
        description="Manage MCP server registrations (~/.avo/mcp.json).",
    )
    sub = parser.add_subparsers(dest="mcp_command", required=True)

    add_p = sub.add_parser("add", help="Register an MCP server.")
    add_p.add_argument("name", help="Server name (used as the registry key).")
    add_p.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Environment variable to set when launching (repeatable).",
    )
    add_p.add_argument(
        "command",
        nargs="*",
        help="Server command argv (default: empty — edit mcp.json manually).",
    )

    sub.add_parser("list", help="List registered MCP servers.")

    remove_p = sub.add_parser("remove", help="Remove a registered MCP server.")
    remove_p.add_argument("name")
    remove_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )

    return parser


def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    """Print ``prompt`` and read a y/N answer from stdin."""

    if assume_yes:
        return True
    print(prompt, end="", flush=True)
    try:
        answer = input().strip().lower()
    except EOFError:
        print()
        return False
    return answer in ("y", "yes")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    # parse_known_args + merge: on Python <=3.11 argparse cannot
    # interleave the variadic `command` positionals after the `--env`
    # optionals (they land in the extras list); on 3.12+/3.13 it can.
    # Merging the extras back in makes the trailing-argv semantics
    # identical across all supported interpreters.
    args, extras = parser.parse_known_args(argv)
    if args.mcp_command == "add" and extras:
        args.command = [*args.command, *extras]

    if args.mcp_command == "add":
        env: dict[str, str] = {}
        for raw in args.env:
            if "=" not in raw:
                raise McpCliError(f"--env expects KEY=VALUE, got {raw!r}")
            key, value = raw.split("=", 1)
            env[key.strip()] = value
        if not args.command:
            raise McpCliError(
                "command required; pass the server argv after the name "
                "(e.g. `avo mcp add demo --env FOO=BAR echo hi`)."
            )
        entry = add(args.name, args.command, env=env)
        cmd_str = " ".join(entry.command)
        print(f"Registered MCP server {entry.name!r}: {cmd_str}")
        if env:
            masked = {k: ("***" if _looks_secret(k) else v) for k, v in env.items()}
            print(f"  env: {masked}")
        return 0

    if args.mcp_command == "list":
        servers = list_servers()
        if not servers:
            print(f"No MCP servers registered at {MCP_CONFIG_PATH}.")
            print("Try: avo mcp add filesystem -- python -m avo.mcp_servers.filesystem")
            return 0
        print(f"{'NAME':24}  COMMAND")
        for server in servers:
            cmd_str = " ".join(server.command)
            print(f"{server.name:24}  {cmd_str}")
        return 0

    if args.mcp_command == "remove":
        if not _confirm(
            f"Remove MCP server {args.name!r}? This rewrites {MCP_CONFIG_PATH}. [y/N] ",
            assume_yes=args.yes,
        ):
            print("Aborted.")
            return 1
        remove(args.name)
        print(f"Removed MCP server {args.name!r}.")
        return 0

    raise McpCliError(f"unknown mcp subcommand: {args.mcp_command}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MCP_CONFIG_PATH",
    "McpCliError",
    "McpServerEntry",
    "add",
    "list_servers",
    "load_servers",
    "main",
    "remove",
    "resolve_env",
]
