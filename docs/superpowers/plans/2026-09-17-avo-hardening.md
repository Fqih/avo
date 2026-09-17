# Avo Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Avo runtime safe, consistent, and honest while preserving its current user changes and completing the token-saver branch.

**Architecture:** Fix behavior at its source with shared resolvers and boundary helpers rather than adding endpoint-specific workarounds. Keep the runtime core stable; harden entry points, web transport, OAuth policy, host execution, plugins, and token-saver factory integration in separate tested slices.

**Tech Stack:** Python 3.11–3.13, Pydantic, standard-library HTTP server, SQLite, pytest, Ruff, mypy, Bandit.

**Spec:** `docs/superpowers/specs/2026-09-17-avo-hardening-design.md`

## Global Constraints

- Preserve all existing user changes in the current worktree.
- Do not add a runtime dependency unless the existing standard library and project dependencies cannot provide the behavior.
- Maintain Python 3.11, 3.12, and 3.13 support.
- Default behavior must require approval for mutating or executable actions.
- Every production behavior change must have a regression test written and observed failing before implementation.
- No raw `git push`; if pushing is requested later, use `~/.local/bin/git-push-notify` and wait for its exit status.

---

### Task 1: Canonical permission setup and first-run behavior

**Files:**
- Modify: `src/avo/cli_setup.py`, `src/avo/permissions.py`, `src/avo/chat.py`, `src/avo/chat_commands.py`
- Test: `tests/test_cli_setup.py`, `tests/test_permissions.py`, `tests/test_chat_repl.py`

**Interfaces:** `load_global_avo_config()` emits canonical `AVO_PERMISSION_MODE`; `permission_policy_from_env()` accepts only canonical values plus the migration alias `bypass`; setup defaults to `default`.

- [ ] Write a test proving generated setup config uses `permission_mode == "default"` and loads without `ValueError`.
- [ ] Write a test proving legacy `permission_mode == "bypass"` normalizes to `bypass_permissions` only when explicitly present.
- [ ] Write a test proving `/setup` can update environment without raising `NameError`.
- [ ] Run the focused tests and observe the expected failures.
- [ ] Implement canonical defaults, alias normalization, and the missing import.
- [ ] Run the focused tests and the existing permission/chat tests.
- [ ] Commit `fix: make setup and permission configuration safe`.

### Task 2: Central database path resolution

**Files:**
- Modify: `src/avo/config.py`, `src/avo/cli.py`, `src/avo/cost.py`, `src/avo/diff.py`
- Test: `tests/test_config.py`, `tests/test_cli.py` or the closest existing CLI test module

**Interfaces:** `database_path_from_env()` remains public and returns a validated string path; CLI defaults resolve `AVO_DATABASE_PATH` only when `--database` is omitted.

- [ ] Write tests proving CLI/database consumers honor `AVO_DATABASE_PATH` and explicit `--database` wins.
- [ ] Run those tests and observe failure from direct `Path("avo.db")` defaults.
- [ ] Implement one resolver helper and use it at the entry points without changing explicit CLI arguments.
- [ ] Run focused config/CLI/cost/diff tests.
- [ ] Commit `fix: centralize database path resolution`.

### Task 3: Subscription OAuth default-off policy

**Files:**
- Modify: `src/avo/oauth/gate.py`, `src/avo/chat_setup.py`, `src/avo/oauth/store.py`
- Test: `tests/test_oauth_gate.py`, `tests/test_chat_setup.py`, `tests/test_oauth_flows.py`
- Documentation: `docs/guides/subscription-auth.md`, `README.md`

**Interfaces:** `subscription_allowed()` returns `False` unless `AVO_ALLOW_SUBSCRIPTION` is an explicit true value; stored OAuth remains usable only after explicit opt-in.

- [ ] Write tests for unset, false, and explicit true subscription gate values.
- [ ] Run the tests and observe the current default-on failure.
- [ ] Implement default-off behavior and preserve explicit setup opt-in.
- [ ] Add tests confirming credential storage describes restrictive permissions rather than encryption.
- [ ] Correct subscription and credential documentation.
- [ ] Run focused OAuth/chat tests.
- [ ] Commit `fix: require explicit subscription oauth opt-in`.

### Task 4: Authenticated web control plane and safe workspace writes

**Files:**
- Modify: `src/avo/web_ui.py`, `src/avo/web_http.py`, `src/avo/web_api.py`, `src/avo/web_workspace.py`, `src/avo/web_playground.py`
- Test: `tests/test_web_ui.py`, new focused web transport tests if needed

**Interfaces:** `AvoWebServer.auth_token` is a per-process secret; POST requests require `Authorization: Bearer <token>` or the same-origin session cookie; default CORS is disabled; workspace writes are atomic and contained.

- [ ] Write tests for unauthorized POST returning 401, valid bearer authentication, no wildcard CORS, and safe OPTIONS behavior.
- [ ] Write a test proving workspace save uses an atomic replacement and rejects paths escaping the workspace.
- [ ] Run focused web tests and observe failure.
- [ ] Implement token generation, cookie/session response, bearer validation, origin handling, and authenticated mutation routing.
- [ ] Implement atomic workspace replacement using a same-directory temporary file and `os.replace`.
- [ ] Set web permission default to `default` and make destructive mutation endpoints require explicit confirmation.
- [ ] Run web tests in the available socket-capable environment; separate sandbox socket failures from product failures.
- [ ] Commit `fix: harden local web control plane`.

### Task 5: Host execution and plugin trust boundary

**Files:**
- Modify: `src/avo/app_tools/test_runner.py`, `src/avo/app_tools/linter.py`, `src/avo/cli_plugins.py`, `src/avo/chat_commands.py`
- Test: `tests/test_test_runner_tool.py`, `tests/test_linter_tool.py`, `tests/test_plugins.py`, `tests/test_cli_confirm.py`
- Documentation: `docs/avo-reference.md`

**Interfaces:** host execution requires `AVO_ALLOW_HOST_EXECUTION=1` unless an explicit caller policy enables it; plugin installation is non-editable by default and requires explicit confirmation for installation/activation.

- [ ] Write tests proving host execution is denied by default and allowed with the explicit environment policy.
- [ ] Write tests proving plugin installation defaults to non-editable and agent/chat installation cannot silently activate it.
- [ ] Run focused tests and observe failure.
- [ ] Implement the smallest policy guard and update callers/tests that intentionally opt into host execution.
- [ ] Change plugin install defaults and user-facing confirmation text.
- [ ] Run focused tool/plugin tests.
- [ ] Commit `fix: make host execution and plugin install explicit`.

### Task 6: Complete token-saver integration

**Files:**
- Modify: `src/avo/savers/stages.py`, `src/avo/savers/provider.py`, `src/avo/savers/presets.py`, `src/avo/config.py`, `src/avo/cli.py`
- Create: `src/avo/savers/config_store.py`, `src/avo/savers/cli.py`
- Test: `tests/test_savers_stages.py`, `tests/test_savers_provider.py`, `tests/test_savers_events.py`, new `tests/test_savers_config_store.py`, `tests/test_savers_cli.py`

**Interfaces:** `AVO_SAVER > saver.json > unset`; `resolve_saver()` returns a built-in preset; the provider factory wraps the selected provider only when configured; `avo saver list|show|use|off` manages the setting.

- [ ] Write a regression test proving dedupe never increases estimated tokens.
- [ ] Write a regression test using `ModelChunk.text` for non-streaming fallback.
- [ ] Write config-store tests for environment/file/unset precedence and CLI tests for list/show/use/off.
- [ ] Run these tests and observe the current failures or missing APIs.
- [ ] Implement the minimal cheaper-only dedupe rule and correct stream contract.
- [ ] Implement saver setting persistence and CLI command routing.
- [ ] Wrap providers at the single factory boundary and preserve opt-in behavior.
- [ ] Run the complete saver test group.
- [ ] Commit `feat: complete opt-in token saver integration`.

### Task 7: Guarantee and documentation cleanup

**Files:**
- Modify: `README.md`, `docs/index.md`, `docs/avo-reference.md`, `docs/guides/combo-routing.md`, `docs/api-stability.md`, `CHANGELOG.md`
- Test: documentation/config smoke tests where existing

- [ ] Write a documentation smoke test or validation script for canonical permission values, database paths, and saver configuration names.
- [ ] Run it and record the current drift.
- [ ] Correct replay, streaming failover, encryption, test counts, permission, OAuth, and configuration claims.
- [ ] Run documentation smoke checks and `git diff --check`.
- [ ] Commit `docs: align guarantees and configuration references`.

### Task 8: Whole-project verification and review

**Files:** repository-wide verification only

- [ ] Run `python -m ruff check .`.
- [ ] Run `python -m ruff format --check .`.
- [ ] Run `python -m mypy src/avo`.
- [ ] Run `python -m pytest -q` with a bounded timeout and record any environment-only socket/MCP limitation.
- [ ] Run `bandit -r src/avo -c pyproject.toml --severity-level medium`.
- [ ] Run the project secret scan or document the exact replacement scan and its findings.
- [ ] Run the package build and `git diff --check`.
- [ ] Perform a final whole-branch code review against the spec.
- [ ] Commit any final fixes after review.
