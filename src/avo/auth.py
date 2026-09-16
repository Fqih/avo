"""OAuth authentication, subscription tokens, and secure token storage for Avo.

Supports:
- OAuth 2.0 Device Authorization Flow (RFC 8628) for GitHub and headless environments.
- OpenRouter OAuth (PKCE / localhost callback) for one-click browser login.
- Subscription OAuth credentials (Claude, ChatGPT/Codex, Gemini) behind explicit gate.
- Secure local token storage in ``~/.config/avo/auth.json`` with strict 0600 permissions.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import sys
import urllib.parse
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from avo.exceptions import AvoError

_LOG = logging.getLogger("avo.auth")


class AuthError(AvoError):
    """Raised when an authentication flow fails."""


def default_auth_dir() -> Path:
    """Return the base config directory for Avo credentials (~/.config/avo)."""

    custom = os.environ.get("AVO_CONFIG_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()

    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser().resolve() / "avo"

    return Path.home() / ".config" / "avo"


def auth_file_path() -> Path:
    """Return the absolute path to auth.json (~/.config/avo/auth.json)."""

    return default_auth_dir() / "auth.json"


def load_all_tokens() -> dict[str, str]:
    """Read all stored provider tokens from auth.json."""

    from avo.oauth.store import load_all_credentials

    out: dict[str, str] = {}
    for k, c in load_all_credentials().items():
        if c.access_token:
            out[k] = c.access_token
    return out


def get_stored_token(provider: str) -> str | None:
    """Return stored API key/token for ``provider`` if available."""

    from avo.oauth.store import get_credential

    cred = get_credential(provider)
    return cred.access_token if cred is not None and cred.access_token else None


def store_token(provider: str, token: str) -> Path:
    """Save an API key/token for ``provider`` with chmod 0600 permissions."""

    from avo.oauth.store import Credential, store_credential

    cred = Credential(
        provider=provider.lower(),
        kind="api_key",
        access_token=token.strip(),
        obtained_at=datetime.now(UTC),
    )
    return store_credential(cred)


def remove_stored_token(provider: str) -> bool:
    """Remove a stored token for ``provider``. Return True if a token was deleted."""

    from avo.oauth.store import remove_credential

    return remove_credential(provider)


def generate_pkce_pair() -> tuple[str, str]:
    """Generate (verifier, challenge) for PKCE OAuth using S256."""

    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def run_localhost_callback_server(
    port: int,
    callback_path: str,
    timeout_seconds: float = 120.0,
    expected_state: str | None = None,
) -> dict[str, str]:
    """Run an ephemeral asyncio HTTP server to capture OAuth callback parameters."""

    future_result: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()

    async def _handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return

            request_line = line.decode("utf-8", errors="replace").strip()
            parts = request_line.split(" ")
            if len(parts) >= 2 and parts[0] == "GET":
                parsed = urllib.parse.urlparse(parts[1])
                if parsed.path == callback_path:
                    query_params = urllib.parse.parse_qs(parsed.query)
                    flattened = {k: v[0] for k, v in query_params.items() if v}
                    if expected_state is not None and flattened.get("state") != expected_state:
                        # State mismatch: ignore non-matching callback request
                        pass
                    elif not future_result.done():
                        future_result.set_result(flattened)

                    body = (
                        b"<!DOCTYPE html><html><head><title>Avo Authorization</title></head>"
                        b"<body style='font-family:sans-serif;text-align:center;padding:50px;'>"
                        b"<h2>&#x2705; Authorization Successful!</h2>"
                        b"<p>You can close this tab and return to your terminal.</p>"
                        b"</body></html>"
                    )
                    header = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: text/html; charset=utf-8\r\n"
                        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                    writer.write(header + body)
                    await writer.drain()
                    return

            not_found = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            writer.write(not_found)
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(_handle_client, "127.0.0.1", port)
    try:
        return await asyncio.wait_for(future_result, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise AuthError(f"OAuth login timed out after {timeout_seconds}s") from exc
    finally:
        server.close()
        await server.wait_closed()


async def login_openrouter(
    *,
    port: int = 43110,
    timeout_seconds: float = 120.0,
    open_browser: bool = True,
    output_writer: Callable[[str], object] = sys.stdout.write,
) -> str:
    """Authorize OpenRouter in the browser and store the credential."""

    callback_path = "/auth/callback"
    callback_url = f"http://localhost:{port}{callback_path}"
    auth_url = (
        f"https://openrouter.ai/auth?callback_url={urllib.parse.quote(callback_url, safe='')}"
    )

    output_writer(
        f"\nOpenRouter OAuth Authentication\n"
        f"1. Open this URL in your browser:\n   {auth_url}\n"
        f"2. Authorize Avo\n"
        f"Waiting for authorization callback on localhost:{port}...\n"
    )

    if open_browser:
        import webbrowser

        with contextlib.suppress(Exception):
            webbrowser.open(auth_url)

    params = await run_localhost_callback_server(
        port=port,
        callback_path=callback_path,
        timeout_seconds=timeout_seconds,
    )

    api_key = params.get("code") or params.get("api_key") or params.get("key")
    if not api_key:
        raise AuthError(f"OpenRouter did not return an API key in callback: {params}")

    store_token("openrouter", api_key)
    output_writer("✓ OpenRouter token successfully stored in ~/.config/avo/auth.json\n")
    return api_key


def _sync_request_json(
    url: str, form_data: dict[str, str], timeout: float = 15.0
) -> dict[str, object]:
    import urllib.request

    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form_data).encode("utf-8"),
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        parsed = json.loads(resp.read().decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
    return {}


async def login_github_device(
    *,
    client_id: str | None = None,
    scope: str = "read:user",
    timeout_seconds: float = 300.0,
    poll_interval: float = 5.0,
    output_writer: Callable[[str], object] = sys.stdout.write,
) -> str:
    """Authenticate via GitHub OAuth 2.0 Device Flow (RFC 8628)."""

    resolved_client_id = (
        client_id or os.environ.get("AVO_GITHUB_CLIENT_ID", "").strip() or "Iv1.8a2e1d713c77d018"
    )

    try:
        data = await asyncio.to_thread(
            _sync_request_json,
            "https://github.com/login/device/code",
            {"client_id": resolved_client_id, "scope": scope},
            15.0,
        )
    except Exception as exc:
        raise AuthError(f"Failed to initiate GitHub device authorization: {exc}") from exc

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = str(data.get("verification_uri", "https://github.com/login/device"))
    interval = float(str(data.get("interval", poll_interval)))

    if not device_code or not user_code:
        raise AuthError(f"GitHub returned invalid device code response: {data}")

    output_writer(
        f"\nGitHub Device Authorization\n"
        f"1. Open URL: {verification_uri}\n"
        f"2. Enter code: {user_code}\n"
        f"Waiting for authorization...\n"
    )

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(interval)
        try:
            poll_data = await asyncio.to_thread(
                _sync_request_json,
                "https://github.com/login/oauth/access_token",
                {
                    "client_id": resolved_client_id,
                    "device_code": str(device_code),
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                15.0,
            )
        except Exception:
            continue

        if "access_token" in poll_data:
            token = str(poll_data["access_token"])
            store_token("github", token)
            output_writer("✓ GitHub token successfully stored in ~/.config/avo/auth.json\n")
            return token

        err = poll_data.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5.0
            continue
        if err:
            err_desc = poll_data.get("error_description", "")
            raise AuthError(f"GitHub authorization failed: {err} ({err_desc})")

    raise AuthError("GitHub authorization timed out")


def main_login(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for `avo login`."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="avo login",
        description="Manage OAuth and API key credentials for Avo.",
    )
    parser.add_argument(
        "provider",
        nargs="?",
        default="openrouter",
        help=(
            "Provider to authenticate with (claude, codex, chatgpt, "
            "gemini, github, openrouter; default: openrouter)."
        ),
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show currently authenticated providers.",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Remove stored credentials for provider.",
    )
    parser.add_argument(
        "--key-stdin",
        action="store_true",
        help="Read raw API key from standard input and store without OAuth.",
    )
    parser.add_argument(
        "--no-browser",
        "--headless",
        dest="no_browser",
        action="store_true",
        help="Do not attempt to open a browser automatically.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Automatically confirm prompts (e.g. importing existing CLI credentials).",
    )
    args = parser.parse_args(argv)

    if args.status:
        from avo.oauth.store import load_all_credentials

        creds = load_all_credentials()
        if not creds:
            print("No providers currently authenticated in ~/.config/avo/auth.json.")
            return 0
        print("Authenticated providers:")
        for p in sorted(creds.keys()):
            cred = creds[p]
            if cred.kind == "oauth":
                exp_str = (
                    f", expires {cred.expires_at.astimezone().strftime('%H:%M')}"
                    if cred.expires_at
                    else ""
                )
                account_str = f" {cred.account}" if cred.account else ""
                print(f"  • {p}: oauth ✓{account_str}{exp_str}")
            else:
                val = cred.access_token or ""
                masked = val[:4] + "..." + val[-4:] if len(val) > 8 else "****"
                print(f"  • {p}: {masked}")
        return 0

    if args.logout:
        removed = remove_stored_token(args.provider)
        if removed:
            print(f"Logged out of {args.provider}.")
        else:
            print(f"No stored credentials found for {args.provider}.")
        return 0

    raw_provider = args.provider.lower()
    store_key = "codex" if raw_provider == "chatgpt" else raw_provider

    if args.key_stdin:
        key = sys.stdin.readline().strip()
        if not key:
            print("Error: Empty API key provided via --key-stdin", file=sys.stderr)
            return 1
        target = store_token(store_key, key)
        print(f"✓ Stored API key for {store_key} in {target}")
        return 0

    from avo.oauth.imports import find_importable

    importable = find_importable(store_key)
    if importable is not None:
        should_import = args.yes
        if not should_import and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            acc = importable.account or "active"
            prompt_msg = f"Found existing credentials for {store_key} ({acc}). Import? [Y/n]: "
            ans = input(prompt_msg).strip().lower()
            should_import = ans in {"", "y", "yes"}
        if should_import:
            from avo.oauth.store import store_credential

            target = store_credential(importable)
            acc = importable.account or "active"
            print(f"✓ Imported {store_key} credentials ({acc}) into {target}")
            return 0

    from avo.oauth.registry import OAUTH

    open_browser = not args.no_browser

    if store_key in OAUTH:
        from avo.oauth.flows import run_pkce_login
        from avo.oauth.store import store_credential

        entry = OAUTH[store_key]
        try:
            cred = asyncio.run(run_pkce_login(entry, open_browser=open_browser))
            target = store_credential(cred)
            exp_str = (
                f", expires {cred.expires_at.astimezone().strftime('%H:%M')}"
                if cred.expires_at
                else ""
            )
            print(f"✓ Logged in: {cred.account or 'authenticated'} ({store_key}{exp_str})")
            print(f"Stored in {target}")
            return 0
        except AuthError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif store_key == "openrouter":
        try:
            asyncio.run(login_openrouter(open_browser=open_browser))
            return 0
        except AuthError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif store_key == "github":
        try:
            asyncio.run(login_github_device())
            return 0
        except AuthError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        supported = sorted(["claude", "codex (chatgpt)", "gemini", "github", "openrouter"])
        print(
            f"Unknown login provider {args.provider!r}. Supported: {', '.join(supported)}. "
            "(Or use --key-stdin to store an API key for any provider.)",
            file=sys.stderr,
        )
        return 2
