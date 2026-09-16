"""Token refresh lifecycle, rotation persistence, and deduplicated renewal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from avo.auth import AuthError
from avo.oauth import flows
from avo.oauth.registry import OAUTH, OAuthEntry
from avo.oauth.store import Credential, get_credential, store_credential

_LOCKS: dict[str, asyncio.Lock] = {}


def _get_lock(provider: str) -> asyncio.Lock:
    key = provider.lower()
    if key not in _LOCKS:
        _LOCKS[key] = asyncio.Lock()
    return _LOCKS[key]


def needs_refresh(cred: Credential, *, now: datetime, entry: OAuthEntry) -> bool:
    """Return True if an OAuth credential is close to expiry or already expired."""

    if cred.kind != "oauth":
        return False
    if cred.expires_at is None:
        return False
    lead = timedelta(seconds=entry.refresh_lead_seconds)
    return now >= (cred.expires_at - lead)


async def refresh_credential(
    entry: OAuthEntry,
    cred: Credential,
    *,
    now: datetime | None = None,
    token_requester: Callable[[OAuthEntry, Mapping[str, str]], Any] = flows.request_token,
) -> Credential:
    """Execute a single refresh token exchange and return the renewed Credential."""

    if not cred.refresh_token:
        raise AuthError(f"No refresh token available for {cred.provider}")

    form: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": cred.refresh_token,
        "client_id": entry.client_id,
    }
    if entry.client_secret:
        form["client_secret"] = entry.client_secret

    try:
        import inspect

        call_target = token_requester
        if inspect.iscoroutinefunction(call_target) or inspect.iscoroutinefunction(
            type(call_target).__call__
        ):
            raw = await token_requester(entry, form)
        else:
            res = await asyncio.to_thread(token_requester, entry, form)
            raw = await res if inspect.isawaitable(res) else res
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError(f"Refresh token exchange failed: {exc}") from exc

    refreshed = flows.map_tokens(raw, entry, now=now)
    updates: dict[str, Any] = {}
    if not refreshed.account and cred.account:
        updates["account"] = cred.account
    if not refreshed.refresh_token and cred.refresh_token:
        updates["refresh_token"] = cred.refresh_token
    if updates:
        refreshed = refreshed.model_copy(update=updates)

    return refreshed


async def ensure_fresh(
    provider: str,
    *,
    now: datetime | None = None,
    token_requester: Callable[[OAuthEntry, Mapping[str, str]], Any] = flows.request_token,
) -> Credential:
    """Ensure the credential for ``provider`` is valid, refreshing it if needed.

    Concurrent calls for the same provider are deduplicated using an asyncio.Lock.
    """

    current_time = now if now is not None else datetime.now(UTC)
    key = provider.lower()
    lock = _get_lock(key)

    async with lock:
        cred = get_credential(key)
        if cred is None:
            raise AuthError(f"No stored credential found for {provider}")
        if cred.kind != "oauth":
            return cred

        entry = OAUTH.get(key)
        if entry is None:
            return cred

        if entry.max_age_seconds is not None and cred.obtained_at is not None:
            age = (current_time - cred.obtained_at).total_seconds()
            if age >= entry.max_age_seconds:
                hint = f"run avo login {provider}"
                raise AuthError(
                    f"{provider} session expired past max age — re-login required: {hint}"
                )

        if not needs_refresh(cred, now=current_time, entry=entry):
            return cred

        last_err: Exception | None = None
        for _ in range(2):
            try:
                renewed = await refresh_credential(
                    entry,
                    cred,
                    now=current_time,
                    token_requester=token_requester,
                )
                store_credential(renewed)
                return renewed
            except Exception as exc:
                last_err = exc

        raise AuthError(
            f"{provider} session expired — reauth required: run avo login {provider}"
        ) from last_err
