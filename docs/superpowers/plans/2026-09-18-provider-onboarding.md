# Provider Onboarding and Default Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Avo discoverable and usable through `avo` alone, with browser-based vendor login, account-dependent free/paid access, separate Ollama Local/Cloud flows, hardware-aware model recommendations, confirmed downloads, and complete CLI help.

**Architecture:** Keep the existing provider adapters and OAuth registry as the transport layer. Add a shared provider catalog for onboarding/help/doctor, a focused local hardware/model recommendation module, and a model-management CLI facade. The chat first-run wizard and explicit setup command consume the same catalog and persistence path; no vendor login page is added to Avo.

**Tech Stack:** Python 3.11+, argparse, asyncio, Pydantic, httpx when providers are installed, Ollama HTTP API, existing PKCE localhost callback server, pytest/pytest-asyncio, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-18-provider-onboarding-design.md`

## Global Constraints

- `avo` with no subcommand behaves as `avo chat`; explicit commands and `--version` remain compatible.
- Vendor login opens the official vendor authorization page and never collects vendor passwords.
- Free and paid access is account/quota dependent; Avo never bypasses vendor limits.
- `AVO_ALLOW_SUBSCRIPTION` remains backward-compatible and means opt-in to vendor OAuth-backed inference, not proof of payment.
- Ollama Local downloads always require explicit confirmation with size/resource information.
- Hardware detection is local-only, advisory, and must degrade gracefully.
- Secrets remain out of shell rc files, logs, doctor output, and event payloads; auth storage remains permission-restricted.
- Live provider tests remain opt-in and must not require credentials in CI.
- Preserve unrelated dirty worktree changes; stage only files belonging to the current task.

---

### Task 1: Make the top-level CLI default to chat and expose complete help

**Files:**
- Modify: `src/avo/cli.py`
- Test: `tests/test_cli.py`
- Create: `tests/test_cli_help.py`

**Interfaces:**
- Produces `_normalize_default_chat_argv(argv: Sequence[str]) -> list[str]` or an equivalent private normalizer that inserts `chat` only when no explicit command is present.
- Produces top-level help containing quick-start examples and all command groups.
- Preserves delegated `rest` forwarding for `login`, `setup`, `ui`, `combo`, `plugin`, `mcp`, `skill`, `init`, `bench`, and `sandbox`.

- [ ] **Step 1: Write failing CLI behavior tests.** Add tests that monkeypatch `avo.cli.run_repl` and assert `main([])` invokes the chat path, `main(["chat", "--new-session"])` remains valid, `main(["--version"])` exits with the version, and explicit `doctor`/`login --status` are not rewritten to chat.

- [ ] **Step 2: Run the focused tests and verify failure.**

  ```bash
  python -m pytest -q tests/test_cli.py tests/test_cli_help.py
  ```

  Expected: new no-argument and help assertions fail against the required subparser implementation.

- [ ] **Step 3: Implement default-chat normalization.** Make the parser accept the no-command case while retaining global options. Normalize an empty argv to `chat`; normalize chat-only options such as `--database PATH` to `--database PATH chat` at the parser boundary; leave an explicit command untouched. Do not reinterpret `--help` or `--version`.

- [ ] **Step 4: Implement useful help output.** Use `RawDescriptionHelpFormatter`, add a short quick-start epilog, and add explicit help text for `avo`, `avo chat`, `avo setup`, `avo login`, `avo models`, `avo doctor`, and `avo runs`. The help must state that provider login may open a browser and that cloud/API calls can consume quota.

- [ ] **Step 5: Run the focused tests and verify success.**

  ```bash
  python -m pytest -q tests/test_cli.py tests/test_cli_help.py
  avo --help
  avo --version
  ```

- [ ] **Step 6: Commit the CLI surface.**

  ```bash
  git add src/avo/cli.py tests/test_cli.py tests/test_cli_help.py
  git commit -m "feat: make avo the default chat command"
  ```

### Task 2: Unify provider catalog and account-aware onboarding

**Files:**
- Create: `src/avo/provider_catalog.py`
- Modify: `src/avo/chat_setup.py`
- Modify: `src/avo/config.py`
- Modify: `src/avo/doctor.py`
- Modify: `src/avo/auth.py`
- Test: `tests/test_chat_setup.py`
- Test: `tests/test_config.py`
- Test: `tests/test_doctor.py`
- Create: `tests/test_provider_catalog.py`

**Interfaces:**
- `ProviderOption` immutable record with `key`, `label`, `group`, `auth_kind`, `default_model`, `description`, `credential_key`, and `access_note`.
- `provider_options() -> tuple[ProviderOption, ...]` returns the supported user-facing choices.
- `get_provider_option(key: str) -> ProviderOption` raises a clear `KeyError`/configuration error for unknown choices.
- An async-safe vendor login entrypoint reuses `run_pkce_login` and `store_credential`; the synchronous CLI wrapper remains responsible for `asyncio.run`.

- [ ] **Step 1: Write catalog tests.** Assert that local, cloud, API, subscription/account, and router choices have stable labels; that Codex/Gemini are not labeled “subscription required”; and that Claude explains account-dependent terminal access.

- [ ] **Step 2: Run the catalog tests and verify failure.**

  ```bash
  python -m pytest -q tests/test_provider_catalog.py
  ```

- [ ] **Step 3: Create the shared catalog.** Move duplicated labels/default models into `provider_catalog.py`, add Ollama Local and Ollama Cloud entries, and retain aliases (`gemini-cli`, `gemini_cli`, `chatgpt`) at the dispatch layer rather than duplicating user-facing choices.

- [ ] **Step 4: Update first-run setup.** Render grouped choices, reuse stored API/OAuth credentials when present, and for account choices call the async-safe login service so the official browser opens from the active chat event loop. For API keys, use hidden input and offer secure auth-store persistence without writing shell rc files.

- [ ] **Step 5: Update configuration and doctor.** Resolve the catalog’s credential key and auth kind consistently, keep `AVO_ALLOW_SUBSCRIPTION` compatibility, and render “vendor account / API key / Ollama local / Ollama cloud” without secrets or an unsupported plan claim.

- [ ] **Step 6: Run onboarding/config/doctor tests.**

  ```bash
  python -m pytest -q tests/test_provider_catalog.py tests/test_chat_setup.py tests/test_config.py tests/test_doctor.py
  ```

- [ ] **Step 7: Commit the shared onboarding layer.**

  ```bash
  git add src/avo/provider_catalog.py src/avo/chat_setup.py src/avo/config.py src/avo/doctor.py src/avo/auth.py tests/test_provider_catalog.py tests/test_chat_setup.py tests/test_config.py tests/test_doctor.py
  git commit -m "feat: add account-aware provider onboarding"
  ```

### Task 3: Add local hardware capability detection and recommendations

**Files:**
- Create: `src/avo/hardware.py`
- Create: `src/avo/model_catalog.py`
- Test: `tests/test_hardware.py`
- Create: `tests/test_model_catalog.py`

**Interfaces:**
- `HardwareProfile` frozen record: `cpu_cores`, `ram_bytes`, `gpu_name`, `gpu_vram_bytes`, `disk_free_bytes`, `platform`.
- `detect_hardware(*, runner: Callable[..., object] | None = None, statvfs: Callable[..., object] | None = None) -> HardwareProfile` performs local best-effort detection and never raises solely because a tool is missing.
- `ModelRecommendation` frozen record: `name`, `purpose`, `download_bytes`, `min_ram_bytes`, `recommended_vram_bytes`, `tool_calling`, `context_tokens`, `fit`, `reason`.
- `recommend_ollama_models(profile: HardwareProfile, *, installed: Collection[str] = ()) -> tuple[ModelRecommendation, ...]` returns deterministic ordered recommendations.

- [ ] **Step 1: Write hardware and recommendation tests.** Cover Linux `/proc/meminfo`, missing `nvidia-smi`, GPU command failures, disk lookup, low-memory fallback, and a 16 GB profile similar to the current laptop. Assert that recommendations are advisory and deterministic.

- [ ] **Step 2: Run tests and verify failure.**

  ```bash
  python -m pytest -q tests/test_hardware.py tests/test_model_catalog.py
  ```

- [ ] **Step 3: Implement platform-safe hardware detection.** Read only local metadata; invoke `nvidia-smi`/`rocm-smi` when available with argument arrays and bounded timeouts; use conservative `None` values on failure; never send the profile over the network.

- [ ] **Step 4: Implement the curated catalog and fit scoring.** Include coding/general/tool-use metadata for models already supported by the repository and common Ollama names. Return “fits”, “marginal”, or “too large” with a human-readable reason; never prevent manual selection.

- [ ] **Step 5: Run tests and commit.**

  ```bash
  python -m pytest -q tests/test_hardware.py tests/test_model_catalog.py
  git add src/avo/hardware.py src/avo/model_catalog.py tests/test_hardware.py tests/test_model_catalog.py
  git commit -m "feat: recommend ollama models from local hardware"
  ```

### Task 4: Implement Ollama Local discovery, confirmed pull, and Cloud auth

**Files:**
- Create: `src/avo/ollama_manager.py`
- Create: `src/avo/cli_models.py`
- Modify: `src/avo/providers/ollama.py`
- Modify: `src/avo/config.py`
- Modify: `src/avo/cli.py`
- Test: `tests/test_ollama_manager.py`
- Test: `tests/test_cli_models.py`
- Test: `tests/test_providers_http.py`

**Interfaces:**
- `OllamaManager(base_url: str, client: AsyncClientLike | None = None)` owns discovery/pull operations but not provider inference.
- `async list_models() -> tuple[OllamaModel, ...]` reads `/api/tags`.
- `async check_health() -> OllamaHealth` reads the daemon/version endpoint with a bounded timeout.
- `async pull(model: str, *, confirm: Callable[[PullPlan], Awaitable[bool] | bool], output: Callable[[PullProgress], object]) -> OllamaModel` refuses to POST until confirmation returns true.
- `build_ollama_cloud_config(...)` creates a remote Ollama-compatible provider with secret redaction and no local model pull.
- `avo models ollama [list|recommend|pull MODEL]` is a discoverable command; `avo models --help` documents local versus cloud behavior.

- [ ] **Step 1: Write mocked HTTP tests.** Cover tags parsing, daemon unavailable, pull progress events, cancellation, non-2xx redaction, confirmation refusal, and final tags verification. Use injected fake clients; no real network.

- [ ] **Step 2: Run tests and verify failure.**

  ```bash
  python -m pytest -q tests/test_ollama_manager.py tests/test_cli_models.py
  ```

- [ ] **Step 3: Implement the manager.** Reuse the provider HTTP error/redaction conventions. Parse newline-delimited pull JSON into stable progress records, close the owned client, and ensure cancellation stops the request without deleting existing models.

- [ ] **Step 4: Implement model CLI and onboarding integration.** Show hardware, installed models, recommendations, size, fit reason, and explicit `[y/N]` confirmation. Add `models` delegation in `src/avo/cli.py`. Keep cloud models remote and require official Ollama sign-in/API key rather than pretending they are local.

- [ ] **Step 5: Add config/doctor readiness.** Report Ollama daemon and selected model readiness separately for Local and Cloud. Do not pull during `doctor`.

- [ ] **Step 6: Run mocked tests and the existing Ollama contract suite.**

  ```bash
  python -m pytest -q tests/test_ollama_manager.py tests/test_cli_models.py tests/test_providers_http.py
  ```

- [ ] **Step 7: Commit Ollama model management.**

  ```bash
  git add src/avo/ollama_manager.py src/avo/cli_models.py src/avo/providers/ollama.py src/avo/config.py src/avo/cli.py tests/test_ollama_manager.py tests/test_cli_models.py tests/test_providers_http.py
  git commit -m "feat: add ollama model discovery and confirmed pulls"
  ```

### Task 5: Align documentation and command help with the implemented flow

**Files:**
- Modify: `README.md`
- Modify: `docs/guides/quickstart.md`
- Modify: `docs/guides/subscription-auth.md`
- Modify: `docs/cli.md`
- Modify: `docs/avo-reference.md`
- Modify: `site/` generated documentation sources only as applicable
- Test: `tests/test_docs_commands.py`

**Interfaces:**
- Documentation examples use `avo` as the default chat command.
- Provider sections distinguish free quota, paid quota, API keys, subscription/account OAuth, Ollama Local, and Ollama Cloud.
- Every documented command exists in `avo --help` or delegated help.

- [ ] **Step 1: Write documentation consistency tests.** Check that README/docs use `avo --help`, `avo`, `avo doctor`, `avo login`, and `avo models`; reject stale “subscription required” wording and old docs host references.

- [ ] **Step 2: Update docs and generated site inputs.** Add copy-paste onboarding flows, warnings about quota/cost, browser login behavior, hardware-based local model selection, confirmation before download, and troubleshooting for account-level `401/403/429` responses.

- [ ] **Step 3: Run documentation tests and build the site.**

  ```bash
  python -m pytest -q tests/test_docs_commands.py
  bash scripts/build_site.sh
  ```

- [ ] **Step 4: Commit docs.**

  ```bash
  git add README.md docs/guides/quickstart.md docs/guides/subscription-auth.md docs/cli.md docs/avo-reference.md site
  git commit -m "docs: document provider onboarding and model management"
  ```

### Task 6: Full verification and release handoff

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml` only if the new implementation requires an already-supported optional dependency
- Test: existing repository test suite and live Ollama test

- [ ] **Step 1: Run focused regression suites.**

  ```bash
  python -m pytest -q tests/test_cli.py tests/test_cli_help.py tests/test_chat_setup.py tests/test_provider_catalog.py tests/test_hardware.py tests/test_model_catalog.py tests/test_ollama_manager.py tests/test_cli_models.py tests/test_doctor.py
  ```

- [ ] **Step 2: Run static checks.**

  ```bash
  make lint
  make format-check
  make typecheck
  git diff --check
  ```

- [ ] **Step 3: Run local acceptance checks using the installed Ollama models.** Use the repository's configured model, require no cloud credential, and verify `avo models ollama list`, `avo models ollama recommend`, `avo doctor`, and `avo` without a subcommand. Do not download a model without the interactive confirmation.

- [ ] **Step 4: Run the opt-in live Ollama test.**

  ```bash
  AVO_OLLAMA_MODEL=qwen2.5-coder:7b python -m pytest -q tests/integration/test_ollama_live.py --run-live -v
  ```

- [ ] **Step 5: Run the repository secret scan and package checks required by the project.** Use the existing project commands/configuration; verify no key appears in tracked diffs or generated documentation.

- [ ] **Step 6: Update the changelog and version only after all checks pass.** Record the default `avo` command, provider onboarding, and Ollama model manager. Do not claim all vendor providers are live-verified unless their opt-in smoke tests were actually run.

- [ ] **Step 7: Commit the release handoff and report exact verification output.** Push only through `~/.local/bin/git-push-notify ...` after explicit user direction to publish.

