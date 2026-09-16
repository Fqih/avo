"""Generic OAuth 2.0 PKCE authorization-code flow and HTTP token exchange."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from avo.auth import AuthError, generate_pkce_pair, run_localhost_callback_server
from avo.oauth.registry import OAuthEntry
from avo.oauth.store import Credential


def build_authorize_url(
    entry: OAuthEntry,
    redirect_uri: str,
    state: str,
    challenge: str,
) -> str:
    """Construct the full OAuth authorization URL with PKCE parameters."""

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": entry.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(entry.scopes),
        "code_challenge": challenge,
        "code_challenge_method": entry.code_challenge_method,
        "state": state,
    }
    params.update(entry.extra_auth_params)
    query = urllib.parse.urlencode(params)
    return f"{entry.authorize_url}?{query}"


def post_json(
    url: str,
    body: Mapping[str, object],
    headers: Mapping[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Execute synchronous POST with JSON body and return parsed response."""

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    if not (url.startswith("https://") or url.startswith("http://")):
        raise AuthError(f"Unsupported URL scheme: {url}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            content = resp.read().decode("utf-8", errors="replace")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise AuthError(f"HTTP {exc.code} from {url}: {err_body}") from exc
    except Exception as exc:
        raise AuthError(f"Request to {url} failed: {exc}") from exc


def post_form(
    url: str,
    form: Mapping[str, str],
    headers: Mapping[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Execute synchronous POST with URL-encoded form and return parsed response."""

    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    if not (url.startswith("https://") or url.startswith("http://")):
        raise AuthError(f"Unsupported URL scheme: {url}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            content = resp.read().decode("utf-8", errors="replace")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise AuthError(f"HTTP {exc.code} from {url}: {err_body}") from exc
    except Exception as exc:
        raise AuthError(f"Request to {url} failed: {exc}") from exc


def request_token(entry: OAuthEntry, form: Mapping[str, str]) -> dict[str, Any]:
    """Dispatch token exchange according to the entry's exchange encoding."""

    if entry.exchange_encoding == "json":
        return post_json(entry.token_url, dict(form), headers=entry.identity_headers)
    return post_form(entry.token_url, form, headers=entry.identity_headers)


def map_tokens(
    raw: dict[str, Any],
    entry: OAuthEntry,
    *,
    now: datetime | None = None,
) -> Credential:
    """Map raw token endpoint JSON response into a typed Credential record."""

    expires_in = raw.get("expires_in")
    current_time = now if now is not None else datetime.now(UTC)
    account: str | None = None
    acc = raw.get("account")
    if isinstance(acc, dict):
        account = acc.get("email_address") or acc.get("email")
    elif isinstance(acc, str):
        account = acc
    if not account and "email" in raw and isinstance(raw["email"], str):
        account = raw["email"]

    expires_at = (
        current_time + timedelta(seconds=float(expires_in)) if expires_in is not None else None
    )
    return Credential(
        provider=entry.provider,
        kind="oauth",
        access_token=raw.get("access_token"),
        refresh_token=raw.get("refresh_token"),
        expires_at=expires_at,
        obtained_at=current_time,
        account=account,
        scope=raw.get("scope"),
        subscription=True,
    )


async def run_pkce_login(
    entry: OAuthEntry,
    *,
    callback_runner: Callable[..., Any] = run_localhost_callback_server,
    token_requester: Callable[[OAuthEntry, Mapping[str, str]], Any] = request_token,
    open_browser: bool = True,
    timeout_seconds: float = 300.0,
    output_writer: Callable[[str], object] = sys.stdout.write,
) -> Credential:
    """Execute end-to-end browser PKCE login and return resulting Credential."""

    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{entry.callback_port}{entry.callback_path}"
    auth_url = build_authorize_url(entry, redirect_uri, state, challenge)

    output_writer(
        f"\n{entry.display_name} OAuth Authentication\n"
        f"1. Open this URL in your browser:\n   {auth_url}\n"
        f"Waiting for authorization callback on localhost:{entry.callback_port}...\n"
    )

    if open_browser:
        import webbrowser

        with contextlib.suppress(Exception):
            webbrowser.open(auth_url)

    params = await callback_runner(
        port=entry.callback_port,
        callback_path=entry.callback_path,
        timeout_seconds=timeout_seconds,
        expected_state=state,
    )

    if params.get("state") != state:
        raise AuthError(f"OAuth state mismatch: expected {state}, got {params.get('state')}")

    code_raw = params.get("code")
    if not code_raw:
        raise AuthError(f"OAuth callback missing authorization code: {params}")

    code = code_raw.split("#")[0]
    exchange_data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": entry.client_id,
        "code_verifier": verifier,
    }
    if entry.client_secret:
        exchange_data["client_secret"] = entry.client_secret
    if "state" in params:
        exchange_data["state"] = params["state"]

    try:
        import inspect

        call_target = token_requester
        if inspect.iscoroutinefunction(call_target) or inspect.iscoroutinefunction(
            type(call_target).__call__
        ):
            raw = await token_requester(entry, exchange_data)
        else:
            res = await asyncio.to_thread(token_requester, entry, exchange_data)
            raw = await res if inspect.isawaitable(res) else res
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError(f"Token exchange failed: {exc}") from exc

    return map_tokens(raw, entry)
