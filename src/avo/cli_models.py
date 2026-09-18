"""Command-line entrypoint for provider model discovery and management."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo models",
        description="Discover and manage Ollama Local and Ollama Cloud models.",
        epilog=(
            "Examples:\n"
            "  avo models ollama list\n"
            "  avo models ollama recommend\n"
            "  avo models ollama pull qwen2.5-coder:7b\n"
            "\n"
            "Local pulls always show size and ask for confirmation. Cloud models\n"
            "run remotely and may consume account quota."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="provider")
    ollama = commands.add_parser("ollama", help="Manage Ollama Local/Cloud models.")
    ollama_commands = ollama.add_subparsers(dest="action")
    ollama_commands.add_parser("list", help="List models installed or available locally.")
    ollama_commands.add_parser("recommend", help="Recommend local models from hardware.")
    pull = ollama_commands.add_parser("pull", help="Confirm and download a local model.")
    pull.add_argument("model", help="Ollama model name to download.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render model-management help until the model manager is connected."""

    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.provider is None:
        parser.print_help()
        return 0
    if args.action is None:
        parser.parse_args(["ollama", "--help"])
    parser.error("Ollama model management is not available in this build yet.")


__all__ = ["main"]
