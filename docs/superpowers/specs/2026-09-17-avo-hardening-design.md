# Avo Reliability and Security Hardening Design

**Date:** 2026-09-17  
**Status:** Approved for implementation planning  
**Scope:** Existing `feat/token-savers` worktree and the surrounding Avo runtime

## Goal

Make Avo safe to run, internally consistent, and honest about its guarantees before adding more provider, UI, OAuth, or optimization features.

The completed work must preserve the existing runtime strengths while fixing the known P0/P1 defects: broken setup output, unsafe permission defaults, unauthenticated mutating web endpoints, subscription OAuth default-on behavior, fragmented configuration, unsandboxed execution paths, incomplete token-saver integration, and documentation drift.

## Constraints

- Preserve all existing user changes in the current worktree.
- Do not add a runtime dependency unless the existing standard library and project dependencies cannot provide the behavior.
- Maintain Python 3.11, 3.12, and 3.13 support.
- Keep provider-specific credentials and subscription OAuth opt-in and clearly separated from the core runtime.
- Default behavior must require approval for mutating or executable actions.
- Every production behavior change must have a regression test written and observed failing before implementation.
- No raw `git push`; if pushing is requested later, use `~/.local/bin/git-push-notify` and wait for its exit status.

## Design

### 1. Permission and first-run configuration

Use the canonical permission values from `PermissionMode`: `default`, `accept_edits`, `plan`, and `bypass_permissions`. The setup wizard must emit the canonical value and choose `default` unless the user explicitly selects a less restrictive mode.

Centralize permission parsing and user-facing validation so config files, environment variables, CLI flags, chat commands, and the web API use the same conversion path. Invalid configuration should produce an actionable error before the runtime starts.

### 2. Configuration resolution

Introduce one resolver for database path and user configuration paths with the precedence:

```text
CLI argument > environment variable > project config > user config > safe default
```

The resolver must expose typed values to CLI, chat, web, OAuth, plugins, and token savers. Existing public helper functions remain compatible where practical, but direct `Path("avo.db")` defaults in entry points must be removed.

Use one documented user root, honoring XDG on Linux while retaining a migration-compatible lookup for existing `~/.avo` installations. Auth, combos, plugins, personas, skills, and event databases must report their resolved paths through diagnostics.

### 3. Web control-plane security

The local web server remains loopback-only, but gains a random per-process bearer token. The token must be required for all state-changing endpoints and must not be included in wildcard CORS behavior. CORS is disabled by default; if enabled, only an explicitly configured origin is accepted. `Origin` validation and a CSRF token are required for browser-based mutation requests.

Web APIs are divided into read-only and mutating capabilities. Read-only dashboard access is the default. File writes, permission changes, provider changes, and git operations require an explicit confirmation field and are rejected unless the request is authenticated.

Workspace writes use validated containment, no-follow semantics where supported, atomic replacement, and a final containment check immediately before replacement.

### 4. Subscription OAuth safety

Subscription-backed OAuth is disabled by default. It is enabled only through an explicit configuration value and an explicit user acknowledgement in the setup flow. Documentation must state that provider terms, account eligibility, revocation, and billing are external constraints.

Credential files are described accurately as permission-protected files unless an actual platform keyring or encryption layer is implemented. Existing credentials remain readable through the migration path, but newly saved credentials must preserve restrictive file permissions.

### 5. Execution and plugin trust boundaries

Tools are classified as read, mutate, execute, or network. Direct host execution of tests and linter commands is either routed through the existing sandbox abstraction or requires an explicit execution policy. Git operations and hooks are treated as mutating execution.

Plugin installation is explicit and non-editable by default. Before installation, Avo shows the source, requested capabilities, and whether installation executes a build backend. Plugin activation remains disabled until the user confirms. The implementation must not silently install or activate plugins from an agent-generated request.

### 6. Token-saver integration

Token savers become an opt-in provider wrapper configured through the same resolver as other runtime settings. The feature must expose a stable factory, CLI/config entry point, and event/trace accounting. A saver transformation is applied only when its replacement is measurably smaller than the original content under the configured token estimator.

Streaming behavior must preserve the `ModelChunk.text` contract. If the inner provider emits partial output and then fails, the wrapper reports an interruption rather than pretending that a transparent failover occurred.

### 7. Runtime guarantee and documentation corrections

Documentation will distinguish:

- checkpoint/resume from deterministic replay;
- pre-output failover from mid-stream failover;
- file permission protection from encryption;
- optional subscription connectors from ordinary API-key providers.

Replay support is described as durable execution unless provider responses are explicitly recorded. A later replay mode may record provider responses and side-effect results, but it is outside this hardening pass.

## Validation strategy

Each subsystem gets focused regression tests first, then the relevant existing suite. The final gate is:

- `python -m ruff check .`
- `python -m ruff format --check .`
- `python -m mypy src/avo`
- `python -m pytest -q`
- `bandit -r src/avo -c pyproject.toml --severity-level medium`
- project secret scan or an explicitly documented replacement scan
- package build and `git diff --check`

Socket-dependent web tests must run in an environment that permits loopback sockets; if the current sandbox blocks sockets, that limitation is reported separately from product failures.

## Out of scope for this pass

- Adding new providers.
- Rewriting the runtime state machine.
- Building a marketplace UI.
- Encrypting credentials without selecting and validating a platform keyring strategy.
- Claiming deterministic replay without recorded provider responses.
- Performance optimization unrelated to measured token-saver behavior.
