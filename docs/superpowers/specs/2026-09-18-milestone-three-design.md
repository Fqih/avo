# Milestone Three: Secure Execution and Extensibility

**Date:** 2026-09-18  
**Status:** Approved for implementation planning  
**Scope:** Existing `feat/token-savers` worktree and Avo's permission,
configuration, sandbox, plugin, and local web-control boundaries

## Goal

Make Avo's mutating and executable capabilities secure-by-default and
consistent across the CLI, chat REPL, local Web UI, plugins, and sandbox
without changing the provider-neutral runtime protocol.

## Why this milestone

Milestone One added provider catalogs, credential backends, and attachments.
Milestone Two added named agents, isolated delegation, and deterministic
replay. The next risk is not another model adapter: it is inconsistent policy
at the boundaries where an agent can write files, execute commands, install
plugins, or mutate a local web-controlled workspace.

## Non-goals

- No new model provider or vendor login flow.
- No rewrite of `AgentRuntime` state transitions.
- No marketplace UI or remote plugin registry.
- No claim that permission-protected files are encrypted.
- No unrestricted remote web service; the dashboard remains loopback-only.
- No automatic approval of agent-generated writes, shell commands, or plugin
  installation.

## Design principles

1. One typed policy path must produce the same answer regardless of whether a
   request originates in environment configuration, CLI flags, the chat REPL,
   a delegated agent, a plugin, or the Web UI.
2. Read-only inspection may be convenient; mutation, execution, and network
   access require an explicit policy boundary.
3. Security failures must be actionable and must fail before the operation
   starts whenever possible.
4. Diagnostics may identify paths, providers, modes, and capabilities, but
   must never print credentials, bearer tokens, cookies, or CSRF material.
5. Every changed boundary gets a focused regression test before implementation
   and an integration test at its public entry point.

## Architecture

### 1. Canonical configuration and permission resolver

Create a typed resolver layer that accepts explicit values from callers and
applies this precedence:

```text
CLI argument > environment variable > project config > user config > safe default
```

The resolver will expose typed values for:

- database and workspace paths;
- `PermissionMode` and forced-approval tool names;
- sandbox requirement, network allowance, and timeout limits;
- plugin installation/activation policy;
- Web UI origin and authentication settings.

Existing helpers remain compatible as wrappers. Invalid values fail with the
canonical allowed values and the source that supplied the invalid setting.
The default permission mode remains `default`; subscription inference remains
explicitly opt-in.

The user root honors XDG locations on Linux while reading the existing
`~/.avo` layout for migration compatibility. Diagnostics show the resolved
source and path, not secret contents.

### 2. Capability-aware execution and sandbox boundary

Classify tools as `read`, `mutate`, `execute`, or `network`. The classification
is metadata owned by the tool registry and is inherited by named child agents.
The permission callback remains the single approval decision point.

`run_shell`, test, and lint execution use the existing sandbox abstraction when
the resolved policy requires it. If sandbox support is unavailable, Avo must
return an actionable error rather than silently executing on the host. A
host-execution escape hatch, if retained for compatibility, requires an
explicit operator policy and must be visible in diagnostics.

Workspace writes enforce resolved containment immediately before replacement,
reject symlink escapes, and use atomic replacement where the existing tool
contract permits it. Child agents cannot widen the parent capability set.

### 3. Plugin trust boundary

Plugin installation becomes a two-phase operation:

1. inspect source metadata and requested entry-point groups/capabilities;
2. install or activate only after an explicit operator confirmation.

The default install path is non-editable unless the user explicitly requests
editable mode. Plugin names and destinations are validated against the user
plugin root. Index updates are atomic and retain source, version, groups,
editable state, and activation state. Agent-generated text cannot implicitly
confirm installation.

Existing plugins remain listable and removable. Activation failures are
isolated and reported without preventing the core CLI from starting.

### 4. Local Web UI control-plane security

The Web UI remains bound to `127.0.0.1` by default. Each process has a random
bearer token; all mutating endpoints require authentication. Browser mutation
requests additionally require an explicit origin match and CSRF token. CORS is
disabled by default and, when enabled, accepts one explicitly configured
origin rather than a wildcard.

Read-only endpoints remain usable for a dashboard session. Mutating endpoints
must require both:

- authenticated request credentials; and
- an explicit confirmation field matching the operation's documented action.

Workspace writes and git operations reuse the same containment and permission
policy as chat tools. Error responses use stable JSON shapes and never echo
authorization headers or tokens.

### 5. Diagnostics, migration, and documentation

`avo doctor` and the Web UI status surface report:

- resolved config source and user root;
- permission mode and whether shell/sandbox execution is required;
- plugin policy and activation state;
- Web UI bind/origin/authentication posture;
- credential backend type without credential values.

Migration documentation distinguishes file permissions from encryption, API-key
providers from subscription OAuth, resume from replay, and sandboxed execution
from host execution. Existing configuration remains readable, while new writes
use the canonical paths and restrictive permissions.

## Failure and compatibility behavior

- Missing optional sandbox dependencies produce a clear remediation message.
- Invalid configuration stops startup before provider inference.
- Unauthorized Web UI mutations return `401`/`403` with no state change.
- Origin or CSRF failures are rejected before parsing mutation payloads.
- Plugin metadata or install failures do not corrupt the plugin index.
- Existing read-only CLI, chat, agent, replay, and provider flows remain
  backward compatible.
- The public runtime provider protocol and `AgentRuntime.run` signature do not
  change in this milestone.

## Verification contract

Focused tests must cover:

- configuration precedence, invalid modes, XDG/migration paths, and redaction;
- capability classification, parent/child policy inheritance, sandbox fallback,
  path containment, symlink rejection, and atomic writes;
- plugin preview, confirmation, non-editable default, atomic index updates,
  activation isolation, and agent non-confirmation;
- Web UI auth, origin, CSRF, CORS, confirmation, stable errors, and read-only
  access;
- CLI/chat/doctor integration and migration documentation examples.

The milestone gate is:

```text
ruff check .
ruff format --check .
python -m mypy src/avo
python -m pytest -q
bandit -r src/avo -c pyproject.toml --severity-level medium
git diff --check
package build
secret scan or documented repository-safe replacement scan
```

The known OAuth callback/refresh tests must be isolated from product failures
when they wait for external authentication state in an offline environment.
