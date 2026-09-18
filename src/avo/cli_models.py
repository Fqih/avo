"""Command-line entrypoint for provider model discovery and management."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from avo.hardware import detect_hardware
from avo.model_catalog import recommend_ollama_models
from avo.ollama_manager import (
    OllamaManager,
    OllamaPullRefused,
    PullPlan,
    PullProgress,
)


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
    ollama.add_argument(
        "--base-url",
        default="http://localhost:11434",
        help="Ollama endpoint for Local discovery (default: http://localhost:11434).",
    )
    ollama_commands = ollama.add_subparsers(dest="action")
    ollama_commands.add_parser("list", help="List models installed or available locally.")
    ollama_commands.add_parser("recommend", help="Recommend local models from hardware.")
    pull = ollama_commands.add_parser("pull", help="Confirm and download a local model.")
    pull.add_argument("model", help="Ollama model name to download.")
    ollama_commands.add_parser("cloud", help="Show remote Ollama Cloud guidance and models.")
    return parser


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown size"
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GB"
    return f"{value / 1024**2:.0f} MB"


def _print_progress(progress: PullProgress) -> None:
    if progress.completed_bytes is not None and progress.total_bytes:
        percent = progress.completed_bytes / progress.total_bytes * 100
        print(f"  {progress.status}: {percent:.0f}%")
    else:
        print(f"  {progress.status}")


def _prompt_pull_confirmation(prompt: str) -> str:
    """Read a terminal confirmation; this command is intentionally interactive."""

    return input(prompt)


async def _run_ollama(args: argparse.Namespace) -> int:
    if args.action == "cloud":
        print("Ollama Cloud (remote)")
        print("  Login: avo login ollama-cloud --key-stdin")
        print("  Cloud models run remotely and are not downloaded by Avo.")
        print("  Example: qwen3-coder:480b-cloud")
        return 0

    manager = OllamaManager(args.base_url)
    health = await manager.check_health()
    if not health.available:
        print(f"Ollama Local is unavailable at {args.base_url}: {health.detail}")
        return 1

    models = await manager.list_models()
    if args.action == "list":
        print(f"Ollama Local ({args.base_url}) — daemon {health.version or 'ready'}")
        if not models:
            print("  No local models installed.")
        for model in models:
            print(f"  • {model.name:28} {_format_bytes(model.size_bytes)}")
        return 0

    if args.action == "recommend":
        profile = detect_hardware()
        if profile.vram_gb is not None:
            hardware_line = (
                f"Hardware: {profile.system}, {profile.cpu_cores} CPU cores, "
                f"{profile.ram_gb:.1f} GB RAM, {profile.vram_gb:.1f} GB VRAM"
            )
        else:
            hardware_line = (
                f"Hardware: {profile.system}, {profile.cpu_cores} CPU cores, "
                f"{profile.ram_gb:.1f} GB RAM, VRAM unknown"
            )
        print(hardware_line)
        installed = tuple(model.name for model in models)
        for item in recommend_ollama_models(profile, installed=installed):
            marker = "installed" if item.name in installed else item.fit
            print(
                f"  • {item.name:24} [{marker:9}] "
                f"{_format_bytes(item.download_bytes)} — {item.reason}"
            )
        return 0

    if args.action == "pull":

        async def confirm(plan: PullPlan) -> bool:
            if plan.already_installed:
                print(f"{plan.model} is already installed; refresh it anyway?")
            answer = _prompt_pull_confirmation(
                f"Download {plan.model} ({_format_bytes(plan.download_bytes)}) "
                "to this computer? [y/N]: "
            )
            return answer.strip().lower() in {"y", "yes"}

        try:
            result = await manager.pull(args.model, confirm=confirm, output=_print_progress)
        except OllamaPullRefused:
            print("Download cancelled; no local model was changed.")
            return 1
        except Exception as exc:
            print(f"Ollama pull failed: {exc}")
            return 1
        print(f"✓ Installed {result.name} ({_format_bytes(result.size_bytes)})")
        return 0

    print("Choose an action: list, recommend, pull MODEL, or cloud")
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.provider is None:
        parser.print_help()
        raise SystemExit(0)
    if args.action is None:
        parser.print_help()
        raise SystemExit(0)
    return args


async def async_main(argv: Sequence[str] | None = None) -> int:
    """Async-safe model command entrypoint used by the top-level CLI."""

    return await _run_ollama(_parse_args(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Discover and manage local Ollama models from a synchronous caller."""

    return asyncio.run(async_main(argv))


__all__ = ["async_main", "main"]
