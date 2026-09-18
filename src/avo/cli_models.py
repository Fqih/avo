"""Command-line entrypoint for provider model discovery and management."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

from avo.hardware import detect_hardware
from avo.model_catalog import recommend_ollama_models
from avo.ollama_manager import (
    OllamaCloudConfig,
    OllamaCloudManager,
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
            "  avo models ollama cloud list\n"
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
        default=None,
        help="Ollama endpoint (local default or https://ollama.com for Cloud).",
    )
    ollama_commands = ollama.add_subparsers(dest="action")
    ollama_commands.add_parser("list", help="List models installed or available locally.")
    ollama_commands.add_parser("recommend", help="Recommend local models from hardware.")
    pull = ollama_commands.add_parser("pull", help="Confirm and download a local model.")
    pull.add_argument("model", help="Ollama model name to download.")
    cloud = ollama_commands.add_parser("cloud", help="Inspect remote Ollama Cloud.")
    cloud_commands = cloud.add_subparsers(dest="cloud_action")
    cloud_commands.add_parser("list", help="List models exposed by the Cloud account.")
    cloud_commands.add_parser("health", help="Check Cloud availability.")
    cloud_commands.add_parser("usage", help="Show Cloud account usage when available.")
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
        if not args.cloud_action:
            print("Ollama Cloud (remote)")
            print("  Login: avo login ollama-cloud --key-stdin")
            print("  Cloud models run remotely and are not downloaded by Avo.")
            print("  Inspect: avo models ollama cloud list|health|usage")
            return 0

        api_key = (
            os.environ.get("AVO_OLLAMA_API_KEY", "").strip()
            or os.environ.get("AVO_OLLAMA_CLOUD_API_KEY", "").strip()
        )
        if not api_key:
            try:
                from avo.oauth.store import get_credential

                credential = get_credential("ollama-cloud") or get_credential("ollama")
                if credential is not None:
                    api_key = credential.secret()
            except Exception:
                api_key = ""
        if not api_key:
            print(
                "Ollama Cloud credentials are missing. Run "
                "`avo login ollama-cloud --key-stdin` or set AVO_OLLAMA_API_KEY."
            )
            return 1

        config = OllamaCloudConfig(
            model=os.environ.get("AVO_OLLAMA_MODEL", "").strip() or "qwen3-coder:480b-cloud",
            api_key=api_key,
            base_url=args.base_url or "https://ollama.com",
        )
        manager = OllamaCloudManager(config)
        if args.cloud_action == "health":
            health = await manager.check_health()
            print(f"Ollama Cloud ({config.base_url}) — {health.version or 'ready'}")
            if not health.available:
                print(f"  unavailable: {health.detail}")
                return 1
            return 0
        if args.cloud_action == "list":
            models = await manager.list_models()
            print(f"Ollama Cloud ({config.base_url})")
            if not models:
                print("  No models were returned by the account.")
            for model in models:
                print(f"  • {model.name}")
            return 0
        if args.cloud_action == "usage":
            usage = await manager.usage()
            print(f"Ollama Cloud usage ({config.base_url})")
            if usage is None:
                print("  Usage endpoint is unavailable for this account.")
            else:
                print(f"  remaining tokens: {usage.remaining_tokens or 'unknown'}")
                print(f"  limit tokens: {usage.limit_tokens or 'unknown'}")
                if usage.reset_at:
                    print(f"  resets: {usage.reset_at}")
            return 0
        print("Choose a Cloud action: list, health, or usage")
        return 0

    base_url = args.base_url or "http://localhost:11434"
    local_manager = OllamaManager(base_url)
    health = await local_manager.check_health()
    if not health.available:
        print(f"Ollama Local is unavailable at {base_url}: {health.detail}")
        return 1

    models = await local_manager.list_models()
    if args.action == "list":
        print(f"Ollama Local ({base_url}) — daemon {health.version or 'ready'}")
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
            result = await local_manager.pull(args.model, confirm=confirm, output=_print_progress)
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
