"""OAuth authentication and secure token storage for Avo.

Supports:
- OAuth 2.0 Device Authorization Flow (RFC 8628) for GitHub and headless environments.
- OpenRouter OAuth (PKCE / localhost callback) for one-click browser login.
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

    path = auth_file_path()
    if not path.is_file():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception as exc:
        _LOG.warning("could not read %s: %s", path, exc)
    return {}


def get_stored_token(provider: str) -> str | None:
    """Return stored API key/token for ``provider`` if available."""

    tokens = load_all_tokens()
    return tokens.get(provider.lower())


def store_token(provider: str, token: str) -> Path:
    """Save an API key/token for ``provider`` with chmod 0600 permissions."""

    target = auth_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    current = load_all_tokens()
    current[provider.lower()] = token.strip()

    # Write securely with 0600 permissions
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    raw_bytes = json.dumps(current, indent=2).encode("utf-8")
    fd = os.open(target, flags, 0o600)
    try:
        with open(fd, "wb", closefd=False) as fh:
            fh.write(raw_bytes)
    finally:
        os.close(fd)

    os.chmod(target, 0o600)
    return target


def remove_stored_token(provider: str) -> bool:
    """Remove a stored token for ``provider``. Return True if a token was deleted."""

    current = load_all_tokens()
    key = provider.lower()
    if key not in current:
        return False

    del current[key]
    target = auth_file_path()
    if not current:
        if target.is_file():
            target.unlink()
        return True

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    raw_bytes = json.dumps(current, indent=2).encode("utf-8")
    fd = os.open(target, flags, 0o600)
    try:
        with open(fd, "wb", closefd=False) as fh:
            fh.write(raw_bytes)
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    return True


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
                    if not future_result.done():
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
        description="Manage OAuth credentials for Avo.",
    )
    parser.add_argument(
        "provider",
        nargs="?",
        default="openrouter",
        help="Provider to authenticate with (openrouter, github; default: openrouter).",
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
    args = parser.parse_args(argv)

    if args.status:
        tokens = load_all_tokens()
        if not tokens:
            print("No providers currently authenticated in ~/.config/avo/auth.json.")
            return 0
        print("Authenticated providers:")
        for p in sorted(tokens.keys()):
            val = tokens[p]
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

    provider = args.provider.lower()
    if provider == "openrouter":
        try:
            asyncio.run(login_openrouter())
            return 0
        except AuthError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif provider == "github":
        try:
            asyncio.run(login_github_device())
            return 0
        except AuthError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        print(
            f"Unknown login provider {args.provider!r}. Supported: openrouter, github.",
            file=sys.stderr,
        )
        return 2
