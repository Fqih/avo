# Milestone One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver account-aware model discovery, safer credential storage, complete Ollama Cloud discovery, and secure paste/drag-and-drop image/file attachments without breaking Avo's existing provider and REPL contracts.

**Architecture:** Add a provider-neutral catalog service above the existing provider metadata and transport code, with bounded disk caching and provider adapters. Add a credential backend abstraction beneath the existing OAuth store, preserving the current JSON format as a safe fallback. Add an attachment parser between the REPL input and `AgentRuntime`, converting validated files into canonical content blocks before providers render their native payloads.

**Tech Stack:** Python 3.11+, Pydantic 2, prompt-toolkit, existing `httpx` optional transport, `urllib` fallback, SQLite session store, pytest/pytest-asyncio, ruff, mypy, bandit.

**Spec:** `docs/superpowers/specs/2026-09-18-milestone-one-design.md`

## Global Constraints

- Preserve Python 3.11, 3.12, and 3.13 support.
- Keep core dependencies limited to Pydantic and prompt-toolkit; keyring remains optional.
- Never persist credentials in model catalogs, caches, logs, shell startup files, or error messages.
- Never read an attachment outside the workspace unless an explicitly configured external attachment root permits it.
- Resolve symlinks before containment checks and enforce file/total size limits before reading bytes.
- Preserve existing `ModelRequest`, provider, session, and plain-text prompt compatibility.
- Write a failing test before each production behavior change, then run the focused test before refactoring.
- Do not run paid/live provider tests by default.
- Do not modify the user's untracked `.avo/` directory.

---

### Task 1: Catalog records and bounded cache

**Files:**
- Create: `src/avo/model_catalog_service.py`
- Modify: `src/avo/model_catalog.py`
- Test: `tests/test_model_catalog_service.py`

**Interfaces:**
- Produces `CatalogSource`, `ModelCatalogEntry`, `ModelCatalogResult`, and `ModelCatalogCache`.
- `ModelCatalogEntry` fields: `provider: str`, `model_id: str`, `label: str`, `source: CatalogSource`, `capabilities: tuple[str, ...]`, `recommended: bool`, `reason: str | None`.
- `ModelCatalogResult` fields: `provider: str`, `models: tuple[ModelCatalogEntry, ...]`, `source: CatalogSource`, `fetched_at: datetime | None`, `stale: bool`, `warning: str | None`.
- `ModelCatalogCache(root: Path, *, ttl_seconds: int = 900, max_entries: int = 256)` exposes `load(provider: str) -> ModelCatalogResult | None` and `save(result: ModelCatalogResult) -> None`.
- `normalize_model_ids(provider: str, raw: object) -> tuple[str, ...]` accepts only non-empty bounded strings and deduplicates deterministically.

- [ ] **Step 1: Write the failing tests**

  Cover normalization/deduplication, JSON cache round-trip, expired cache marked stale, bounded cache entries, provider-key path safety, and omission of credential-looking fields from serialized cache data.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `python -m pytest -q tests/test_model_catalog_service.py`

  Expected: collection/import failures because the service types and cache do not exist.

- [ ] **Step 3: Implement the minimal catalog service**

  Use frozen Pydantic/dataclass value objects, UTC timestamps, atomic replacement, mode `0600` cache files, and deterministic sorting by recommendation then label then model ID. Keep the existing hardware recommendation catalog as an input to `recommended` metadata rather than replacing it.

- [ ] **Step 4: Run focused tests and quality checks**

  Run: `python -m pytest -q tests/test_model_catalog_service.py && python -m ruff check src/avo/model_catalog_service.py tests/test_model_catalog_service.py`

  Expected: all focused tests pass.

- [ ] **Step 5: Commit**

  `git add src/avo/model_catalog_service.py src/avo/model_catalog.py tests/test_model_catalog_service.py && git commit -m "feat: add provider-neutral model catalog cache"`

### Task 2: Provider discovery adapters and `/model` picker

**Files:**
- Create: `src/avo/model_discovery.py`
- Modify: `src/avo/chat_turn.py`
- Modify: `src/avo/config.py`
- Modify: `src/avo/provider_catalog.py`
- Test: `tests/test_model_discovery.py`
- Modify: `tests/test_chat_repl.py`

**Interfaces:**
- `ModelDiscoveryClient` protocol exposes `async list_models() -> tuple[str, ...]`.
- `discover_provider_models(provider: str, environ: Mapping[str, str], *, cache: ModelCatalogCache, http_client: object | None = None) -> ModelCatalogResult` tries live discovery, then fresh/stale cache, then an explicitly marked static catalog.
- Adapters: `OllamaLocalDiscovery`, `OllamaCloudDiscovery`, `OpenAICompatibleDiscovery`, `GeminiCliDiscovery`; each returns normalized IDs and never includes auth headers in exceptions.
- `_run_model_command` consumes `ModelCatalogResult` and displays source/stale status while preserving `/model NAME` compatibility.

- [ ] **Step 1: Write failing adapter and picker tests**

  Test OpenAI-compatible `/v1/models` normalization, Ollama `/api/tags`, live failure with fresh cache, stale fallback warning, static fallback labeling, and picker selection using the discovered model ID rather than the old static list.

- [ ] **Step 2: Run focused tests to verify RED**

  Run: `python -m pytest -q tests/test_model_discovery.py tests/test_chat_repl.py -k "model or catalog"`

  Expected: failures for the missing discovery service and source metadata.

- [ ] **Step 3: Implement adapters and integrate the picker**

  Reuse injected HTTP clients where existing providers already expose them, keep local Ollama proxy bypass behavior, and make `/model` degrade cleanly in non-TTY tests. Never reject a currently configured model solely because a vendor catalog is temporarily unavailable.

- [ ] **Step 4: Run focused tests**

  Run: `python -m pytest -q tests/test_model_discovery.py tests/test_chat_repl.py -k "model or catalog"`

  Expected: all selected tests pass.

- [ ] **Step 5: Commit**

  `git add src/avo/model_discovery.py src/avo/chat_turn.py src/avo/config.py src/avo/provider_catalog.py tests/test_model_discovery.py tests/test_chat_repl.py && git commit -m "feat: discover provider models dynamically"`

### Task 3: Credential backend and migration

**Files:**
- Create: `src/avo/credentials.py`
- Modify: `src/avo/oauth/store.py`
- Modify: `src/avo/auth.py`
- Modify: `src/avo/doctor.py`
- Modify: `pyproject.toml`
- Test: `tests/test_credentials.py`
- Modify: `tests/test_oauth_store.py`
- Modify: `tests/test_doctor.py`

**Interfaces:**
- `CredentialBackend` protocol exposes `get(provider: str) -> Credential | None`, `put(credential: Credential) -> None`, `delete(provider: str) -> bool`, `list() -> tuple[Credential, ...]`, and `describe() -> str`.
- `FileCredentialBackend(path: Path)` preserves the existing JSON format, writes atomically with directory mode `0700` and file mode `0600`, and reports `file-permissions`.
- `KeyringCredentialBackend(service: str)` stores serialized credential records in the optional `keyring` package and reports `os-keyring`.
- `resolve_credential_backend(environ: Mapping[str, str] | None = None) -> CredentialBackend` selects keyring when explicitly requested or available, otherwise the secure file backend; invalid backend selection fails with an actionable error.
- Existing `get_credential`, `store_credential`, and related functions delegate through the backend without changing their public signatures.

- [ ] **Step 1: Write failing backend tests**

  Test file backend permissions and atomic replacement, keyring round-trip with an injected fake keyring module, migration from existing `auth.json`, backend selection, deletion, and secret-free diagnostics.

- [ ] **Step 2: Run focused tests to verify RED**

  Run: `python -m pytest -q tests/test_credentials.py tests/test_oauth_store.py`

  Expected: failures because the backend abstraction and resolver do not exist.

- [ ] **Step 3: Implement backends and delegate the OAuth store**

  Keep token values out of `repr`, logs, cache files, and shell persistence. For keyring serialization use the existing validated `Credential` schema. For fallback migration, read the old file once, write through the selected backend, and retain the file only when fallback is selected.

- [ ] **Step 4: Update doctor and test it**

  Run: `python -m pytest -q tests/test_credentials.py tests/test_oauth_store.py tests/test_doctor.py`

  Expected: all selected tests pass and doctor reports backend type without secrets.

- [ ] **Step 5: Commit**

  `git add src/avo/credentials.py src/avo/oauth/store.py src/avo/auth.py src/avo/doctor.py pyproject.toml tests/test_credentials.py tests/test_oauth_store.py tests/test_doctor.py && git commit -m "feat: add pluggable credential backends"`

### Task 4: Complete Ollama Cloud management

**Files:**
- Modify: `src/avo/ollama_manager.py`
- Modify: `src/avo/cli_models.py`
- Modify: `src/avo/config.py`
- Test: `tests/test_ollama_cloud.py`
- Modify: `tests/test_ollama_manager.py`
- Modify: `tests/test_cli_models.py`

**Interfaces:**
- `OllamaCloudConfig` contains `base_url`, `api_key` as a private value, and `model`.
- `OllamaCloudManager.list_models() -> tuple[OllamaModel, ...]`, `check_health() -> OllamaHealth`, and `usage() -> OllamaUsage | None` use the remote endpoint and redact auth failures.
- `OllamaUsage` contains optional `remaining_tokens`, `limit_tokens`, `reset_at`, and `raw_fields: tuple[str, ...]` without retaining raw secret values.
- `avo models ollama cloud list|health|usage` performs read-only remote operations; `pull` remains explicitly local-only.

- [ ] **Step 1: Write failing cloud tests**

  Test bearer header, remote model normalization, health, optional usage response, HTTP error redaction, and that cloud commands never call local `/api/pull`.

- [ ] **Step 2: Run focused tests to verify RED**

  Run: `python -m pytest -q tests/test_ollama_cloud.py tests/test_ollama_manager.py tests/test_cli_models.py`

  Expected: missing cloud manager/config and CLI subcommands fail.

- [ ] **Step 3: Implement cloud manager and CLI**

  Use the existing injected-client pattern, keep local and cloud endpoints explicit, and return a useful “provider does not expose usage” result rather than inventing quota values.

- [ ] **Step 4: Run focused tests**

  Run: `python -m pytest -q tests/test_ollama_cloud.py tests/test_ollama_manager.py tests/test_cli_models.py`

  Expected: all selected tests pass.

- [ ] **Step 5: Commit**

  `git add src/avo/ollama_manager.py src/avo/cli_models.py src/avo/config.py tests/test_ollama_cloud.py tests/test_ollama_manager.py tests/test_cli_models.py && git commit -m "feat: add Ollama Cloud discovery and usage"`

### Task 5: Attachment model and secure parser

**Files:**
- Create: `src/avo/attachments.py`
- Modify: `src/avo/content_blocks.py`
- Test: `tests/test_attachments.py`
- Modify: `tests/test_content_blocks.py`

**Interfaces:**
- `AttachmentSettings` contains `max_file_bytes`, `max_image_bytes`, `max_total_bytes`, and `external_root: Path | None` with conservative defaults and environment parsing.
- `Attachment` contains `display_name`, `path: Path | None`, `media_type`, `kind: Literal["image", "text", "binary"]`, `size_bytes`, and immutable content metadata.
- `parse_prompt_attachments(text: str, *, workspace: Path, settings: AttachmentSettings) -> ParsedPrompt` returns `text: str`, `attachments: tuple[Attachment, ...]`, and `errors: tuple[str, ...]`.
- `attachment_blocks(parsed: ParsedPrompt) -> list[TextBlock | ImageBlock]` creates labelled text/image blocks and rejects unsupported binary input with an actionable error.
- `parse_clipboard_image(raw: bytes, media_type: str, *, workspace: Path, settings: AttachmentSettings) -> Attachment` validates clipboard data before writing any temporary file.

- [ ] **Step 1: Write failing attachment tests**

  Cover `@path`, dropped absolute path, `file://` URI decoding, quoted spaces, ordinary prose non-attachment behavior, workspace containment, symlink escape, external-root opt-in, image MIME allowlist, text decoding, binary rejection, size limits, total limits, and no secret/path leakage in errors.

- [ ] **Step 2: Run focused tests to verify RED**

  Run: `python -m pytest -q tests/test_attachments.py tests/test_content_blocks.py`

  Expected: missing parser/settings/block integration failures.

- [ ] **Step 3: Implement parser and block conversion**

  Use `Path.resolve(strict=True)` for existing paths, `relative_to` containment checks, `urllib.parse.unquote` for file URIs, `mimetypes` plus a strict image allowlist, UTF-8 decoding with a clear error, and immutable records. Never inline unsupported binary data into a prompt.

- [ ] **Step 4: Run focused tests**

  Run: `python -m pytest -q tests/test_attachments.py tests/test_content_blocks.py`

  Expected: all selected tests pass.

- [ ] **Step 5: Commit**

  `git add src/avo/attachments.py src/avo/content_blocks.py tests/test_attachments.py tests/test_content_blocks.py && git commit -m "feat: add secure file and image attachments"`

### Task 6: Clipboard/drop input and provider payload integration

**Files:**
- Create: `src/avo/clipboard.py`
- Modify: `src/avo/chat.py`
- Modify: `src/avo/chat_turn.py`
- Modify: `src/avo/runtime.py`
- Modify: `src/avo/providers/http_common.py`
- Modify: `src/avo/providers/ollama.py`
- Modify: `src/avo/providers/gemini.py`
- Modify: `src/avo/providers/gemini_cli.py`
- Modify: `src/avo/providers/anthropic.py`
- Modify: `src/avo/providers/codex.py`
- Test: `tests/test_clipboard.py`
- Modify: `tests/test_chat_repl.py`
- Modify: `tests/test_ollama_provider.py`
- Modify: `tests/test_gemini.py`
- Modify: `tests/test_content_blocks.py`

**Interfaces:**
- `ClipboardProvider.read_image() -> tuple[bytes, str] | None` probes `wl-paste` then `xclip` with bounded output and returns `None` when unavailable.
- `prepare_user_prompt(text: str, *, workspace: Path, settings: AttachmentSettings, clipboard: ClipboardProvider | None = None) -> PreparedPrompt` handles explicit attachment tokens, dropped paths, and the `Ctrl+V` attachment action.
- `PreparedPrompt.text` is the clean user text; `PreparedPrompt.blocks` is the canonical content list; `PreparedPrompt.summary` is safe UI text.
- `AgentRuntime.run` gains an optional `user_blocks` argument only through a backward-compatible keyword path; plain `task: str` remains unchanged.

- [ ] **Step 1: Write failing integration tests**

  Test clipboard command parsing with an injected subprocess runner, REPL prompt preparation, session persistence of a safe attachment summary, OpenAI-compatible image payload, Ollama `images` payload, Anthropic image block, Gemini inline data, and rejection for providers that cannot accept images.

- [ ] **Step 2: Run focused tests to verify RED**

  Run: `python -m pytest -q tests/test_clipboard.py tests/test_chat_repl.py tests/test_ollama_provider.py tests/test_gemini.py tests/test_content_blocks.py -k "attach or image or clipboard"`

  Expected: integration failures because prompt input and provider payloads do not consume parsed blocks consistently.

- [ ] **Step 3: Implement clipboard and REPL integration**

  Add a prompt-toolkit binding that inserts a non-secret attachment marker for clipboard images, parse dropped paths at submit time, show a compact colored attachment summary, and pass canonical blocks into the existing message history/runtime path. Keep the prompt one line high when no attachment is active.

- [ ] **Step 4: Implement provider renderers**

  Reuse the existing `content_blocks` dumpers, extend Gemini and Ollama where their native schemas differ, and make unsupported provider behavior explicit. Keep all provider payload tests deterministic and injected.

- [ ] **Step 5: Run focused tests**

  Run: `python -m pytest -q tests/test_clipboard.py tests/test_chat_repl.py tests/test_ollama_provider.py tests/test_gemini.py tests/test_content_blocks.py -k "attach or image or clipboard"`

  Expected: all selected tests pass.

- [ ] **Step 6: Commit**

  `git add src/avo/clipboard.py src/avo/chat.py src/avo/chat_turn.py src/avo/runtime.py src/avo/providers/http_common.py src/avo/providers/ollama.py src/avo/providers/gemini.py src/avo/providers/gemini_cli.py src/avo/providers/anthropic.py src/avo/providers/codex.py tests/test_clipboard.py tests/test_chat_repl.py tests/test_ollama_provider.py tests/test_gemini.py tests/test_content_blocks.py && git commit -m "feat: connect terminal attachments to providers"`

### Task 7: Onboarding, diagnostics, documentation, and migration

**Files:**
- Modify: `src/avo/chat_setup.py`
- Modify: `src/avo/doctor.py`
- Modify: `src/avo/cli.py`
- Modify: `README.md`
- Modify: `docs/cli.md`
- Modify: `docs/guides/quickstart.md`
- Modify: `docs/guides/subscription-auth.md`
- Modify: `CHANGELOG.md`
- Test: `tests/test_milestone_one_docs.py`
- Modify: `tests/test_cli_help.py`
- Modify: `tests/test_chat_setup.py`

**Interfaces:**
- Setup uses the dynamic catalog picker when live discovery succeeds and labels fallback models honestly.
- `avo doctor` reports catalog cache location, credential backend, Ollama local/cloud reachability, and attachment limits without secrets.
- CLI help documents `avo models ollama cloud list|health|usage`, `/model`, attachment syntax, and optional keyring setup.

- [ ] **Step 1: Write failing docs/diagnostic tests**

  Assert help text contains the new commands and attachment syntax, doctor output contains backend/source labels, and no output contains token values or raw authorization headers.

- [ ] **Step 2: Run focused tests to verify RED**

  Run: `python -m pytest -q tests/test_milestone_one_docs.py tests/test_cli_help.py tests/test_chat_setup.py`

  Expected: missing help/diagnostic strings fail.

- [ ] **Step 3: Implement onboarding and documentation**

  Update setup to prefer live model choices, add migration notes for old `auth.json`, and document terminal limitations: drag/drop depends on the terminal sending a path; binary files are not silently uploaded.

- [ ] **Step 4: Run focused tests and documentation checks**

  Run: `python -m pytest -q tests/test_milestone_one_docs.py tests/test_cli_help.py tests/test_chat_setup.py && python -m ruff check src tests`

  Expected: all selected tests and lint pass.

- [ ] **Step 5: Commit**

  `git add src/avo/chat_setup.py src/avo/doctor.py src/avo/cli.py README.md docs/cli.md docs/guides/quickstart.md docs/guides/subscription-auth.md CHANGELOG.md tests/test_milestone_one_docs.py tests/test_cli_help.py tests/test_chat_setup.py && git commit -m "docs: document milestone one provider and attachment flows"`

### Task 8: Full quality gate and release readiness

**Files:**
- Modify only files required by failing checks.
- Test: existing full test suite and integration tests.

- [ ] **Step 1: Run the focused milestone suite**

  Run: `python -m pytest -q tests/test_model_catalog_service.py tests/test_model_discovery.py tests/test_credentials.py tests/test_ollama_cloud.py tests/test_attachments.py tests/test_clipboard.py tests/test_content_blocks.py`

  Expected: all milestone tests pass.

- [ ] **Step 2: Run the complete offline quality gate**

  Run: `python -m ruff check . && python -m ruff format --check . && python -m mypy src/avo && python -m pytest -q && bandit -r src/avo -c pyproject.toml --severity-level medium && git diff --check`

  Expected: every check passes; environment-only failures are reported separately and not hidden.

- [ ] **Step 3: Run opt-in local live validation**

  Run: `AVO_OLLAMA_BASE_URL=http://127.0.0.1:11434 AVO_OLLAMA_MODEL=qwen2.5-coder:7b python -m pytest -q tests/integration/test_ollama_live.py --run-live -v`

  Expected: local Ollama tests pass when the daemon/model are available; otherwise tests skip with the reason.

- [ ] **Step 4: Review the diff and release notes**

  Run: `git status --short && git diff --stat HEAD~8..HEAD`

  Confirm `.avo/` remains untracked and untouched, no secrets are present, and every acceptance criterion in the spec is covered by tests or an explicit provider limitation.

- [ ] **Step 5: Commit any final quality fixes**

  Use a focused commit message for each correction; do not amend unrelated history.

