# Provider Login, Accounts, and Quotas

> [!WARNING]
> **Risk Notice**: Account-backed inference uses vendor OAuth endpoints intended
> for first-party CLI tools. These endpoints may change and vendor terms or
> account eligibility apply. Avo does not collect vendor passwords.
>
> Free and paid accounts may expose different models, request limits, or terminal
> eligibility. Avo cannot upgrade an account or bypass a quota.

Avo supports browser-based vendor login and ordinary API keys. Choose the path
that matches the account and quota you actually have.

---

## 1. Explicit Opt-In Gate

Because vendor-account backends carry ToS and eligibility risks, account-backed
inference is **disabled by default**.

To enable account-backed inference, you must explicitly opt in by setting the
`AVO_ALLOW_SUBSCRIPTION` environment variable to `1`, `true`, `yes`, or `on`
(case-insensitive):

```bash
export AVO_ALLOW_SUBSCRIPTION=1
```

If this variable is unset, false, empty, or unrecognized, any attempt to run
inference via a vendor OAuth credential will immediately raise an error
instructing you to set `AVO_ALLOW_SUBSCRIPTION=1`.
Direct API keys (e.g. `AVO_ANTHROPIC_API_KEY`, `AVO_OPENAI_API_KEY`) are never
gated and remain the recommended path for production workloads.

---

## 2. Supported Account Providers

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
4. Avo exchanges the authorization code for access and refresh tokens and stores
   them as plaintext JSON in `~/.config/avo/auth.json`, restricted to `0600` file
  permissions. Avo does not encrypt this file.

The first-run `avo` wizard uses this same flow. It opens the official vendor
login; there is no Avo-hosted login page. To configure several providers for a
combo, run:

```bash
avo combo auth coder
```

Each missing vendor is opened once, sequentially. Existing credentials are
skipped, and local Ollama never asks for a login.

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

## 4. Ollama Local and Cloud

These are separate paths:

```bash
# Local: inspect hardware and installed models
avo models ollama list
avo models ollama recommend

# Local downloads always ask for confirmation
avo models ollama pull qwen2.5-coder:7b

# Cloud: remote models and account quota; no local download
avo login ollama-cloud --key-stdin
avo models ollama cloud list
avo models ollama cloud health
avo models ollama cloud usage
```

The recommendation is advisory and based on local CPU, RAM, GPU memory when
available, and free disk space. Avo never silently downloads a multi-gigabyte
model.

Cloud discovery is read-only. `cloud list` reads the models exposed by the
account, `health` checks the remote endpoint, and `usage` displays quota
metadata only when the account exposes that optional endpoint. Avo never
calls the local `/api/pull` flow for Cloud models.

## 5. Universal API Key Login

You can also store API keys as plaintext in the permission-restricted `auth.json`
file without setting environment variables:

```bash
echo "$ANTHROPIC_API_KEY" | avo login anthropic --key-stdin
echo "$OPENAI_API_KEY" | avo login openai --key-stdin
```

---

## 6. Token Lifecycle & Auto-Refresh

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

## 7. Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| `subscription-backed inference is disabled by default` | OAuth inference is not explicitly enabled | Run `export AVO_ALLOW_SUBSCRIPTION=1`, or use the provider's API key path |
| `reauth required: run avo login <provider>` | Refresh token expired or revoked | Re-authenticate by running `avo login <provider>` |
| `Codex session expired past max age` | Codex 8-day maximum session limit reached | Run `avo login codex` to start a new session |
| `Port 1455 already in use` | Another process is using Codex callback port | Stop conflicting local servers and retry `avo login codex` |
| Tokens not found | `AVO_CONFIG_DIR` pointing to empty directory | Ensure `~/.config/avo/auth.json` exists or re-run login |
