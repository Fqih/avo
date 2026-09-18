"""Operator-facing token-saver preset commands."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from avo.savers.config_store import write_saver_setting
from avo.savers.presets import BUILTIN_PRESETS, builtin_skill_body


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avo saver", description="Manage token-saver presets.")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Avo config directory (default: $AVO_CONFIG_DIR or ~/.config/avo).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List available presets.")
    show = commands.add_parser("show", help="Show one preset and its prompt addendum.")
    show.add_argument("name")
    use = commands.add_parser("use", help="Persist a preset for future chats.")
    use.add_argument("name")
    commands.add_parser("off", help="Disable the persisted preset.")
    return parser


def _valid_names() -> str:
    return ", ".join(BUILTIN_PRESETS)


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``avo saver`` and return a process exit status."""

    args = _parser().parse_args(list(argv) if argv is not None else None)
    config_dir = args.config_dir
    if args.command == "list":
        print(f"{'NAME':10}  DESCRIPTION")
        for preset in BUILTIN_PRESETS.values():
            print(f"{preset.name:10}  {preset.description}")
        return 0

    if args.command == "show":
        shown_preset = BUILTIN_PRESETS.get(args.name)
        if shown_preset is None:
            print(
                f"avo saver: unknown preset {args.name!r}; valid names: {_valid_names()}",
                file=sys.stderr,
            )
            return 2
        print(f"{shown_preset.name}: {shown_preset.description}")
        if shown_preset.skill_name is not None:
            print(f"\nAddendum ({shown_preset.skill_name}):")
            print(builtin_skill_body(shown_preset.skill_name).strip())
        else:
            print("\nAddendum: none")
        print("\nPipeline:")
        print("  configured" if shown_preset.pipeline is not None else "  none")
        return 0

    if args.command == "use":
        if args.name not in BUILTIN_PRESETS:
            print(
                f"avo saver: unknown preset {args.name!r}; valid names: {_valid_names()}",
                file=sys.stderr,
            )
            return 2
        write_saver_setting(args.name, config_dir)
        print(f"Token saver enabled: {args.name}. Restart running chats to apply it.")
        return 0

    write_saver_setting(None, config_dir)
    print("Token saver disabled. Restart running chats to confirm the change.")
    return 0


__all__ = ["main"]
