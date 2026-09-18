# Milestone Three: Secure Execution and Extensibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Avo's mutating and executable capabilities secure-by-default and consistent across CLI, chat, delegated agents, plugins, sandbox execution, and the local Web UI.

**Architecture:** Add a typed configuration/policy resolver at the boundary, extend tool metadata with capability classifications, and make sandbox/plugin/web mutation paths consume those resolved policies. Preserve the existing `AgentRuntime`, provider protocol, CLI compatibility, and loopback-only Web UI while making unsafe operations explicit and auditable.

**Tech Stack:** Python 3.11+, Pydantic 2, standard-library `http.server`, existing Docker sandbox adapter, SQLite, pytest/pytest-asyncio, ruff, mypy, bandit.

**Spec:** `docs/superpowers/specs/2026-09-18-milestone-three-design.md`

## Global Constraints

- Preserve Python 3.11, 3.12, and 3.13 support.
- Do not add a required runtime dependency.
- Do not modify the user's untracked `.avo/` directory.
- Write and run a failing test before each production behavior change.
- Keep the default permission mode `default` and subscription inference explicitly opt-in.
- Never print credentials, bearer tokens, cookies, CSRF tokens, or raw authorization headers.
- Keep the Web UI loopback-only unless an explicit existing server boundary says otherwise.
- Do not change `AgentRuntime.run` or the provider protocol signatures.
- Every plugin install or activation requires explicit operator confirmation and cannot be confirmed by agent-generated text.
- Do not push during this milestone unless the user separately requests it.

---

### Task 1: Canonical configuration and permission resolution

**Files:**
- Create: `src/avo/config_resolver.py`
- Modify: `src/avo/config.py`, `src/avo/permissions.py`, `src/avo/doctor.py`, `src/avo/cli_setup.py`
- Test: `tests/test_config_resolver.py`, `tests/test_permissions.py`, `tests/test_doctor.py`

**Interfaces:**
- `ConfigSource(StrEnum)` with `CLI`, `ENVIRONMENT`, `PROJECT`, `USER`, and `DEFAULT`.
- Frozen `ResolvedValue[T]` containing `value`, `source`, and `key`.
- `AvoSecurityConfig` containing `permission_mode`, `require_approval`, `sandbox_required`, `sandbox_network`, `sandbox_timeout_seconds`, `plugin_editable`, `plugin_activation`, `web_allowed_origin`, and `web_cors_enabled`.
- `resolve_security_config(*, explicit: Mapping[str, object] | None = None, environ: Mapping[str, str] | None = None, workspace_root: Path | None = None, user_root: Path | None = None) -> AvoSecurityConfig`.
- `parse_permission_mode(value: object, *, source: str) -> PermissionMode` remains the single user-facing parser used by CLI, chat, and web paths.

- [x] **Step 1: Write failing resolver tests.**

  Cover precedence `explicit > environment > project > user > default`, invalid permission values, invalid booleans/timeouts, project config containment, XDG user-root selection, and redacted diagnostic rendering.

- [x] **Step 2: Run resolver tests and verify RED.**

  Run: `python -m pytest -q tests/test_config_resolver.py`

  Expected: import/API failures because the resolver module does not exist.

- [x] **Step 3: Implement typed resolution with compatibility wrappers.**

  Read `.avo/config.toml` or the existing project config format if present, then the user config, and apply explicit/environment values last. Keep current `resolve_database_path`, `permission_policy_from_env`, and setup APIs working by delegating to the resolver.

- [x] **Step 4: Add doctor/setup integration tests and implementation.**

  Ensure `avo doctor` reports the mode/source/backend posture without values that look like credentials, and setup writes canonical permission values.

- [x] **Step 5: Run focused tests and commit.**

  Run: `python -m pytest -q tests/test_config_resolver.py tests/test_permissions.py tests/test_doctor.py`

  Commit: `feat: centralize security configuration resolution`

### Task 2: Tool capabilities and child policy inheritance

**Files:**
- Create: `src/avo/capabilities.py`
- Modify: `src/avo/tools.py`, `src/avo/delegation.py`, `src/avo/app_tools/task_tool.py`, `src/avo/chat.py`
- Test: `tests/test_capabilities.py`, `tests/test_delegation.py`, `tests/test_task_tool.py`

**Interfaces:**
- `ToolCapability(StrEnum)` with `READ`, `MUTATE`, `EXECUTE`, and `NETWORK`.
- `ToolMetadata` gains a backward-compatible `capability` field defaulting to `READ`.
- `classify_tool(name: str) -> ToolCapability` maps existing tools deterministically.
- `filter_tools(parent_tools, *, maximum: ToolCapability | None = None, read_only: bool = False) -> list[Tool]` never widens the parent set.
- `inherit_policy(parent: AvoSecurityConfig, child_capability: AgentCapability) -> AvoSecurityConfig` returns a restricted child policy.

- [x] **Step 1: Write failing capability tests.**

  Assert all built-in tools receive a stable capability, unknown tools default to `READ` rather than execute, read-only agents cannot advertise mutating/executable/network tools, and child policy cannot become less restrictive than its parent.

- [x] **Step 2: Run tests and verify RED.**

  Run: `python -m pytest -q tests/test_capabilities.py tests/test_delegation.py tests/test_task_tool.py`

- [x] **Step 3: Implement capability metadata and filtering.**

  Keep existing `ToolMetadata` construction valid for third-party tools. Use the capability helper in named delegation and the legacy `task` tool so both paths enforce the same boundary.

- [x] **Step 4: Run focused tests and commit.**

  Run: `python -m pytest -q tests/test_capabilities.py tests/test_delegation.py tests/test_task_tool.py`

  Commit: `feat: enforce capability boundaries for child agents`

### Task 3: Sandbox and workspace execution hardening

**Files:**
- Modify: `src/avo/app_tools/shell_tool.py`, `src/avo/app_tools/file_tools.py`, `src/avo/app_tools/git_tools.py`, `src/avo/app_tools/workspace_tools.py`, `src/avo/sandbox.py`, `src/avo/chat_workspace_commands.py`
- Test: `tests/test_sandbox_policy.py`, `tests/test_file_tools.py`, `tests/test_chat_repl.py`

**Interfaces:**
- `ExecutionPolicy` resolved from `AvoSecurityConfig` with `sandbox_required`, `network_enabled`, and timeout fields.
- `require_execution_policy(policy, *, sandbox_available: bool, operation: str) -> None` raises an actionable `AvoError` before host execution when sandbox is required but unavailable.
- `assert_workspace_target(root: Path, target: Path, *, follow_symlinks: bool = False) -> Path` performs containment and final pre-write validation.

- [x] **Step 1: Write failing sandbox/path tests.**

  Cover required-sandbox rejection, explicit host policy, network-disabled Docker requests, symlink escapes, replacement targets changing between validation and write, and bounded command timeouts.

- [x] **Step 2: Run tests and verify RED.**

  Run: `python -m pytest -q tests/test_sandbox_policy.py tests/test_file_tools.py`

- [x] **Step 3: Implement policy checks and final containment.**

  Route shell/test/lint paths through the existing sandbox adapter when required. Preserve current read-only behavior and make missing optional Docker support fail with an install/remediation message rather than falling back silently.

- [x] **Step 4: Add chat integration coverage.**

  Verify `/shell`, `/test`, `/lint`, and agent child execution use the same policy and render actionable errors without tracebacks or secret values.

- [x] **Step 5: Run focused tests and commit.**

  Run: `python -m pytest -q tests/test_sandbox_policy.py tests/test_file_tools.py tests/test_chat_repl.py`

  Commit: `feat: harden sandbox and workspace execution boundaries`

### Task 4: Plugin preview, confirmation, and activation isolation

**Files:**
- Modify: `src/avo/cli_plugins.py`, `src/avo/cli.py`, `src/avo/chat_commands.py`, `src/avo/cli_setup.py`
- Create: `src/avo/plugin_policy.py`
- Test: `tests/test_plugin_policy.py`, `tests/test_cli_plugins.py`, `tests/test_chat_repl.py`

**Interfaces:**
- Frozen `PluginManifest(name, version, description, groups, source, editable)`.
- `inspect_plugin_source(source: str, *, name: str | None = None) -> PluginManifest` performs metadata-only inspection.
- `confirm_plugin_action(action: str, *, confirmation: str | None, interactive: bool) -> bool` requires exact operator confirmation for install/activate.
- `PluginPolicy(editable_allowed: bool = False, activation_allowed: bool = False)`.
- `install(..., confirm: bool = False)` defaults to non-editable and refuses mutation without explicit confirmation.

- [x] **Step 1: Write failing plugin trust tests.**

  Assert metadata inspection is read-only, install preview includes source/groups/version, default editable mode is false, missing or incorrect confirmation performs no clone/pip/index write, index replacement is atomic, and one broken plugin does not prevent core startup.

- [x] **Step 2: Run tests and verify RED.**

  Run: `python -m pytest -q tests/test_plugin_policy.py tests/test_cli_plugins.py`

- [x] **Step 3: Implement policy and atomic index writes.**

  Keep legacy list/show/remove behavior. Add explicit `--confirm`/interactive confirmation at the CLI boundary and reject confirmation values originating from model/agent text. Store groups and activation state in the index without credentials.

- [x] **Step 4: Add chat/plugin command coverage.**

  Ensure `/plugin install` shows a preview and stops for confirmation; agent-delegated prompts cannot invoke install implicitly.

- [x] **Step 5: Run focused tests and commit.**

  Run: `python -m pytest -q tests/test_plugin_policy.py tests/test_cli_plugins.py tests/test_chat_repl.py`

  Commit: `feat: add explicit plugin trust boundaries`

### Task 5: Web UI authentication, origin, CSRF, and mutation confirmation

**Files:**
- Modify: `src/avo/web_ui.py`, `src/avo/web_http.py`, `src/avo/web_api.py`, `src/avo/web_workspace.py`, `src/avo/web_playground.py`
- Test: `tests/test_web_security.py`, `tests/test_web_ui.py`

**Interfaces:**
- `WebSecurityConfig(allowed_origin: str | None, cors_enabled: bool, require_confirmation: bool)`.
- `authenticate_mutation(headers, *, expected_token, csrf_token, allowed_origin, confirmation) -> None` raises a stable HTTP error before mutation parsing.
- `_send_json` and mutation errors use `{"error": {"code": str, "message": str}}` without echoing secrets.

- [x] **Step 1: Write failing web security tests.**

  Cover read-only GET access, missing/wrong bearer token (`401`), wrong/missing origin (`403`), missing/wrong CSRF (`403`), wildcard CORS rejection, missing confirmation, and successful authenticated mutation.

- [x] **Step 2: Run tests and verify RED.**

  Run: `python -m pytest -q tests/test_web_security.py tests/test_web_ui.py`

- [x] **Step 3: Implement one mutation gate before route dispatch.**

  Keep the per-process random token and loopback bind. Apply origin/CSRF checks only to browser mutations, require bearer authentication for all mutations, and require exact confirmation for workspace/git/provider changes. Preserve the dashboard's read-only routes.

- [x] **Step 4: Add integration coverage for workspace and playground writes.**

  Assert rejected requests leave SQLite, files, git state, and provider settings unchanged.

- [x] **Step 5: Run focused tests and commit.**

  Run: `python -m pytest -q tests/test_web_security.py tests/test_web_ui.py`

  Commit: `feat: secure local web control-plane mutations`

### Task 6: Diagnostics, migration, and public documentation

**Files:**
- Modify: `src/avo/doctor.py`, `src/avo/cli.py`, `README.md`, `docs/cli.md`, `docs/api-stability.md`, `docs/migrations/`
- Test: `tests/test_doctor.py`, `tests/test_cli_help.py`, `tests/test_config_resolver.py`

- [x] **Step 1: Write failing diagnostics/documentation tests.**

  Assert doctor output contains resolved config source, permission mode, sandbox posture, plugin policy, web origin posture, and credential backend type while excluding token-shaped values. Assert help and migration docs distinguish resume/replay, sandbox/host execution, OAuth/API keys, and permission protection/encryption.

- [x] **Step 2: Implement diagnostics and migration guidance.**

  Reuse typed resolver output; do not duplicate environment parsing in doctor or docs examples. Add a `0.7.x-to-milestone-three` migration note with rollback-safe configuration changes.

- [x] **Step 3: Run focused tests and commit.**

  Run: `python -m pytest -q tests/test_doctor.py tests/test_cli_help.py tests/test_config_resolver.py`

  Commit: `docs: document milestone three security posture`

### Task 7: Milestone verification and release checkpoint

**Files:**
- Modify only files required by failing checks.
- Update: `CHANGELOG.md`, `docs/superpowers/plans/2026-09-18-milestone-three.md`

- [ ] **Step 1: Run the Milestone 3 focused suite.**

  Run: `python -m pytest -q tests/test_config_resolver.py tests/test_capabilities.py tests/test_sandbox_policy.py tests/test_plugin_policy.py tests/test_web_security.py tests/test_doctor.py tests/test_delegation.py tests/test_chat_repl.py`

- [ ] **Step 2: Run repository quality gates.**

  Run: `ruff check . && ruff format --check . && python -m mypy src/avo && bandit -r src/avo -c pyproject.toml --severity-level medium && git diff --check`

- [ ] **Step 3: Run the full suite with a bounded timeout.**

  Run: `timeout 90s python -m pytest -q`

  Report OAuth callback/refresh waits separately from product assertion failures if the offline environment cannot complete them.

- [ ] **Step 4: Run build and repository-safe secret scan.**

  Run: `python -m build` and scan tracked source/docs for credential-shaped literals, excluding existing redaction fixtures with an explicit report.

- [ ] **Step 5: Review status, changelog, and commits.**

  Confirm `.avo/` remains untracked and untouched, no secrets were added, the public runtime/provider protocols are unchanged, and all completed plan steps are checked.
