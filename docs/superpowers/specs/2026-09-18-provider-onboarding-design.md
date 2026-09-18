# Avo Provider Onboarding, Model Discovery, and Default Chat

## Status

Proposed design for review before implementation.

## Goal

Make first-run Avo usable without manual `AVO_*` environment configuration:

- `avo` with no subcommand starts the interactive chat.
- Provider setup is grouped into local, cloud, and vendor-account paths.
- Claude, ChatGPT/Codex, and Gemini login opens the official vendor browser flow
  and returns through the existing localhost OAuth callback.
- Free and paid vendor accounts are treated as account-dependent access. Avo
  must not claim that a subscription is required or bypass vendor quotas.
- Ollama Local and Ollama Cloud are separate choices.
- Ollama Local detects available hardware, recommends models, asks for explicit
  download confirmation, and shows pull progress.
- Ollama Cloud supports the official sign-in/API-key path without pretending
  cloud usage is equivalent to free local inference.

## User-facing flow

`avo setup` and the first-run path in `avo chat` share one provider onboarding
flow. Existing credentials and global configuration are shown first. The user
then chooses one of:

1. Ollama Local
2. Ollama Cloud
3. Claude account
4. ChatGPT/Codex account
5. Gemini account
6. API provider
7. Router / fallback

Vendor-account choices invoke the existing `avo login <provider>` flow. The
browser opens at the vendor's official authorization page; Avo never asks for
the user's vendor password. The callback server only accepts the expected
localhost path and OAuth state. After login, Avo reports that the account is
connected, but does not infer or promise a paid plan. A vendor rejection, quota
error, or unsupported model is rendered as an actionable provider error.

The existing `AVO_ALLOW_SUBSCRIPTION` setting remains compatible, but its UI
copy must explain that it opts into vendor OAuth-backed inference, not that it
proves the user has a paid subscription.

All non-secret selections are persisted through `~/.avo/config.json`. Tokens
and API keys remain in `~/.config/avo/auth.json` with restrictive permissions.
Secrets must not be placed in shell rc files, logs, doctor output, or event
payloads.

## Account and access semantics

Provider catalog entries gain an access description rather than a binary
"subscription required" label:

- ChatGPT/Codex: free or paid account, subject to the account's current Codex
  allowance and available models.
- Gemini CLI: Google account OAuth, with free or paid quota determined by
  Google; Gemini API key remains a separate path.
- Claude: vendor account OAuth; Claude Code access may require an eligible plan
  or API route, so free web access must not be presented as guaranteed terminal
  access.
- Ollama Local: no account required.
- Ollama Cloud: official Ollama account/API key and cloud usage limits.

Avo only uses the access method the vendor exposes. It never retries around
`401`, `403`, or `429` errors indefinitely and never attempts to evade quota or
plan restrictions.

## Ollama Local model manager

Add a provider-specific model manager used by onboarding and available from a
stable CLI command (recommended command: `avo models ollama`). It will:

1. Detect the Ollama daemon and resolve the configured base URL.
2. Read installed models from `/api/tags`.
3. Gather local capability signals without uploading them: CPU cores,
   available RAM, detected GPU/vendor memory when safely available, and free
   disk space.
4. Match those signals against a curated model catalog containing model name,
   purpose, approximate download size, recommended RAM/VRAM, tool-calling
   suitability, and context guidance.
5. Show installed models separately from recommended models.
6. Require explicit confirmation displaying model name, size, and a storage
   warning before calling Ollama's pull endpoint.
7. Stream pull progress, support cancellation, and re-check `/api/tags` after
   completion.

Hardware detection is advisory. It must never block a user from selecting a
model manually, and it must degrade gracefully when GPU tools or platform
metadata are unavailable. Cloud model names are never passed to the local pull
flow unless Ollama identifies them as cloud models.

## Ollama Cloud

Ollama Cloud is represented separately from local Ollama. The integration will
use the official Ollama sign-in/API surface available in the installed Ollama
version. API keys/device keys are entered through a hidden prompt or an
existing `--key-stdin` path; passwords are never collected. Cloud models are
listed as remote models and are not treated as local downloads. The UI labels
cloud usage and account requirements clearly.

## CLI behavior

The top-level parser changes its no-argument behavior:

- `avo` behaves as `avo chat`.
- `avo --version`, `avo -h`, and explicit operational subcommands retain their
  current meanings.
- Explicit `avo chat` remains supported.
- Non-chat commands such as `avo doctor`, `avo login`, `avo setup`, `avo runs`,
  and `avo ui` are not shadowed by the default.
- Chat flags (`--database`, `--workspace-root`, `--session`, `--new-session`)
  work with both `avo` and `avo chat`.

The help text must show the short form prominently:

```text
avo                 Start chat
avo chat            Start chat explicitly
```

## Doctor and diagnostics

`avo doctor` remains non-destructive and does not make model inference calls.
It should report:

- selected provider and model;
- auth method and connected account, without secrets;
- whether the OAuth opt-in is enabled when applicable;
- Ollama daemon reachability and selected local model readiness;
- a clear distinction between local, cloud, API-key, and vendor-account
  inference.

## Testing strategy

Unit tests will cover:

- no-argument CLI dispatch versus `--version`, help, and explicit commands;
- provider catalog labels and account-dependent access copy;
- OAuth browser invocation and callback delegation using injected fakes;
- hardware detection with platform command failures and partial data;
- model recommendation boundaries for RAM, VRAM, and disk;
- Ollama tags parsing, pull confirmation, progress parsing, cancellation, and
  post-pull verification;
- cloud/API-key secret redaction and configuration persistence.

Existing provider contract tests remain offline. Live tests remain opt-in and
must never require credentials in CI. Manual acceptance tests will cover one
browser login for each vendor account path, one Ollama Local pull, one Ollama
Cloud request, and the short `avo` command.

## Non-goals

- Avo will not build or host a vendor login page.
- Avo will not scrape vendor web pages to determine plan status.
- Avo will not silently download multi-gigabyte models.
- Avo will not promise that every free account can access every model.
- Avo will not replace the official Ollama client or vendor CLI tools.
