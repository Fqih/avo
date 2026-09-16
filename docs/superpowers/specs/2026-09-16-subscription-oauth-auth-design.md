# S1 — Subscription OAuth auth for Claude, ChatGPT (Codex), and Gemini

- Status: approved in design discussion 2026-09-16 (user: pilot all three
  providers, explicit opt-in gating, one subproject at a time)
- Branch: continues from `docs/redesign` work, targets `main`
- Upstream reference: [decolua/9router](https://github.com/decolua/9router)
  (MIT), which itself credits CLIProxyAPI (Go) for the flow shapes

## 1. Goal

Let a user run Avo against their existing **subscription** accounts
(Claude Pro/Max via the Claude Code OAuth grant, ChatGPT Plus/Pro via the
Codex CLI grant, Gemini free-tier via the Gemini CLI grant) with one
`avo login <provider>` command, persistent refreshable tokens, and the
same fail-closed, offline-testable discipline as the rest of Avo.

Plain API-key login becomes a first-class, universal path in the same
command, closing the current gap where only `openrouter` and `github`
have a login flow and the chat REPL never persists a pasted key.

### Non-goals (other subprojects)

- Combo/multi-model routing tiers (S2), native performance work (S5),
  token-saver presets (S6), README/GitHub presentation (S4).
- Copilot/Cursor/Kimi providers — same plumbing, added later by copying
  the registry pattern; `github` device flow already exists in
  `src/avo/auth.py` as proof.

## 2. Current state

`src/avo/auth.py` stores a flat `provider -> api_key` string map in
`~/.config/avo/auth.json` (0600, `AVO_CONFIG_DIR`/XDG overridable).
It already contains the reusable primitives: `generate_pkce_pair()`
(:126), `run_localhost_callback_server()` (:135), `login_openrouter()`
(:198, PKCE + localhost callback), `login_github_device()` (:258,
RFC 8628), `main_login()` (:333). `src/avo/chat_setup.py` prompts for
keys via getpass and deliberately never persists them — this spec
supersedes that rule for stored tokens (a refresh token that dies with
the process is useless); the store stays 0600 and redacted.

Providers (`src/avo/providers/`) authenticate with a static key
(`x-api-key`, `Authorization: Bearer`, or query param depending on
provider). None of them refresh.

## 3. Architecture

```
src/avo/oauth/
    __init__.py        # public surface: login(), get_access_token(), status()
    registry.py        # static per-provider OAuth+endpoint constants (data only)
    flows.py           # generic authorization-code+PKCE and device-code flows
    refresh.py         # token refresh lifecycle: proactive + 401-once
    imports.py         # read ~/.claude/.credentials.json, ~/.codex/auth.json
    store.py           # token store v2 (re-exports from avo.auth for compat)
src/avo/auth.py        # keeps CLI wiring + backward-compatible helpers
```

`avo_native`/core dependency policy is untouched: everything here runs on
stdlib + Pydantic only (urllib, asyncio, secrets, hashlib — same
approach the existing login flows use). No `requests`, no `authlib`.

## 4. Token store v2

`auth.json` records become objects; bare strings remain valid:

```json
{
  "openrouter": "sk-or-v1-...",
  "claude": {
    "type": "oauth",
    "account": "fqih@example.com",
    "access_token": "...",
    "refresh_token": "...",
    "expires_at": "2026-09-16T18:04:00+00:00",
    "scope": "org:create_api_key user:profile user:inference",
    "obtained_at": "2026-09-16T12:04:00+00:00",
    "subscription": true
  }
}
```

- `load_all_tokens()` gains a sibling `load_all_credentials() -> dict[str, Credential]`
  returning typed records; old function keeps working by flattening
  (`str` value → api_key record; oauth record → `access_token`).
- Writes stay atomic with `os.open(..., 0o600)` (existing pattern).
- Migration: none needed — v2 readers accept v1 files transparently.
- `Credential` is a frozen Pydantic model, `extra="forbid"`.

## 5. Per-provider constants (extracted from the 9router registry, 2026-09-16)

Pin as a data table in `registry.py`, with the upstream file path in a
comment for future re-sync. All three use the same PKCE
`authorization_code` shape; `avo` reuses `build_auth_url` /
`exchange_token` / `map_tokens` semantics from
`src/lib/oauth/providers/{claude,codex,gemini-cli}.js`.

| | claude | codex (ChatGPT) | gemini-cli |
|---|---|---|---|
| authorize | `https://claude.ai/oauth/authorize` | `https://auth.openai.com/oauth/authorize` | `https://accounts.google.com/o/oauth2/v2/auth` |
| token | `https://api.anthropic.com/v1/oauth/token` (JSON body) | `https://auth.openai.com/oauth/token` (form) | `https://oauth2.googleapis.com/token` (form) |
| client_id | `9d1c250a-e61b-44d9-88ed-5944d1962f5e` | `app_EMoamEEZ73f0CkXaXp7hrann` | `681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com` |
| client_secret | none (public client) | none (public client) | `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` (open-source CLI constant) |
| scopes | `org:create_api_key user:profile user:inference` | `openid profile email offline_access` | `cloud-platform userinfo.email userinfo.profile` |
| callback | localhost `:43111/auth/callback` | fixed `:1455/auth/callback` (must match Codex CLI) | localhost `:43113/auth/callback` |
| extra auth params | `code=true` | `id_token_add_organizations=true, codex_cli_simplified_flow=true, originator=codex_cli_rs` | none |
| inference endpoint | `https://api.anthropic.com/v1/messages?beta=true` | `https://chatgpt.com/backend-api/codex/responses` (responses API, streaming required) | `https://cloudcode-pa.googleapis.com/v1internal` |
| identity headers | `anthropic-beta: claude-code-20250219,oauth-2025-04-20,...`, `user-agent: claude-cli/<ver> (external, sdk-cli)` | `originator: codex_cli_rs`, `user-agent: codex_cli_rs/<ver>` | `x-goog-api-client: google-genai-sdk/...` |
| token TTL / refresh lead | ~6 h, refresh lead 4 h | access ~5 d, refresh rotates; force re-login past 8 d (`maxRefreshAgeMs`) | Google standard, refresh lead 5 min default |
| account extraction | token response `account.email` | userinfo from id_token claims | `https://www.googleapis.com/oauth2/v1/userinfo` |
| quota probe (S2 input) | `GET https://api.anthropic.com/api/oauth/usage` | `GET https://chatgpt.com/backend-api/wham/usage` | `retrieveUserQuota` / `loadCodeAssist` |

The identity headers are what make the vendors' backends accept a
subscription bearer on inference endpoints — they are also the
"pretending to be the official CLI" part. This is why §9 gates them.

## 6. Login flow (sequence)

1. `avo login claude` → check imports (§8) → if none, generate PKCE pair,
   open browser at authorize URL; headless fallback: print URL, read code
   manually.
2. Localhost callback server captures `?code=` (timeout 300 s, matching
   9router's `OAUTH_TIMEOUT`).
3. Exchange for tokens, extract account email, store via `store.py`.
4. Print `✓ logged in: fqih@example.com (claude, expires 18:04)`.
5. `avo login <provider> --key-stdin` covers the plain-API-key path for
   **every** provider in `build_provider_from_env`'s catalog (fold-in of
   the universal-login design approved earlier).

## 7. Refresh lifecycle

- **Proactive**: before each request, if `now > expires_at - lead` →
  refresh and persist. Lead is per-provider from §5 (Claude 4 h, Codex
  5 d/8 d cap, Gemini 5 min).
- **Reactive**: a `401`/`invalid_grant` triggers exactly one refresh +
  one retry; second failure marks the credential `reauth_required` and
  raises `AuthError` with the command to run (`avo login claude`).
- **Rotation**: if the refresh response carries a new `refresh_token`
  (Codex does), persist immediately — losing a rotated refresh token
  forces a full re-login. Concurrent callers are deduped by a per-store
  asyncio lock so a burst of requests refreshes once.

## 8. Import / reuse existing CLIs

`imports.py` reads, never writes, other tools' credential files and
offers reuse (copy into `auth.json`, then Avo owns its refresh):

- Claude Code: `~/.claude/.credentials.json`
- Codex CLI: `~/.codex/auth.json` (9router's
  `api/oauth/codex/import-token` does the same)

Trigger: `avo login <provider>` detects an importable file and asks
`reuse existing login from Claude Code? [Y/n]` before opening a browser.
Import failures degrade silently to the normal flow.

## 9. Opt-in gate (user decision: explicit, default-off)

- Inference over a **subscription grant** requires
  `AVO_ALLOW_SUBSCRIPTION=1`. Without it: the credential stores fine and
  `avo login --status` lists it, but the provider raises `AuthError`
  pointing at the flag and the docs page.
- API-key paths (including `sk-ant-`, `OPENAI_API_KEY`, openrouter) are
  never gated.
- Docs page `docs/guides/subscription-auth.md` opens with the honest
  warning: unofficial use of subscription backends may violate vendor
  ToS and risk rate limits or account suspension. Reference: 9router
  itself marks all three providers `RISK_NOTICE` in its own registry.

## 10. Provider integration

- `AnthropicProvider` gains `auth_mode: Literal["api_key","oauth"]`
  (default `api_key`). OAuth mode sends `Authorization: Bearer <token>`
  + §5 identity headers instead of `x-api-key`.
- New `CodexSubscriptionProvider` (OpenAI **Responses API** wire format,
  streaming required) and `GeminiCliProvider` (cloudcode-pa
  `v1internal:streamGenerateContent`).
- Credential resolution order at build time:
  `AVO_{NAME}_API_KEY` env > api_key in store > oauth in store
  (subscription gate applies) > error listing `avo login` options.
- Event log/audit keep current behavior: the model id and provider name
  are recorded per request; bearer tokens never enter models.

## 11. Security & redaction

- Store file 0600; directory 0700 (create with mode, assert on write).
- Tokens excluded from `repr`/`str` on `Credential` (Pydantic
  field repr=False), from `avo doctor` output (show only
  `<provider>: oauth ✓ fqih@example.com (expires 18:04)`), and from
  logs — extend the existing deep-secret redaction to
  `refresh_token`/`access_token` shaped strings.
- PKCE verifier: `secrets.token_urlsafe(64)`, S256 (existing
  `generate_pkce_pair`). No `token_param` in URLs for token endpoint
  (POST body only). Callback server binds 127.0.0.1 only and rejects
  missing/mismatched `state`.
- SSRF guard: all OAuth/inference URLs come from the frozen registry
  table, never from user config.

## 12. Adoption & licensing

9router is MIT; we port **flow logic and constants** (its OAuth modules
are thin wrappers over public-protocol code anyway), re-implemented
idiomatically in Python — no vendored JS, no runtime coupling.
`registry.py` header credits 9router + CLIProxyAPI lineage; README
Acknowledgments entry lands with S4.

## 13. Test plan (offline, mocked HTTP — CI never hits a vendor)

- `test_oauth_registry.py`: table shape, frozen constants, all URLs https.
- `test_oauth_flows.py`: fake callback server, PKCE challenge/verifier
  pairing, state mismatch rejected, exchange success + failure, 300 s
  timeout path.
- `test_oauth_refresh.py`: proactive boundary (exactly at lead),
  reactive 401-once-retry, refresh_token rotation persisted, concurrent
  refresh deduped to one call, reauth_required after second failure,
  Codex 8-day cap.
- `test_store_v2.py`: v1 file (strings) + v2 (records) read, write-back
  preserves other entries, 0600 mode, repr redaction.
- `test_auth_imports.py`: fixture credential files for both CLIs;
  reuse prompt yes/no paths.
- `test_subscription_gate.py`: gate off → clear AuthError; on → request
  carries bearer + identity headers (mock transport asserts headers).
- Update `chat_setup` to offer store persistence (Y/n) using the same
  store; default remains non-persistent when declined.

Quality gates unchanged: `ruff`, `mypy src/avo`, `pytest` (coverage ≥ 90%
project gate applies to new modules).

## 14. Rollout & compatibility

1. store v2 + gate + CLI universal key login (no OAuth yet) — usable
   improvement on its own.
2. Claude flow (closest to existing openrouter code) → dogfood.
3. Codex + Gemini flows.
4. Docs page + `avo doctor` status columns.

No breaking change to any existing API: `get_stored_token` keeps its
contract, provider constructors keep defaults, env precedence unchanged.
