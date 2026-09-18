"""`avo combo` — create, list, show, and remove combo routing profiles."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from avo.combo.models import ComboProfile, ComboTier
from avo.combo.store import (
    BUILTIN_COMBOS,
    delete_combo,
    get_combo,
    load_combos,
    save_combo,
)
from avo.config import supported_providers
from avo.exceptions import AvoError
from avo.oauth.store import get_credential


class ComboCliError(AvoError):
    """User-facing error in `avo combo`."""


_VENDOR_LOGIN_TARGETS: dict[str, tuple[tuple[str, ...], str]] = {
    "claude": (("claude", "anthropic"), "claude"),
    "anthropic": (("claude", "anthropic"), "claude"),
    "codex": (("codex", "openai"), "codex"),
    "gemini": (("gemini", "gemini-api"), "gemini"),
    "gemini-cli": (("gemini", "gemini-api"), "gemini"),
    "openrouter": (("openrouter",), "openrouter"),
}


def _parse_tier_spec(spec: str, index: int) -> ComboTier:
    parts = spec.split(":")
    if len(parts) == 2:
        provider, model = parts[0].strip(), parts[1].strip()
        name = f"tier_{index}"
        timeout = 60.0
        cooldown = 60.0
    elif len(parts) == 3:
        name, provider, model = parts[0].strip(), parts[1].strip(), parts[2].strip()
        timeout = 60.0
        cooldown = 60.0
    elif len(parts) == 4:
        name, provider, model, timeout_str = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
        )
        try:
            timeout = float(timeout_str)
        except ValueError as exc:
            raise ComboCliError(f"invalid timeout {timeout_str!r} in tier {spec!r}") from exc
        cooldown = 60.0
    elif len(parts) == 5:
        name, provider, model, timeout_str, cooldown_str = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
            parts[4].strip(),
        )
        try:
            timeout = float(timeout_str)
            cooldown = float(cooldown_str)
        except ValueError as exc:
            raise ComboCliError(f"invalid timeout/cooldown in tier {spec!r}") from exc
    else:
        raise ComboCliError(
            f"invalid tier spec {spec!r}. Format: [name:]provider:model[:timeout[:cooldown]]"
        )

    if not name:
        raise ComboCliError(f"tier name cannot be blank in {spec!r}")
    if not provider:
        raise ComboCliError(f"provider cannot be blank in {spec!r}")
    if not model:
        raise ComboCliError(f"model cannot be blank in {spec!r}")
    if provider == "combo":
        raise ComboCliError("nested combo tiers are not supported")
    if provider == "claude":
        provider = "anthropic"

    valid_providers = set(supported_providers()) - {"combo"}
    if provider not in valid_providers:
        allowed = ", ".join(sorted(valid_providers))
        raise ComboCliError(f"unsupported provider {provider!r}; must be one of: {allowed}")

    if timeout <= 0:
        raise ComboCliError(f"timeout must be positive; got {timeout}")
    if cooldown < 0:
        raise ComboCliError(f"cooldown cannot be negative; got {cooldown}")

    return ComboTier(
        name=name,
        provider=provider,
        model=model,
        timeout_seconds=timeout,
        cooldown_seconds=cooldown,
    )


def _format_tier_summary(tier: ComboTier) -> str:
    display_name = "account" if tier.name == "subscription" else tier.name
    return f"{display_name} ({tier.provider}/{tier.model})"


def _cmd_list(as_json: bool) -> int:
    combos = load_combos()
    if as_json:
        data = {name: profile.model_dump(mode="json") for name, profile in combos.items()}
        print(json.dumps(data, indent=2))
        return 0

    if not combos:
        print("No combo profiles found.")
        return 0

    print(f"{'NAME':16}  {'TIERS':45}  DESCRIPTION")
    for name, profile in sorted(combos.items()):
        tiers_str = " -> ".join(_format_tier_summary(t) for t in profile.tiers)
        desc = profile.description or ""
        print(f"{name:16}  {tiers_str:45}  {desc}")
    return 0


def _cmd_show(name: str, as_json: bool) -> int:
    profile = get_combo(name)
    if profile is None:
        print(f"avo combo: profile {name!r} not found", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(profile.model_dump(mode="json"), indent=2))
        return 0

    is_builtin = name in BUILTIN_COMBOS
    print(f"Profile     : {profile.name}{' (built-in preset)' if is_builtin else ''}")
    if profile.description:
        print(f"Description : {profile.description}")
    print("Tiers (priority order):")
    for idx, tier in enumerate(profile.tiers, start=1):
        display_name = "account" if tier.name == "subscription" else tier.name
        print(
            f"  {idx}. {display_name:15} provider={tier.provider:10} "
            f"model={tier.model:20} timeout={tier.timeout_seconds}s "
            f"cooldown={tier.cooldown_seconds}s"
        )
    return 0


def _cmd_new(name: str, raw_tiers: list[str], description: str) -> int:
    tiers: list[ComboTier] = []
    for idx, spec in enumerate(raw_tiers, start=1):
        tiers.append(_parse_tier_spec(spec, idx))

    profile = ComboProfile(
        name=name,
        description=description,
        tiers=tiers,
    )
    save_combo(profile)
    print(f"Saved combo profile {name!r} with {len(tiers)} tier(s).")
    return 0


def _cmd_rm(name: str) -> int:
    if name in BUILTIN_COMBOS:
        combos_in_catalog = delete_combo(name)
        if not combos_in_catalog:
            print(f"avo combo: cannot delete built-in combo profile {name!r}", file=sys.stderr)
            return 1
        print(f"Removed custom override for built-in combo profile {name!r}.")
        return 0

    removed = delete_combo(name)
    if not removed:
        print(f"avo combo: profile {name!r} not found", file=sys.stderr)
        return 1
    print(f"Removed combo profile {name!r}.")
    return 0


def _cmd_auth(name: str) -> int:
    """Authenticate every missing vendor route in a combo profile once."""

    profile = get_combo(name)
    if profile is None:
        print(f"avo combo: profile {name!r} not found", file=sys.stderr)
        return 1

    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for tier in profile.tiers:
        provider = tier.provider.lower()
        if provider in seen:
            continue
        seen.add(provider)
        if provider == "ollama-cloud":
            if get_credential("ollama") is not None:
                print("✓ ollama-cloud: credential already stored")
            else:
                print(
                    "• ollama-cloud: add the official Ollama API/device key with "
                    "`avo login ollama-cloud --key-stdin`"
                )
            continue
        login_spec = _VENDOR_LOGIN_TARGETS.get(provider)
        if login_spec is None:
            if provider == "ollama":
                print(f"✓ {provider}: local provider needs no login")
            else:
                print(f"• {provider}: configure an API key with `avo login {provider} --key-stdin`")
            continue

        credential_keys, login_target = login_spec
        if any(get_credential(key) is not None for key in credential_keys):
            print(f"✓ {provider}: credential already stored")
            continue
        targets.append((provider, login_target))

    if not targets:
        print(f"All providers in combo {name!r} are already ready.")
        return 0

    print(f"Opening {len(targets)} vendor login(s) for combo {name!r}...")
    from avo.auth import login_provider_in_browser

    failures = 0
    for provider, login_target in targets:
        print(f"→ Login {provider} via {login_target}")
        if not login_provider_in_browser(login_target):
            failures += 1
            print(f"✗ {provider}: login failed; continuing with remaining vendors")

    if failures:
        print(f"{failures} vendor login(s) failed.", file=sys.stderr)
        return 1
    print("✓ All requested vendor logins completed.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo combo",
        description="Manage multi-tier combo routing profiles.",
    )
    subparsers = parser.add_subparsers(dest="combo_command", required=False)

    list_parser = subparsers.add_parser(
        "list", aliases=["ls"], help="List all available combo profiles."
    )
    list_parser.add_argument("--json", action="store_true", help="Output profiles as JSON.")

    show_parser = subparsers.add_parser("show", help="Inspect a specific combo profile.")
    show_parser.add_argument("name", help="Name of the combo profile.")
    show_parser.add_argument("--json", action="store_true", help="Output profile as JSON.")

    new_parser = subparsers.add_parser(
        "new", aliases=["add", "create"], help="Create or update a combo profile."
    )
    new_parser.add_argument("name", help="Name of the combo profile.")
    new_parser.add_argument(
        "--tier",
        dest="tiers",
        action="append",
        required=True,
        help=(
            "Tier definition: [name:]provider:model[:timeout[:cooldown]] "
            "(repeatable for ordered tiers)."
        ),
    )
    new_parser.add_argument(
        "--description",
        "-d",
        default="",
        help="Optional human-readable description.",
    )

    rm_parser = subparsers.add_parser(
        "rm", aliases=["delete", "remove"], help="Remove a combo profile."
    )
    rm_parser.add_argument("name", help="Name of the combo profile to remove.")

    auth_parser = subparsers.add_parser(
        "auth",
        help="Open login flows for every missing vendor used by a combo profile.",
    )
    auth_parser.add_argument("name", help="Name of the combo profile to authenticate.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    if not args.combo_command:
        return _cmd_list(as_json=False)

    try:
        if args.combo_command in ("list", "ls"):
            return _cmd_list(as_json=args.json)
        if args.combo_command == "show":
            return _cmd_show(args.name, as_json=args.json)
        if args.combo_command in ("new", "add", "create"):
            return _cmd_new(args.name, args.tiers, args.description)
        if args.combo_command in ("rm", "delete", "remove"):
            return _cmd_rm(args.name)
        if args.combo_command == "auth":
            return _cmd_auth(args.name)
        raise ComboCliError(f"unknown combo subcommand: {args.combo_command}")
    except ComboCliError as exc:
        print(f"avo combo: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
