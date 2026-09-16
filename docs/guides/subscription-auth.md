# Subscription OAuth Authentication

> [!WARNING]
> **Risk Notice**: Subscription-backed inference uses unofficial client endpoints
> to reuse your existing Claude Pro/Team, ChatGPT Plus/Team, or Google Gemini CLI
> subscriptions. These endpoints are intended for vendors' first-party CLI tools
> (`claude`, `codex`, `cloudcode`). Using them programmatically carries risks of
> vendor Terms of Service violations, unexpected rate limits, or account
> suspensions. 9router marks all three providers with a `RISK_NOTICE`. Use at your own risk.

Avo supports subscription OAuth authentication so developers can leverage their
existing vendor subscriptions without incurring separate API token costs.

---

## 1. Explicit Opt-In Gate

Because subscription backends carry ToS risks, subscription inference is
**disabled by default**.

To enable subscription inference, you must explicitly opt in by setting the
`AVO_ALLOW_SUBSCRIPTION` environment variable:

```bash
export AVO_ALLOW_SUBSCRIPTION=1
```

If this variable is not set, any attempt to run inference via a subscription
OAuth credential will immediately raise an error pointing to this guide.
Direct API keys (e.g. `AVO_ANTHROPIC_API_KEY`, `AVO_OPENAI_API_KEY`) are never
gated and remain the recommended path for production workloads.

---

## 2. Supported Subscription Providers

| Provider | CLI Command | Backend Endpoint | Identity Header |
|---|---|---|---|
| **Claude (Anthropic)** | `avo login claude` | `https://api.anthropic.com/v1/messages?beta=true` | `anthropic-beta: claude-code-20250219,oauth-2025-04-20` |
| **ChatGPT (Codex)** | `avo login codex` | `https://chatgpt.com/backend-api/codex/responses` | `originator: codex_cli_rs` |
| **Gemini CLI** | `avo login gemini` | `https://cloudcode-pa.googleapis.com/v1internal` | `x-goog-api-client: google-genai-sdk/1.41.0` |

---

## 3. Login Walkthrough

### Logging In via Browser (PKCE Flow)

Run the login command for your desired provider:

```bash
avo login claude
```

1. Avo generates an RFC 7636 PKCE code challenge and starts a temporary local
   callback server.
2. Your default web browser opens to the vendor's authorization page.
3. After granting access, the vendor redirects to `http://localhost:<port>/auth/callback`.
4. Avo exchanges the authorization code for access and refresh tokens, securely
   storing them in `~/.config/avo/auth.json` (with `0600` file permissions).

### Headless / SSH Login

If no browser is available, Avo prints the authorization URL and prompts you to
paste either the full callback redirect URL or the authorization code directly:

```
Open this URL in your browser:
  https://claude.ai/oauth/authorize?...

Paste the callback URL or authorization code:
```

### Reusing Official CLI Logins

If you already use official vendor CLI tools, Avo automatically detects their
existing credentials:
- **Claude Code**: `~/.claude/.credentials.json`
- **OpenAI Codex CLI**: `~/.codex/auth.json`

When running `avo login claude` or `avo login codex`, Avo prompts:
```
Found existing Claude Code credentials for user@example.com. Reuse? [Y/n]:
```
Answering `Y` imports the tokens immediately without needing a browser flow.

---

## 4. Universal API Key Login

You can also store API keys securely in `auth.json` without setting environment variables:

```bash
echo "$ANTHROPIC_API_KEY" | avo login anthropic --key-stdin
echo "$OPENAI_API_KEY" | avo login openai --key-stdin
```

---

## 5. Token Lifecycle & Auto-Refresh

Avo handles the token lifecycle automatically:
- **Proactive Refresh**: Before each request, Avo checks if the token is close
  to expiring (within a provider-specific lead window: 4 hours for Claude, 5 days
  for Codex, 5 minutes for Gemini) and refreshes it in the background.
- **Reactive 401 Recovery**: If a request fails with HTTP 401, Avo forces an
  immediate token refresh and retries the request once.
- **Refresh Token Rotation**: Vendors that rotate refresh tokens on every renewal
  (like Codex) have their new refresh tokens persisted immediately.
- **Concurrent Deduplication**: Multiple parallel tool calls share a single
  in-memory lock so a burst of requests only triggers one refresh call.

---

## 6. Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| `subscription-backed inference is disabled by default` | `AVO_ALLOW_SUBSCRIPTION` is not set | Run `export AVO_ALLOW_SUBSCRIPTION=1` |
| `reauth required: run avo login <provider>` | Refresh token expired or revoked | Re-authenticate by running `avo login <provider>` |
| `Codex session expired past max age` | Codex 8-day maximum session limit reached | Run `avo login codex` to start a new session |
| `Port 1455 already in use` | Another process is using Codex callback port | Stop conflicting local servers and retry `avo login codex` |
| Tokens not found | `AVO_CONFIG_DIR` pointing to empty directory | Ensure `~/.config/avo/auth.json` exists or re-run login |
