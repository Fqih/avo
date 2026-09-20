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
from collections.abc import Mapping, Sequence
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

_GLOBAL_PATH_NAMES = frozenset(
    {
        "config.json",
        "instructions.md",
        "personas",
        "skills",
        "plugins",
        "mcp.json",
        "history",
    }
)


def global_avo_dir(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Resolve the global config directory at call time.

    ``Path.home()`` and ``AVO_CONFIG_DIR`` are intentionally evaluated for
    every call.  Import-time path constants made tests and embedded callers
    write to the developer's real ``~/.avo`` directory after changing their
    environment.
    """

    env = os.environ if environ is None else environ
    configured = env.get("AVO_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return ((home if home is not None else Path.home()) / ".avo").expanduser().resolve()


def global_avo_path(
    name: str,
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Return a supported path below the call-time global config directory."""

    if name not in _GLOBAL_PATH_NAMES:
        raise ValueError(f"Unsupported global Avo path: {name!r}")
    return global_avo_dir(environ, home=home) / name


_DEFAULT_CONFIG = {
    "provider": "codex",
    "model": "gpt-5.6-sol",
    "allow_subscription": False,
    "permission_mode": "default",
    "stream": True,
}

_DEFAULT_INSTRUCTIONS = """# Global Avo Instructions

These instructions apply to all projects and workspaces you work on with Avo.
Add your global preferences, coding conventions, or operating guidelines here.
"""

_DEFAULT_MCP: dict[str, dict[str, object]] = {"mcpServers": {}}

_SECURITY_CONFIG_ENV_KEYS = {
    "require_approval": "AVO_TOOLS_REQUIRE_APPROVAL",
    "sandbox_required": "AVO_SANDBOX_REQUIRED",
    "sandbox_network": "AVO_SANDBOX_NETWORK",
    "sandbox_timeout_seconds": "AVO_SANDBOX_TIMEOUT_SECONDS",
    "plugin_editable": "AVO_PLUGIN_EDITABLE",
    "plugin_activation": "AVO_PLUGIN_ACTIVATION",
    "web_allowed_origin": "AVO_WEB_ALLOWED_ORIGIN",
    "web_cors_enabled": "AVO_WEB_CORS_ENABLED",
}


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

    target = (base_dir / "config.json") if base_dir is not None else global_avo_path("config.json")
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
    for config_key, env_key in _SECURITY_CONFIG_ENV_KEYS.items():
        value = data.get(config_key)
        if isinstance(value, bool):
            env_mapping[env_key] = "1" if value else "0"
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            env_mapping[env_key] = str(value)
        elif isinstance(value, str) and value.strip():
            env_mapping[env_key] = value.strip()
    if "stream" in data:
        env_mapping["AVO_CHAT_STREAM"] = "1" if bool(data["stream"]) else "0"
    if data.get("allow_subscription") is True:
        env_mapping["AVO_ALLOW_SUBSCRIPTION"] = "1"
    if "base_url" in data and isinstance(data["base_url"], str) and data["base_url"].strip():
        prov = env_mapping.get("AVO_PROVIDER", "").upper()
        if prov:
            env_mapping[f"AVO_{prov}_BASE_URL"] = data["base_url"].strip()

    return env_mapping


def remember_last_provider(
    environ: Mapping[str, str],
    base_dir: Path | None = None,
) -> Path | None:
    """Persist the last provider/model choice without copying any secrets.

    The first-run wizard historically exported values to the shell rc file,
    which only affected a future shell and made the next ``avo`` invocation
    appear unconfigured.  Global config stores only routing preferences; API
    keys and OAuth tokens remain in the credential store.
    """

    provider = environ.get("AVO_PROVIDER", "").strip()
    model = environ.get("AVO_MODEL", "").strip()
    if not provider or not model:
        return None

    if base_dir is not None:
        target_dir = base_dir.expanduser().resolve()
    else:
        # Login callers pass only the runtime provider/model mapping.  Keep
        # the process-level config override in that case instead of silently
        # falling back to an import-time ~/.avo path.
        path_environ = environ if "AVO_CONFIG_DIR" in environ else os.environ
        target_dir = global_avo_dir(path_environ)
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / "config.json"
    try:
        current = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current = {}
    if not isinstance(current, dict):
        current = {}

    current["provider"] = provider
    current["model"] = model
    current["allow_subscription"] = environ.get("AVO_ALLOW_SUBSCRIPTION") == "1"
    config_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return config_path


def setup_global_avo(
    target_dir: Path | None = None,
    *,
    force: bool = False,
    allow_subscription: bool = False,
) -> SetupReport:
    """Ensure ~/.avo/ structure exists and is populated with defaults."""

    base = target_dir.expanduser().resolve() if target_dir is not None else global_avo_dir()
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

    if allow_subscription:
        try:
            current_config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current_config = {}
        if not isinstance(current_config, dict):
            current_config = {}
        current_config["allow_subscription"] = True
        config_path.write_text(json.dumps(current_config, indent=2) + "\n", encoding="utf-8")

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
    parser.add_argument(
        "--allow-subscription",
        action="store_true",
        help="Explicitly enable stored subscription OAuth credentials for this user.",
    )

    args = parser.parse_args(argv)
    target = args.dir if args.dir is not None else global_avo_dir()
    color_enabled = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")

    try:
        report = setup_global_avo(
            target,
            force=args.force,
            allow_subscription=args.allow_subscription,
        )
        out.write(render_setup_card(report, color=color_enabled))
        out.flush()
        return 0
    except Exception as exc:
        out.write(f"setup error: {exc}\n")
        out.flush()
        return 1
