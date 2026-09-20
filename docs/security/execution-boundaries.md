# Avo execution boundaries

Avo treats model-controlled actions as untrusted requests. The runtime, tool
factories, and child-agent delegation must all preserve the same boundary;
calling a tool through a different entry point must not silently make it more
privileged.

## Defaults

Unless an operator explicitly changes them, a chat session uses:

- `permission_mode=default`: tool calls require operator approval.
- `sandbox_required=true`: command execution runs through the Docker sandbox.
- `sandbox_network=false`: sandboxed commands have no network access.
- plugin editing and plugin activation disabled.
- credentials are not copied into model-controlled subprocess environments.

The default `AgentRuntime` approval callback is deny-by-default for mutating,
execution, network, and unknown tools. Read-only tools remain available for
inspection. Chat wiring may provide an interactive approval callback, but child
runtimes inherit that callback rather than creating a permissive replacement.

## Tool capabilities

Every built-in tool is classified as one of:

- `read`: inspect workspace state; examples include `read_file`, `grep`, and
  `git_diff`.
- `mutate`: change workspace or repository state; examples include
  `write_file`, `edit_file`, and `git_commit`.
- `execute`: run code or commands; examples include `run_terminal`,
  `test_runner`, and `lint`.
- `network`: make external requests; examples include `web_fetch` and
  `web_search`.

`accept_edits` can auto-approve the existing edit tools, but execution and
network tools still require approval. `bypass_permissions` is the only mode
that removes that approval gate and should be treated as an explicit operator
choice.

## Child agents and delegation

Delegated agents receive a separate event store and a narrowed copy of the
parent security configuration. An inherited coding agent cannot weaken the
parent policy. A read-only agent additionally receives:

- sandboxing forced on;
- network access forced off;
- plugin editing and activation forced off; and
- only read-capability tools.

Parallel delegation is bounded by the coordinator's concurrency limit. Child
approval uses the parent's effective callback, so a child cannot bypass a
parent's approval policy by constructing a new runtime.

## Workspace paths and subprocesses

File, lint, and test targets are resolved with `Workspace.validate_path`.
Paths must exist when the tool requires an existing target, remain below the
workspace root, and may not escape through symlinks. Pytest node selectors are
preserved only after their path component has been validated, for example:
`tests/test_runtime.py::test_one`.

Subprocess environments are filtered through `build_safe_environment`; API
keys, OAuth tokens, cookies, passwords, and other credential-like variables
are not forwarded. Direct use of `run_terminal_tool()` also gets a secure
resolved default. Host execution requires an explicit policy with
`sandbox_required=false` and should only be enabled by the operator.

The Docker sandbox uses a workspace-only writable mount, a read-only
container filesystem, dropped Linux capabilities, a non-root user, bounded
memory/CPU/PID resources, and no network by default. A timed-out container is
stopped best-effort before forced removal.

## Public web fetching

`web_fetch` accepts only HTTP(S) URLs without URL credentials. The hostname is
resolved before the request, and every resolved address must be public. Avo
rejects loopback, private, link-local, multicast, unspecified, reserved, and
IPv4-mapped private destinations. Redirects are followed manually with a
maximum hop count; every `Location` target is validated again before a new
request. HTTP client proxy environment variables are disabled for this tool.

Response bodies are bounded by the requested maximum byte count. Errors avoid
including resolved addresses or URL credentials.

## Global configuration isolation

Global setup and login paths are resolved at call time. `AVO_CONFIG_DIR`, when
set, is used for routing config and credential storage; otherwise Avo falls
back to the current user's `~/.avo` setup directory. Importing Avo before a
test or embedding application changes its home directory no longer freezes
the old path in production reads/writes.

## Verification

Offline boundary checks can be run without Docker or provider credentials:

```bash
python -m pytest -q \
  tests/test_policy_inheritance.py tests/test_permissions.py \
  tests/test_execution_targets.py tests/test_web_fetch.py \
  tests/test_web_fetch_security.py tests/test_web_security.py \
  tests/test_sandbox.py tests/test_cli_sandbox.py \
  tests/test_auth_login_cli.py tests/test_cli_setup.py \
  tests/test_task_tool.py tests/test_chat_repl.py
```

Live provider and real Docker tests remain opt-in. Do not enable them in CI
unless the environment is intentionally provisioned for those external
services.
