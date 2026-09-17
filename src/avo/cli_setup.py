"""`avo setup` — scaffold global ~/.avo and workspace configuration.

Mirrors Claude Code's ``~/.claude/`` and Codex's ``~/.codex/`` directory
layouts. Configures:
- ``~/.avo/config.json`` — global provider, model, permissions, and stream defaults.
- ``~/.avo/instructions.md`` — global standing instructions applied across all workspaces.
- ``~/.avo/personas/`` — global custom role templates.
- ``~/.avo/skills/`` — user-installed global skills.
- ``~/.avo/plugins/`` — installed plugins and index.
- ``~/.avo/mcp.json`` — MCP server registrations.
- ``~/.avo/history`` — persistent command-line history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from avo.exceptions import AvoError

GLOBAL_AVO_DIR = Path.home() / ".avo"
GLOBAL_CONFIG_FILE = GLOBAL_AVO_DIR / "config.json"
GLOBAL_INSTRUCTIONS_FILE = GLOBAL_AVO_DIR / "instructions.md"
GLOBAL_PERSONAS_DIR = GLOBAL_AVO_DIR / "personas"
GLOBAL_SKILLS_DIR = GLOBAL_AVO_DIR / "skills"
GLOBAL_PLUGINS_DIR = GLOBAL_AVO_DIR / "plugins"
GLOBAL_MCP_FILE = GLOBAL_AVO_DIR / "mcp.json"
GLOBAL_HISTORY_FILE = GLOBAL_AVO_DIR / "history"

_DEFAULT_CONFIG = {
    "provider": "codex",
    "model": "gpt-5.6-sol",
    "permission_mode": "default",
    "stream": True,
}

_DEFAULT_INSTRUCTIONS = """# Global Avo Instructions

These instructions apply to all projects and workspaces you work on with Avo.
Add your global preferences, coding conventions, or operating guidelines here.
"""

_DEFAULT_MCP: dict[str, dict[str, object]] = {"mcpServers": {}}


class SetupCliError(AvoError):
    """User-facing failure in `avo setup`."""


@dataclass(frozen=True)
class SetupReport:
    """Summary of paths created or checked during setup."""

    base_dir: Path
    created_files: tuple[Path, ...]
    existing_files: tuple[Path, ...]


def load_global_avo_config(base_dir: Path | None = None) -> dict[str, str]:
    """Read ~/.avo/config.json and return standard AVO_* environment variables."""

    target = (base_dir or GLOBAL_AVO_DIR) / "config.json"
    if not target.is_file():
        return {}

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    env_mapping: dict[str, str] = {}
    if "provider" in data and isinstance(data["provider"], str) and data["provider"].strip():
        env_mapping["AVO_PROVIDER"] = data["provider"].strip()
    if "model" in data and isinstance(data["model"], str) and data["model"].strip():
        env_mapping["AVO_MODEL"] = data["model"].strip()
    if "permission_mode" in data and isinstance(data["permission_mode"], str):
        permission_mode = data["permission_mode"].strip().lower()
        if permission_mode == "bypass":
            permission_mode = "bypass_permissions"
        env_mapping["AVO_PERMISSION_MODE"] = permission_mode
    if "stream" in data:
        env_mapping["AVO_CHAT_STREAM"] = "1" if bool(data["stream"]) else "0"
    if "base_url" in data and isinstance(data["base_url"], str) and data["base_url"].strip():
        prov = env_mapping.get("AVO_PROVIDER", "").upper()
        if prov:
            env_mapping[f"AVO_{prov}_BASE_URL"] = data["base_url"].strip()

    return env_mapping


def setup_global_avo(
    target_dir: Path | None = None,
    *,
    force: bool = False,
) -> SetupReport:
    """Ensure ~/.avo/ structure exists and is populated with defaults."""

    base = (target_dir or GLOBAL_AVO_DIR).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    existing: list[Path] = []

    personas_dir = base / "personas"
    skills_dir = base / "skills"
    plugins_dir = base / "plugins"

    for d in (personas_dir, skills_dir, plugins_dir):
        d.mkdir(parents=True, exist_ok=True)

    config_path = base / "config.json"
    if not config_path.exists() or force:
        config_path.write_text(json.dumps(_DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        created.append(config_path)
    else:
        existing.append(config_path)

    inst_path = base / "instructions.md"
    if not inst_path.exists() or force:
        inst_path.write_text(_DEFAULT_INSTRUCTIONS, encoding="utf-8")
        created.append(inst_path)
    else:
        existing.append(inst_path)

    mcp_path = base / "mcp.json"
    if not mcp_path.exists() or force:
        mcp_path.write_text(json.dumps(_DEFAULT_MCP, indent=2) + "\n", encoding="utf-8")
        created.append(mcp_path)
    else:
        existing.append(mcp_path)

    history_path = base / "history"
    if not history_path.exists():
        history_path.touch(mode=0o600)
        created.append(history_path)
    else:
        existing.append(history_path)

    return SetupReport(
        base_dir=base,
        created_files=tuple(created),
        existing_files=tuple(existing),
    )


def render_setup_card(report: SetupReport, *, color: bool = True) -> str:
    """Render a full ASCII box-drawing summary of ~/.avo setup."""

    bold_cyan = "\033[1;36m" if color else ""
    dim = "\033[90m" if color else ""
    green = "\033[32m" if color else ""
    rst = "\033[0m" if color else ""

    lines = [
        (
            f"╭─── {bold_cyan}Avo Global Configuration (~/.avo){rst} "
            "─────────────────────────────────────╮"
        ),
        f"│ {dim}Directory   {rst}: {report.base_dir!s:<58} │",
        f"│ {dim}Config      {rst}: {report.base_dir / 'config.json'!s:<58} │",
        f"│ {dim}Instructions{rst}: {report.base_dir / 'instructions.md'!s:<58} │",
        f"│ {dim}Personas    {rst}: {str(report.base_dir / 'personas') + '/':<58} │",
        f"│ {dim}Skills      {rst}: {str(report.base_dir / 'skills') + '/':<58} │",
        f"│ {dim}Plugins     {rst}: {str(report.base_dir / 'plugins') + '/':<58} │",
        f"│ {dim}MCP Servers {rst}: {report.base_dir / 'mcp.json'!s:<58} │",
        f"│ {dim}History     {rst}: {report.base_dir / 'history'!s:<58} │",
        "╰────────────────────────────────────────────────────────────────────────────╯",
    ]
    if report.created_files:
        lines.append(
            f"{green}✓ Created {len(report.created_files)} file(s) in {report.base_dir}{rst}"
        )
    else:
        lines.append(f"{dim}All global configuration files already present.{rst}")
    lines.append(f"{dim}Edit ~/.avo/config.json or ~/.avo/instructions.md to customize.{rst}\n")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None) -> int:
    """CLI entry point for `avo setup`."""

    out = stdout or sys.stdout
    parser = argparse.ArgumentParser(
        prog="avo setup",
        description="Configure global ~/.avo directory and standing instructions.",
    )
    parser.add_argument(
        "--global",
        "-g",
        dest="is_global",
        action="store_true",
        default=True,
        help="Configure global user environment in ~/.avo (default).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Custom target directory (default: ~/.avo).",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing configuration files with defaults.",
    )

    args = parser.parse_args(argv)
    target = args.dir if args.dir is not None else GLOBAL_AVO_DIR
    color_enabled = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")

    try:
        report = setup_global_avo(target, force=args.force)
        out.write(render_setup_card(report, color=color_enabled))
        out.flush()
        return 0
    except Exception as exc:
        out.write(f"setup error: {exc}\n")
        out.flush()
        return 1
