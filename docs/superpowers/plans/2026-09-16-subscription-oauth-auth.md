# S1 — Subscription OAuth Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `avo login` command that stores refreshable OAuth credentials for Claude / ChatGPT (Codex) / Gemini subscriptions plus universal API-key login, wired into providers behind an explicit subscription opt-in gate.

**Architecture:** New `src/avo/oauth/` package (static registry, generic PKCE flows, refresh lifecycle, imports from official CLIs, credential resolution) on top of the existing `src/avo/auth.py` primitives (PKCE pair, localhost callback server, 0600 store). Providers gain an `auth_mode`/token-provider seam; `build_provider_from_env` consults the store when env vars are absent.

**Tech Stack:** Python 3.11+, Pydantic v2 only (stdlib `asyncio`/`urllib`/`secrets`/`hashlib` for HTTP — same pattern as existing `login_github_device`), pytest with injected fake transports.

**Spec:** `docs/superpowers/specs/2026-09-16-subscription-oauth-auth-design.md` (read it first — §5 holds the constants table this plan encodes).

## Global Constraints

- Core `dependencies` stay `pydantic>=2.8,<3` only — no requests/httpx/authlib in new code. (Providers may keep using their existing optional httpx; new OAuth code uses `urllib` via `asyncio.to_thread`, like `auth.py:241` `_sync_request_json`.)
- CI and the test suite NEVER hit a vendor network: every remote call goes through an injectable function/transport.
- `auth.json` file mode 0600; directory created 0700; atomic write pattern from `auth.py:76-96` is the only writer.
- Secret fields use `Field(repr=False)`; tokens must never appear in logs, `repr`, `avo doctor`, or event payloads.
- All env reads via `Mapping[str, str]` parameters defaulting to `os.environ` (testability, matches `config.py:173`).
- Commits: conventional, imperative, ≤72 chars, single author `Fqih <mhmdfkih21@gmail.com>`, no trailers. Gates before each commit: `ruff check . && ruff format --check . && mypy src/avo && pytest`.
- Backward compatibility: `load_all_tokens()`, `get_stored_token()`, `store_token()`, `remove_stored_token()`, `main_login()` keep their current contracts (spec §14).
- Python floor 3.11; mypy strict must stay clean for `src/avo`.

## File structure

```
src/avo/oauth/
    __init__.py      # public surface (Task 1 onward)
    store.py         # Credential model + v2 read/write (Task 1)
    registry.py      # frozen per-provider OAuth constants (Task 2)
    gate.py          # AVO_ALLOW_SUBSCRIPTION policy (Task 2)
    flows.py         # generic PKCE authorize+exchange (Task 3)
    refresh.py       # proactive/reactive refresh, rotation, dedupe (Task 4)
    resolution.py    # env > store credential resolution (Task 4)
    imports.py       # ~/.claude and ~/.codex reuse (Task 6)
src/avo/auth.py            # modify: v1-compat shim + CLI (Tasks 1, 5)
src/avo/config.py          # modify: build_provider_from_env consults store (Task 10)
src/avo/providers/anthropic.py   # modify: oauth auth_mode (Task 7)
src/avo/providers/codex.py       # create (Task 8)
src/avo/providers/gemini_cli.py  # create (Task 9)
src/avo/chat_setup.py      # modify: offer stored creds + persist (Task 10)
src/avo/doctor.py          # modify: credential status rows (Task 10)
docs/guides/subscription-auth.md   # create (Task 10)
tests/test_oauth_store.py | test_oauth_registry.py | test_oauth_gate.py |
tests/test_oauth_flows.py | test_oauth_refresh.py | test_oauth_resolution.py |
tests/test_oauth_imports.py | test_auth_login_cli.py |
tests/test_anthropic_oauth.py | test_codex_provider.py |
tests/test_gemini_cli_provider.py  # created per task
```

---

### Task 1: Credential model + token store v2

**Files:**
- Create: `src/avo/oauth/__init__.py`, `src/avo/oauth/store.py`
- Modify: `src/avo/auth.py:53-123` (rewire the four public helpers)
- Test: `tests/test_oauth_store.py`

**Interfaces:**
- Consumes: existing `default_auth_dir()`, `auth_file_path()` in `src/avo/auth.py:33-50`.
- Produces:
  - `avo.oauth.store.Credential` — frozen Pydantic model with fields `provider: str`, `kind: Literal["api_key","oauth"]`, `access_token: str | None` (repr=False), `refresh_token: str | None` (repr=False), `expires_at: datetime | None`, `obtained_at: datetime | None`, `last_refresh_at: datetime | None`, `account: str | None`, `scope: str | None`, `subscription: bool = False`; method `secret() -> str` (raises `AuthError` when no token field set).
  - `load_all_credentials() -> dict[str, Credential]`, `get_credential(provider: str) -> Credential | None`, `store_credential(cred: Credential) -> Path`, `remove_credential(provider: str) -> bool`, `raw_auth_data() -> dict[str, object]` (for the compat shim).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_oauth_store.py
from datetime import datetime, timedelta, timezone
import json
import pytest
from avo.auth import AuthError, get_stored_token, load_all_tokens, store_token
from avo.oauth.store import (
    Credential, get_credential, load_all_credentials, store_credential,
)

@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))

def _cred(provider="claude", **over):
    base = dict(provider=provider, kind="oauth", access_token="at-1",
        refresh_token="rt-1", expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        account="fqih@example.com", subscription=True)
    return Credential(**{**base, **over})

def test_v1_string_file_still_loads():
    path = auth_file(); path.write_text(json.dumps({"openrouter": "sk-or-1"}))
    assert get_stored_token("openrouter") == "sk-or-1"
    assert load_all_credentials()["openrouter"].kind == "api_key"

def test_store_oauth_record_roundtrip():
    store_credential(_cred())
    cred = get_credential("claude")
    assert cred is not None and cred.secret() == "at-1"
    assert json.loads(auth_file().read_text())["claude"]["kind"] == "oauth"
    assert auth_file().stat().st_mode & 0o777 == 0o600

def test_repr_and_str_redact_tokens():
    text = repr(_cred())
    assert "at-1" not in text and "rt-1" not in text

def test_mixed_file_preserves_other_entries_on_write():
    store_token("openrouter", "sk-or-1"); store_credential(_cred())
    assert get_stored_token("openrouter") == "sk-or-1"
    assert get_credential("claude") is not None

def test_secret_raises_when_empty():
    with pytest.raises(AuthError):
        Credential(provider="x", kind="oauth").secret()
```

(Helper `auth_file()` = `avo.auth.auth_file_path()`; add the import.)

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_oauth_store.py -x` → FAIL `ModuleNotFoundError: avo.oauth`.

- [ ] **Step 3: Implement**

`src/avo/oauth/store.py`:

```python
"""Typed credential records (auth.json v2) with v1 string compatibility."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from avo.auth import AuthError, auth_file_path  # noqa: TID253 (late-bound below)


class Credential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    kind: Literal["api_key", "oauth"]
    access_token: str | None = Field(default=None, repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    expires_at: datetime | None = None
    obtained_at: datetime | None = None
    last_refresh_at: datetime | None = None
    account: str | None = None
    scope: str | None = None
    subscription: bool = False

    def secret(self) -> str:
        token = self.access_token
        if not token:
            raise AuthError(f"credential for {self.provider!r} has no access token")
        return token


def _from_raw(provider: str, value: object) -> Credential:
    if isinstance(value, str):
        return Credential(provider=provider, kind="api_key", access_token=value)
    if isinstance(value, dict):
        data = dict(value)
        data.setdefault("provider", provider)
        try:
            return Credential.model_validate(data)
        except Exception:
            return Credential(provider=provider, kind="api_key", access_token="")
    return Credential(provider=provider, kind="api_key", access_token="")


def _to_raw(cred: Credential) -> object:
    if cred.kind == "api_key":
        return cred.access_token or ""
    return cred.model_dump(mode="json", exclude_none=True) | {"kind": "oauth"}


def load_all_credentials() -> dict[str, Credential]:
    path = auth_file_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).lower(): _from_raw(str(k).lower(), v) for k, v in data.items()}


def get_credential(provider: str) -> Credential | None:
    return load_all_credentials().get(provider.lower())


def _write_raw(data: dict[str, object]) -> Path:
    target = auth_file_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    raw = json.dumps({k: _to_raw(v) if isinstance(v, Credential) else v
                      for k, v in data.items()}, indent=2).encode("utf-8")
    fd = os.open(target, flags, 0o600)
    try:
        with open(fd, "wb", closefd=False) as fh:
            fh.write(raw)
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    return target


def store_credential(cred: Credential) -> Path:
    current = load_all_credentials()
    current[cred.provider.lower()] = cred
    return _write_raw(dict(current))


def remove_credential(provider: str) -> bool:
    current = load_all_credentials()
    if provider.lower() not in current:
        return False
    del current[provider.lower()]
    _write_raw(dict(current))
    return True
```

Circular import note: `store.py` imports `AuthError`/`auth_file_path` from `avo.auth`, and `avo.auth` imports the store lazily **inside function bodies** (its top-level import list is untouched) to keep one dependency direction per module-load. In `src/avo/auth.py`, replace bodies of the four helpers:

```python
def load_all_tokens() -> dict[str, str]:
    from avo.oauth.store import load_all_credentials
    out = {}
    for k, c in load_all_credentials().items():
        if c.access_token:
            out[k] = c.access_token
    return out

def get_stored_token(provider: str) -> str | None:
    from avo.oauth.store import get_credential
    cred = get_credential(provider)
    return cred.access_token if cred is not None and cred.access_token else None

def store_token(provider: str, token: str) -> Path:
    from avo.datetime_util import utcnow  # do NOT add: use datetime directly
    from avo.oauth.store import Credential, store_credential
    cred = Credential(provider=provider.lower(), kind="api_key",
                      access_token=token.strip(), obtained_at=datetime.now(timezone.utc))
    return store_credential(cred)
```

(Use `datetime.now(timezone.utc)` inline — no new util module.) `remove_stored_token` delegates to `remove_credential`. Note `store_token` previously deleted the file when empty — new store keeps an empty dict written; behaviorally identical for readers. Add `from datetime import datetime, timezone` to `auth.py` imports. Update `auth.py` docstring (module header lines 1–7) to mention oauth subscriptions and the gate.

- [ ] **Step 4: Run** — `pytest tests/test_oauth_store.py tests/test_auth.py -v` → all PASS (old tests must not break — that is the compat proof).
- [ ] **Step 5: Gates + commit**

```bash
ruff check . && ruff format --check . && mypy src/avo && pytest -q
git add src/avo/oauth/ src/avo/auth.py tests/test_oauth_store.py
git commit -m "feat(auth): typed oauth credential records"
```

---

### Task 2: OAuth registry table + subscription gate

**Files:**
- Create: `src/avo/oauth/registry.py`, `src/avo/oauth/gate.py`
- Modify: `src/avo/oauth/__init__.py`
- Test: `tests/test_oauth_registry.py`, `tests/test_oauth_gate.py`

**Interfaces:**
- Consumes: Task 1 `Credential`.
- Produces: `registry.OAuthEntry` (frozen dataclass) + `registry.OAUTH: dict[str, OAuthEntry]` keyed `"claude" | "codex" | "gemini"`; `gate.SUBSCRIPTION_ENV = "AVO_ALLOW_SUBSCRIPTION"`, `gate.subscription_allowed(environ=None) -> bool`, `gate.require_subscription_allowed(environ=None) -> None` (raises `AuthError`).

- [ ] **Step 1: Failing tests**

```python
# tests/test_oauth_registry.py
import pytest
from avo.oauth.registry import OAUTH, OAuthEntry

@pytest.mark.parametrize("key", ["claude", "codex", "gemini"])
def test_entries_present_and_https(key):
    entry = OAUTH[key]
    for url in (entry.authorize_url, entry.token_url):
        assert url.startswith("https://")
    assert entry.code_challenge_method == "S256"

def test_claude_constants_match_upstream():
    e = OAUTH["claude"]
    assert e.client_id == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert e.authorize_url == "https://claude.ai/oauth/authorize"
    assert e.token_url == "https://api.anthropic.com/v1/oauth/token"
    assert e.exchange_encoding == "json" and e.refresh_lead_seconds == 14400

def test_entry_is_frozen():
    with pytest.raises(Exception):
        OAUTH["claude"].client_id = "x"
```

```python
# tests/test_oauth_gate.py
import pytest
from avo.auth import AuthError
from avo.oauth.gate import require_subscription_allowed, subscription_allowed

def test_gate_default_off(): assert subscription_allowed({}) is False
def test_gate_on():         assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "1"}) is True
def test_gate_true_string():assert subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "true"}) is True
def test_require_raises_with_hint():
    with pytest.raises(AuthError, match="AVO_ALLOW_SUBSCRIPTION"):
        require_subscription_allowed({})
    require_subscription_allowed({"AVO_ALLOW_SUBSCRIPTION": "1"})  # no raise
```

- [ ] **Step 2: Run → FAIL (ModuleNotFoundError).**
- [ ] **Step 3: Implement.** `registry.py` — copy every value from spec §5 verbatim into three frozen dataclass instances (fields: `provider, display_name, authorize_url, token_url, client_id, client_secret, scopes: tuple[str, ...], code_challenge_method="S256", extra_auth_params: Mapping[str, str] = {}, callback_port, callback_path="/auth/callback", exchange_encoding="form", refresh_encoding="form", refresh_lead_seconds, max_age_seconds: float | None = None, userinfo_url: str | None = None, inference_base_url, identity_headers: Mapping[str, str] = {}`); module header comment credits the port to 9router (MIT) with upstream file paths `src/lib/oauth/providers/{claude,codex,gemini-cli}.js`, `open-sse/providers/registry/{claude,codex,gemini-cli}.js`, re-synced 2026-09-16. Use codex `client_secret=None` (public client), `max_age_seconds=691200` (8 days), `exchange_encoding="form"`. `gemini` uses the cloudcode client id/secret pair and `userinfo_url=https://www.googleapis.com/oauth2/v1/userinfo`. `identity_headers`: claude `{"anthropic-beta": "claude-code-20250219,oauth-2025-04-20"}` (minimal, not the full 9router beta soup); codex `{"originator": "codex_cli_rs", "User-Agent": "codex_cli_rs/0.154.0"}`; gemini `{"x-goog-api-client": "google-genai-sdk/1.41.0"}`. `gate.py`:

```python
"""Explicit opt-in policy for subscription (non-API-key) inference."""
from __future__ import annotations
import os
from collections.abc import Mapping
from avo.auth import AuthError

SUBSCRIPTION_ENV = "AVO_ALLOW_SUBSCRIPTION"

def subscription_allowed(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(SUBSCRIPTION_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

def require_subscription_allowed(environ: Mapping[str, str] | None = None) -> None:
    if not subscription_allowed(environ):
        raise AuthError(
            "subscription-backed inference is disabled by default because it "
            "uses unofficial client endpoints and may risk account limits. "
            f"Set {SUBSCRIPTION_ENV}=1 to opt in; see docs/guides/subscription-auth.md"
        )
```

`__init__.py` re-exports: `from avo.oauth.gate import subscription_allowed, require_subscription_allowed`, `from avo.oauth.registry import OAUTH, OAuthEntry`, `from avo.oauth.store import Credential, get_credential, load_all_credentials, store_credential` (with `__all__`).

- [ ] **Step 4: Run → PASS.** - [ ] **Step 5: Gates + commit** `git commit -m "feat(auth): oauth provider registry and subscription gate"`

---

### Task 3: Generic PKCE authorization-code flow

**Files:**
- Create: `src/avo/oauth/flows.py`
- Test: `tests/test_oauth_flows.py`

**Interfaces:**
- Consumes: `avo.auth.generate_pkce_pair()` (:126), `avo.auth.run_localhost_callback_server()` (:135), `avo.oauth.registry.OAuthEntry`.
- Produces: `flows.build_authorize_url(entry, redirect_uri, state, challenge) -> str`; `flows.post_json(url, body: dict, headers: dict, timeout: float = 15.0) -> dict` (sync urllib); `flows.post_form(url, form: dict, headers: dict, timeout: float = 15.0) -> dict` (sync); `flows.request_token(entry, form: dict) -> dict` (picks encoding, raises `AuthError` with response body on non-2xx); `async flows.run_pkce_login(entry, *, callback_runner=run_localhost_callback_server, token_requester=flows.request_token, open_browser: bool = True, timeout_seconds: float = 300.0, output_writer=sys.stdout.write) -> Credential`.

- [ ] **Step 1: Failing tests** (all offline — fakes only)

```python
# tests/test_oauth_flows.py
import asyncio, urllib.parse
import pytest
from avo.auth import AuthError
from avo.oauth import flows
from avo.oauth.registry import OAUTH

def test_authorize_url_contains_pkce_and_state():
    e = OAUTH["claude"]
    url = flows.build_authorize_url(e, "http://localhost:43111/auth/callback", "ST", "CHAL")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge"] == ["CHAL"] and q["state"] == ["ST"]
    assert q["client_id"] == [e.client_id]
    assert q["code"] == ["true"]  # claude extra param

def test_exchange_sends_verifier_and_grant(monkeypatch):
    captured = {}
    def fake(url, body, headers, timeout=15.0):
        captured.update(url=url, body=body); return {"access_token": "at", "refresh_token": "rt",
            "expires_in": 21600, "scope": "user:inference",
            "account": {"email_address": "fqih@example.com"}}
    cred = asyncio.run(flows.run_pkce_login(
        OAUTH["claude"],
        callback_runner=lambda **kw: _done({"code": "c1", "state": kw["expected_state"]}),
        token_requester=lambda entry, form: fake(entry.token_url, form, {}),
        open_browser=False, output_writer=lambda s: None))
    assert captured["body"]["code_verifier"]
    assert captured["body"]["grant_type"] == "authorization_code"
    assert cred.secret() == "at" and cred.account == "fqih@example.com"

def _done(value):
    fut = asyncio.get_event_loop_policy().new_event_loop()  # simpler: build real future below
```

Replace `_done` with a fake callback runner returning an already-completed coroutine:

```python
async def _fake_cb(**kw): return {"code": "c1", "state": kw["expected_state"]}
```

and pass `callback_runner=_fake_cb`. Add: `test_state_mismatch_raises` (callback returns state "BAD" → `AuthError`), `test_token_error_becomes_auth_error` (token_requester raises with body text → wrapped `AuthError`, message contains the body), `test_map_account_from_claude_shape` (account nested dict email_address → `cred.account`).

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `flows.py`. `build_authorize_url`: `URLSearchParams` equivalent with `response_type=code`, `client_id`, `redirect_uri`, `scope=" ".join(entry.scopes)`, `code_challenge`, `code_challenge_method`, `state=secrets.token_urlsafe(32)` generated by the **caller**, then `entry.extra_auth_params`. `run_pkce_login` sequence: generate verifier/challenge + state → print URL (+ `webbrowser.open` if `open_browser`) → `params = await callback_runner(port=entry.callback_port, callback_path=entry.callback_path, timeout_seconds=timeout_seconds, expected_state=state)`; if `params.get("state") != state` raise; code = `params["code"].split("#")[0]` (9router compat); exchange with `{code, state, grant_type, client_id, redirect_uri, code_verifier}` (+ `client_secret` when set); map via `map_tokens(raw, entry)`:

```python
def map_tokens(raw: dict, entry) -> Credential:
    expires_in = raw.get("expires_in")
    now = datetime.now(timezone.utc)
    account = None
    acc = raw.get("account")
    if isinstance(acc, dict):
        account = acc.get("email_address") or acc.get("email")
    return Credential(
        provider=entry.provider, kind="oauth",
        access_token=raw.get("access_token"), refresh_token=raw.get("refresh_token"),
        expires_at=now + timedelta(seconds=float(expires_in)) if expires_in else None,
        obtained_at=now, account=account, scope=raw.get("scope"), subscription=True)
```

Also extend `run_localhost_callback_server` signature in `auth.py` with `expected_state: str | None = None` kwarg (when set, ignore requests whose state mismatches) — small additive change, existing callers unaffected.

- [ ] **Step 4: Run → PASS + `pytest tests/test_auth.py` regression.** - [ ] **Step 5: Gates + commit** `git commit -m "feat(auth): generic pkce authorization-code login flow"`

---

### Task 4: Refresh lifecycle + credential resolution

**Files:**
- Create: `src/avo/oauth/refresh.py`, `src/avo/oauth/resolution.py`
- Test: `tests/test_oauth_refresh.py`, `tests/test_oauth_resolution.py`

**Interfaces:**
- Consumes: `Credential`, `store_credential/get_credential`, `OAuthEntry`, `flows` token requesters, `gate`.
- Produces:
  - `refresh.needs_refresh(cred: Credential, *, now: datetime, entry: OAuthEntry) -> bool`
  - `async refresh.refresh_credential(entry: OAuthEntry, cred: Credential, *, token_requester=flows.request_token) -> Credential` (pure; persists not included)
  - `async refresh.ensure_fresh(provider: str, *, now=None, token_requester=...) -> Credential` — per-provider `asyncio.Lock`; raises `AuthError("reauth required — run avo login <provider>")` on second failure and marks `reauth_required` via clearing refresh path (keep simple: raise only).
  - `resolution.resolve_credential(store_key: str, environ=None) -> Credential | None` — env `AVO_{STORE_KEY_UPPER}_API_KEY` wins; else stored `api_key` record; else oauth record (with `require_subscription_allowed` + `needs_refresh` handled by caller); `resolution.PROVIDER_TO_STORE_KEY = {"anthropic": "claude", "openrouter": "openrouter", "github": "github", "openai": "codex", "gemini": "gemini"}`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_oauth_refresh.py
from datetime import datetime, timedelta, timezone
import asyncio
import pytest
from avo.auth import AuthError
from avo.oauth.registry import OAUTH
from avo.oauth.refresh import ensure_fresh, needs_refresh, refresh_credential
from avo.oauth.store import Credential, get_credential, store_credential

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)

def _cred(**over):
    base = dict(provider="claude", kind="oauth", access_token="at-old",
                refresh_token="rt-old", obtained_at=NOW - timedelta(hours=1),
                expires_at=NOW + timedelta(hours=5), subscription=True)
    return Credential(**{**base, **over})

class FakeRequester:
    def __init__(self, replies): self.replies = list(replies); self.calls = 0
    async def __call__(self, entry, form):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception): raise reply
        return reply

def test_needs_refresh_boundary():
    e = OAUTH["claude"]
    assert needs_refresh(_cred(expires_at=NOW + timedelta(seconds=e.refresh_lead_seconds)), now=NOW, entry=e)
    assert not needs_refresh(_cred(expires_at=NOW + timedelta(seconds=e.refresh_lead_seconds + 1)), now=NOW, entry=e)

def test_api_key_credential_never_needs_refresh():
    assert not needs_refresh(Credential(provider="x", kind="api_key", access_token="k"), now=NOW, entry=OAUTH["claude"])

@pytest.mark.asyncio
async def test_rotation_persists_new_refresh_token(store_dir, frozen_now, monkeypatch):
    store_credential(_cred())
    fake = FakeRequester([{"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 21600}])
    cred = await ensure_fresh("claude", now=frozen_now, token_requester=fake)
    assert cred.secret() == "at-new"
    assert get_credential("claude").refresh_token == "rt-new"  # rotated rt saved
    assert fake.calls == 1

@pytest.mark.asyncio
async def test_second_failure_requires_relogin(store_dir, frozen_now):
    store_credential(_cred())
    fake = FakeRequester([AuthError("401"), AuthError("401")])
    with pytest.raises(AuthError, match="avo login claude"):
        await ensure_fresh("claude", now=frozen_now, token_requester=fake)

@pytest.mark.asyncio
async def test_concurrent_ensure_fresh_refreshes_once(store_dir, frozen_now):
    store_credential(_cred())
    fake = FakeRequester([{"access_token": "at-new", "expires_in": 21600}])
    await asyncio.gather(*(ensure_fresh("claude", now=frozen_now, token_requester=fake) for _ in range(10)))
    assert fake.calls == 1  # per-provider lock + reload-inside-lock

@pytest.mark.asyncio
async def test_codex_max_age_forces_relogin(store_dir):
    e = OAUTH["codex"]
    old = _cred(provider="codex", obtained_at=NOW - timedelta(seconds=e.max_age_seconds + 60),
                expires_at=NOW + timedelta(days=1))
    store_credential(old)
    with pytest.raises(AuthError, match="re-login"):
        await ensure_fresh("codex", now=NOW, token_requester=FakeRequester([]))
```

(`store_dir` fixture = `monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))`; `frozen_now` patches the module's clock seam — `refresh.py` must take `now: datetime | None = None` and default `datetime.now(timezone.utc)` internally, so tests inject `NOW`.)

```python
# tests/test_oauth_resolution.py
def test_env_key_wins_over_store(store_dir):
    store_token("claude", "sk-stored")
    cred = resolve_credential("claude", {"AVO_CLAUDE_API_KEY": "sk-env"})
    assert cred.secret() == "sk-env"

def test_stored_api_key_used(store_dir):
    store_token("claude", "sk-stored")
    assert resolve_credential("claude", {}).secret() == "sk-stored"

def test_oauth_blocked_without_gate(store_dir):
    store_credential(_cred())
    assert resolve_credential("claude", {}) is None

def test_oauth_ok_with_gate(store_dir):
    store_credential(_cred(expires_at=datetime.now(timezone.utc) + timedelta(hours=5)))
    cred = resolve_credential("claude", {"AVO_ALLOW_SUBSCRIPTION": "1"})
    assert cred.kind == "oauth"
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `ensure_fresh` logic: take `asyncio.Lock` (module-level dict keyed by provider) → reload credential inside lock → if `kind != "oauth"` return as-is → if `max_age_seconds` exceeded since `obtained_at` → raise re-auth → if `expires_at` beyond lead → return → else POST refresh (`grant_type=refresh_token`, form or json per `entry.refresh_encoding`, include `client_id`, `client_secret` when set) → `map_tokens` + carry `account` → `store_credential`. On any exception after one retry: `AuthError(f"{provider} session expired — run avo login {provider}")`.
- [ ] **Step 4: Run → PASS.** - [ ] **Step 5: Gates + commit** `git commit -m "feat(auth): token refresh lifecycle with rotation and dedupe"`

---

### Task 5: `avo login` CLI overhaul

**Files:**
- Modify: `src/avo/auth.py:333-400` (`main_login`)
- Test: `tests/test_auth_login_cli.py`

**Interfaces:**
- Consumes: `flows.run_pkce_login`, `OAUTH`, `imports` hook (Task 6 stub: call `find_importable(store_key)` guarded by `try: from avo.oauth.imports import find_importable except ImportError: find_importable = None` — Task 6 makes it real), existing `login_openrouter`, `login_github_device`.
- Produces: providers `{openrouter, github, claude, codex, chatgpt, gemini}`; flags `--key-stdin`, `--status` v2, `--headless`, `--no-browser`, `--yes`; unknown provider prints the sorted supported list.

- [ ] **Step 1: Failing tests** — invoke `main_login([...])` with monkeypatched `login_*` functions and `sys.stdin`; assert exit codes: `claude` → login fn called, returns 0; `--key-stdin` (stdin `sk-test\n`) → `store_token("deepseek", ...)`; unknown provider → 2; `--status` with an oauth record prints `claude: oauth ✓ fqih@example.com` + `expires` but never `at-1`.
- [ ] **Step 2: FAIL. Step 3: Implement** argparse additions (`--provider` positional now choices-checked dynamically; `codex` also registers alias `chatgpt`); oauth path runs `asyncio.run(run_pkce_login(OAUTH[store_key]))` then prints ✓ line + store path; `--key-stdin` reads `sys.stdin.readline().strip()`, non-empty check, `store_token`. Status v2: iterate `load_all_credentials()`, render kind/account/expiry (local time `%H:%M`) or masked key (`val[:4]+"..."+val[-4:]`) — never oauth tokens.
- [ ] **Step 4: PASS (extend, don't rewrite, existing `tests/test_auth.py::test_main_login_status_and_logout` expectations — update that test's expectations for the new masked format if it asserts on it.)**
- [ ] **Step 5: Gates + commit** `git commit -m "feat(cli): universal login for oauth and api key providers"`

---

### Task 6: Reuse existing official-CLI logins

**Files:**
- Create: `src/avo/oauth/imports.py`
- Test: `tests/test_oauth_imports.py`

**Interfaces:**
- Consumes: Task 1 `Credential`; Task 5 calls `find_importable`.
- Produces: `find_importable(store_key: str) -> Credential | None`; `import_credential(store_key: str, *, copy=True) -> Credential | None` (reads upstream files, never writes them).

- [ ] **Step 1: Failing tests** — `tmp_path` fixtures writing realistic files: Claude Code `~/.claude/.credentials.json` shape `{"claudeAiOauth": {"accessToken": "at", "refreshToken": "rt", "expiresAt": <epoch_ms int>, "scope": "user:inference"}}`; Codex `~/.codex/auth.json` shape `{"tokens": {"access_token": "at", "refresh_token": "rt", "id_token": "<jose: header.payload.sig with payload containing email + created_at>"}}` — build the fake `id_token` in the fixture with `base64.urlsafe_b64encode` of a JSON payload (decode path must not verify signature and must tolerate garbage id_token). Assert: fields land on `Credential` (`subscription=True`, `account` email for codex, `expires_at` from ms epoch), missing file → `None`, malformed JSON → `None` (never raise).
- [ ] **Step 2: FAIL. Step 3: Implement** (~60 lines: two readers + dispatch map `{"claude": claude_code_credential, "codex": codex_cli_credential, "chatgpt": codex_cli_credential}`, each `Path.home()/<...>` guarded by `expanduser`, `contextlib.suppress(OSError, ValueError, KeyError)` around parsing).
- [ ] **Step 4: PASS. Step 5: Gates + commit** `git commit -m "feat(auth): import existing claude code and codex cli logins"` — plus wire it into Task 5's login path (remove the try-import shim).

---

### Task 7: Anthropic OAuth auth-mode

**Files:**
- Modify: `src/avo/providers/anthropic.py` (config :42-87, provider :95-145 and stream path)
- Test: `tests/test_anthropic_oauth.py`

**Interfaces:**
- Consumes: `ensure_fresh("claude")`, `registry.OAUTH["claude"].identity_headers`, `gate`.
- Produces: `AnthropicConfig` gains `auth_mode: Literal["api_key", "oauth"] = "api_key"` (model field) and classmethod `AnthropicConfig.oauth(model: str, *, base_url=_DEFAULT_BASE_URL) -> AnthropicConfig`; `AnthropicProvider.__init__(..., token_provider: Callable[[], Awaitable[str]] | None = None)`; async `AnthropicConfig.request_headers(token: str | None) -> dict[str, str]`; endpoint in oauth mode: `f"{base_url}/v1/messages?beta=true"`. `from_avo_env` falls back to stored credential via `resolution.resolve_credential("claude", environ)` (env `AVO_ANTHROPIC_API_KEY` still wins; `require_subscription_allowed` enforced only for oauth kind) before raising `ValueError`.

- [ ] **Step 1: Failing tests** — fake injected `_AsyncHTTPClient` (existing pattern from `tests/` for anthropic provider — mirror `test_provider` files using `client=` param) capturing headers: oauth config + `token_provider=async lambda: "at-1"` → request carries `Authorization: Bearer at-1`, no `x-api-key`, plus `anthropic-beta` value from registry, URL ends `?beta=true`; api_key mode unchanged (regression); `from_avo_env` with stored oauth + no gate → raises `AuthError` containing `AVO_ALLOW_SUBSCRIPTION`; with gate → returns config with `auth_mode == "oauth"`.
- [ ] **Step 2: FAIL. Step 3: Implement** — replace both `self._config.headers()` call sites (:145, :198) with `await self._config.request_headers(await self._token_source())` where `_token_source = token_provider or (lambda: _sync(self._api_key))`; default `token_provider=None` + api_key mode keeps the existing sync headers path byte-identical when `auth_mode == "api_key"` (guard early-return so existing tests pass unedited).
- [ ] **Step 4: PASS + regression `pytest tests/ -k anthropic`. Step 5: Gates + commit** `git commit -m "feat(providers): oauth auth mode for anthropic provider"`

---

### Task 8: CodexProvider (ChatGPT subscription, Responses API)

**Files:**
- Create: `src/avo/providers/codex.py`
- Test: `tests/test_codex_provider.py`

**Interfaces:**
- Consumes: `ensure_fresh("codex")`, `OAUTH["codex"]` (`inference_base_url="https://chatgpt.com/backend-api/codex"`, identity headers), Task 1 `Credential`.
- Produces: `CodexConfig(BaseModel)` (`model`, `base_url`, `auth_mode`-always-oauth, classmethod `from_avo_env` resolving the `codex` credential, `endpoint` property → `{base_url}/responses`); `CodexProvider(config, *, token_provider, client=None)` implementing `ModelProvider` (`name = "codex"`, `async generate(request) -> ModelResponse`, `async stream(request)`).

Payload mapping (Responses wire): `body = {"model": config.model, "store": False, "stream": True, "input": [...]}` — `request.messages` map `system` → single `{"role": "system", "content": text}` input item (first), `user`/`assistant` pass through as `{"role", "content": text}` items, assistant `tool_calls` → `{"type": "function_call", "call_id", "name", "arguments": json.dumps(...)}` and `role=="tool"` → `{"type": "function_call_output", "call_id", "output": ...}`; `request.tools` (list[`ToolMetadata`]) → `{"type": "function", "name", "description", "parameters": tool.input_schema}`. Parse `generate` from the aggregated SSE (reuse `avo.providers.streaming` helpers as `openai_compatible.py` does): last `response.completed` event → `response.output[]`: first `function_call` item → `ModelResponse(tool_call=ToolCall(tool_call_id=item["call_id"], name=item["name"], arguments=json.loads(item["arguments"])))`; else concat `message` `output_text` parts → `content`; `usage = TokenUsage(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)`. Missing both → `ProviderError("codex returned no output", retryable=False)`. Follow `openai_compatible.py`'s `_post`/SSE reader structure (import the shared helpers from there if exported; otherwise copy its ~40-line `aiter_bytes` framing — no new abstraction).

- [ ] **Step 1: Failing tests**

```python
# tests/test_codex_provider.py
import json
import pytest
from avo import ModelRequest, ToolCall
from avo.exceptions import ProviderError
from avo.providers.codex import CodexConfig, CodexProvider

class FakeClient:
    """Same injected-client contract openai_compatible tests use: async post(url, headers, json)
    -> object with .status_code and .json() (mirror the real fake in tests/ for openai)."""
    def __init__(self, reply): self.reply = reply; self.requests = []
    async def post(self, url, headers=None, json=None, **kw):
        self.requests.append({"url": url, "headers": headers, "body": json})
        return _Resp(self.reply)

class _Resp:
    def __init__(self, payload): self._p = payload; self.status_code = 200
    def json(self): return self._p
    @property
    def text(self): return json.dumps(self._p)

COMPLETED_TEXT = {"type": "response.completed", "response": {
    "id": "resp_1", "output": [{"type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": "hello"}]}],
    "usage": {"input_tokens": 11, "output_tokens": 3}}}
COMPLETED_CALL = {"type": "response.completed", "response": {
    "id": "resp_2", "output": [{"type": "function_call", "call_id": "c1",
        "name": "add", "arguments": '{"a":2,"b":2}'}], "usage": {}}}

def _req(**over):
    base = dict(run_id="r1", step=1, messages=[{"role": "user", "content": "hi"}])
    return ModelRequest(**{**base, **over})

async def _token(): return "at-1"

@pytest.mark.asyncio
async def test_generates_text_and_uses_bearer_identity():
    client = FakeClient(COMPLETED_TEXT)
    p = CodexProvider(CodexConfig(model="gpt-5.6-sol"), token_provider=_token, client=client)
    res = await p.generate(_req())
    assert res.content == "hello" and res.usage.output_tokens == 3
    sent = client.requests[0]
    assert sent["url"].endswith("/responses")
    assert sent["headers"]["Authorization"] == "Bearer at-1"
    assert sent["headers"]["originator"] == "codex_cli_rs"
    assert sent["body"]["store"] is False and sent["body"]["stream"] is True

@pytest.mark.asyncio
async def test_parses_function_call():
    p = CodexProvider(CodexConfig(model="m"), token_provider=_token,
                      client=FakeClient(COMPLETED_CALL))
    res = await p.generate(_req())
    assert res.tool_call == ToolCall(tool_call_id="c1", name="add", arguments={"a": 2, "b": 2})

@pytest.mark.asyncio
async def test_maps_system_and_tool_items():
    client = FakeClient(COMPLETED_TEXT)
    p = CodexProvider(CodexConfig(model="m"), token_provider=_token, client=client)
    await p.generate(_req(messages=[
        {"role": "system", "content": "be brief"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "name": "add", "arguments": {"a": 1, "b": 1}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "2"},
    ]))
    items = client.requests[0]["body"]["input"]
    assert items[0] == {"role": "system", "content": "be brief"}
    assert items[-1] == {"type": "function_call_output", "call_id": "c1", "output": "2"}

@pytest.mark.asyncio
async def test_empty_output_raises():
    p = CodexProvider(CodexConfig(model="m"), token_provider=_token,
                      client=FakeClient({"type": "response.completed", "response": {"output": []}}))
    with pytest.raises(ProviderError):
        await p.generate(_req())
```

(If the provider's non-stream path posts and reads one JSON object instead of SSE, implement `generate` against exactly this fake — one `post`, full payload — and implement `stream` with the `aiter_bytes` framing copied from `openai_compatible.py`; that split matches how the existing openai provider is tested. Adjust `FakeClient` to the real `_AsyncHTTPClient` protocol in `http_common.py` when you read it.)
- [ ] **Step 2: FAIL. Step 3: Implement (~200 lines, mirroring `openai_compatible.py` structure exactly — read that file first; it is the template).**
- [ ] **Step 4: PASS. Step 5: Gates + commit** `git commit -m "feat(providers): codex subscription provider on responses api"`

---

### Task 9: GeminiCliProvider (Google OAuth, cloudcode-pa)

**Files:**
- Create: `src/avo/providers/gemini_cli.py`
- Test: `tests/test_gemini_cli_provider.py`

**Interfaces:** Consumes `ensure_fresh("gemini")`, `OAUTH["gemini"]`, `Credential`. Produces `GeminiCliConfig` (`endpoint` → `https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse`) and `GeminiCliProvider(config, *, token_provider, client=None)` (`name = "gemini_cli"`).

Body: `{"model": config.model, "config": {"systemInstruction"? , "tools": [...]}, "contents": [{"role": "user|model", "parts": [{"text": ...}]}]}` — map avo `tool` messages to Gemini `functionResponse` parts and assistant `functionCall` parts; parse `candidates[0].content.parts[]` → text concat or first `functionCall` → `ToolCall(name=fc["name"], arguments=fc["args"], tool_call_id=fc.get("id", new id))`; usage from `usageMetadata.promptTokenCount / candidatesTokenCount`. Auth: bearer + `x-goog-api-client` identity header only.

- [ ] **Step 1: Failing tests**

```python
# tests/test_gemini_cli_provider.py
import pytest
from avo import ModelRequest
from avo.providers.gemini_cli import GeminiCliConfig, GeminiCliProvider

# reuse FakeClient/_Resp helpers from tests/test_codex_provider.py via
# tests/helpers.py (move them there in this task; both files import from helpers)
DONE_TEXT = {"candidates": [{"content": {"role": "model",
    "parts": [{"text": "halo"}]}}, ], "usageMetadata":
    {"promptTokenCount": 7, "candidatesTokenCount": 2}}
DONE_CALL = {"candidates": [{"content": {"role": "model", "parts": [
    {"functionCall": {"id": "fc1", "name": "add", "args": {"a": 1, "b": 1}}}]}}, ]}

async def _token(): return "at-9"

def _req(msgs=None):
    return ModelRequest(run_id="r1", step=1,
                        messages=msgs or [{"role": "user", "content": "hi"}])

@pytest.mark.asyncio
async def test_bearer_and_url_and_parse_text():
    client = FakeClient(DONE_TEXT)
    p = GeminiCliProvider(GeminiCliConfig(model="gemini-2.5-pro"), token_provider=_token, client=client)
    res = await p.generate(_req())
    assert res.content == "halo" and res.usage.input_tokens == 7
    sent = client.requests[0]
    assert "v1internal:streamGenerateContent" in sent["url"] and "alt=sse" in sent["url"]
    assert sent["headers"]["Authorization"] == "Bearer at-9"
    assert sent["headers"]["x-goog-api-client"].startswith("google-genai-sdk")
    assert sent["body"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]

@pytest.mark.asyncio
async def test_parses_function_call():
    p = GeminiCliProvider(GeminiCliConfig(model="m"), token_provider=_token, client=FakeClient(DONE_CALL))
    res = await p.generate(_req())
    assert res.tool_call.name == "add" and res.tool_call.arguments == {"a": 1, "b": 1}

@pytest.mark.asyncio
async def test_tool_messages_map_to_function_response():
    client = FakeClient(DONE_TEXT)
    p = GeminiCliProvider(GeminiCliConfig(model="m"), token_provider=_token, client=client)
    await p.generate(_req([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "fc1", "name": "add", "arguments": {"a": 1, "b": 1}}]},
        {"role": "tool", "tool_call_id": "fc1", "content": "2"},
    ]))
    contents = client.requests[0]["body"]["contents"]
    assert contents[-1]["parts"][0]["functionResponse"] == {"name": "add", "response": {"result": "2"}}
```

Note Gemini role quirk: assistant history role is `"model"`, not `"assistant"` — map accordingly.
- [ ] **Step 2: FAIL** [ ] **Step 3: Implement (~180 lines, `gemini.py` is the structural template — read it first)** [ ] **Step 4: PASS** [ ] **Step 5: Gates + commit** `git commit -m "feat(providers): gemini cli subscription provider"`

---

### Task 10: Factory wiring, chat setup, doctor, docs

**Files:**
- Modify: `src/avo/config.py:173+` (branches `anthropic` done in T7; `openai`, `gemini`: consult `resolution` → construct oauth providers when `kind=="oauth"` + gate ok; register names `codex`, `gemini-cli` in `_PROVIDER_NAMES` (:31) and dispatch), `src/avo/chat_setup.py` (`_PROVIDER_CATALOG` add the three subscription providers marked "subscription (opt-in)"; on key prompt, offer `Reuse stored login? [Y/n]` when `get_credential` has an oauth record; after pasting a key ask `Save to ~/.config/avo/auth.json? [y/N]` — default N keeps the current never-persist behavior), `src/avo/doctor.py` (status line per credential: `provider kind account expires` — no secrets), `mkdocs.yml` (nav: Guides → `guides/subscription-auth.md`), `CLAUDE.md` (drop the stale "read docs/avo-reference.md" line — it still points at the pre-split name; replace with `docs/reference/`), `docs/reference/storage-and-config.md` (§12.3 env table: add `AVO_ALLOW_SUBSCRIPTION` row — note § numbers frozen, append to the table only).
- Create: `docs/guides/subscription-auth.md`
- Test: extend `tests/test_config.py`, `tests/test_chat_setup.py`, `tests/test_doctor.py`.

- [ ] **Step 1: Failing tests** — `build_provider_from_env({"AVO_PROVIDER": "codex", "AVO_MODEL": "gpt-5.6-sol", ...})` with stored cred + gate → `CodexProvider` instance; gate off → `ConfigError` mentioning `AVO_ALLOW_SUBSCRIPTION`; `avo login` hint in the error.
- [ ] **Step 2: FAIL. Step 3: Implement.** Guide page: honest risk warning first (quote: 9router marks these `RISK_NOTICE`; possible account limits/suspension), flag usage, per-provider login walkthrough, import-reuse note, troubleshooting table (`reauth required`, 403s, clock skew).
- [ ] **Step 4: `pytest -q` (full suite) + `mkdocs build --strict` + `ruff`/`mypy` clean.**
- [ ] **Step 5: Commit per file-group** (`feat(config): resolve stored credentials in provider factory`, `docs(guides): subscription oauth auth guide`, `fix(docs): point CLAUDE.md at split reference tree`).

---

### Task 11: End-to-end dogfood + security sweep

- [ ] Manual, in a terminal you control: `avo login claude` with your real account → `avo doctor` shows it → `AVO_ALLOW_SUBSCRIPTION=1 AVO_PROVIDER=anthropic AVO_MODEL=claude-sonnet-5 avo chat "say hi"` → confirm bearer path works and `grep -ri "$(python -c 'from avo.oauth.store import get_credential as g; print(g("claude").secret())')" ~/.config/avo/ ./avo.db` finds token **only** in `auth.json`.
- [ ] `bandit -q -r src/avo` clean (dev extra already installed); `avo runs list` and audit log show no token strings.
- [ ] Update `docs/changelog.md` (Unreleased) — CHANGELOG.md source file.
- [ ] `git push` only via `~/.local/bin/git-push-notify origin feat/subscription-oauth` **after user confirms**.

**S1 definition of done** = spec §13 targets: login survives reboot (auto-refresh verified with a short `expires_at` fixture), universal key login for every catalog provider, gate enforced, suite green offline.
