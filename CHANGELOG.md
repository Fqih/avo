# Changelog

All notable changes to avo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Autonomous Loop & Self-Paced Runner (`avo.loop`)**:
  - Added schedule parsing for intervals (`30s`, `5m`, `2h`, `1d`) and 5-part cron syntax.
  - Added `LoopRunner` with budget limit enforcement, failure backoff, metrics aggregation, and pause/resume lifecycle.
  - Integrated `/loop CADENCE PROMPT`, `/unloop`, and `/loop-status` REPL slash commands.
- **Code Intelligence & AST Tools (`avo.code_intel`)**:
  - Native Python `ast` symbol extractor (`extract_symbols`), declaration finder (`find_symbol_definitions`), and reference tracker (`find_symbol_references`).
  - Added workspace-contained FunctionTools: `outline_symbols`, `find_definitions`, and `find_references`.
- **Multi-Agent Shared Blackboard Memory (`avo.blackboard`)**:
  - Shared typed key-value scratchpad backed by SQLite with optimistic versioning and namespace isolation.
  - Added agent FunctionTools: `blackboard_set`, `blackboard_get`, and `blackboard_list`.
- **Web Cockpit Full-Duplex (`avo.web_approval`, `avo.web_dag`)**:
  - Added `WebApprovalBridge` for human-in-the-loop tool approval with `GET /api/approvals/pending` and `POST /api/approvals/{id}/decision`.
  - Added DAG trace visualizer with `GET /api/runs/{id}/dag` and Mermaid graph rendering.

## [0.7.3] - 2026-09-20

### Added

- `avo` now starts chat by default, with a complete discoverable `avo --help`
  command surface.
- Account-aware vendor onboarding opens official Claude, Codex, and Gemini
  browser login flows; `avo combo auth NAME` logs missing vendors sequentially.
- Separate Ollama Local and Ollama Cloud paths, hardware-aware local model
  recommendations, model listing, and explicit confirmation before downloads.
- Documentation and the Netlify `site/` output now document the new onboarding
  flow, quota semantics, model manager, and current Avo branding.
- Added opt-in deterministic token savers with `avo saver list|show|use|off`,
  persisted settings, provider wrapping, `SAVER_APPLIED` trace events, and
  reproducible benchmark results.
- Added live provider model catalogs with bounded cache/static fallback labels,
  optional OS-keyring credentials, and read-only Ollama Cloud model/health/
  usage inspection.
- Added safe workspace file/image attachments via pasted or dragged paths,
  `@clipboard`, size/containment validation, multimodal provider rendering,
  and `avo doctor` attachment/catalog diagnostics.
- Added Milestone 2 named workspace agents (`@coder`, `@explore`, `@reviewer`),
  searchable agent selection, isolated bounded parallel delegation, and
  read-only tool boundaries for delegated explorers.
- Added deterministic event-ledger replay with request fingerprints, durable
  tool-result validation, `avo runs replay RUN_ID --json`, and `/replay`.
- Added canonical security configuration resolution with source-aware `avo
  doctor` diagnostics across permission, sandbox, plugin, and web settings.
- Added capability metadata and non-escalating child-agent policies, final
  workspace containment checks, required-sandbox enforcement, and bounded
  execution/network defaults.
- Added plugin metadata previews, explicit operator confirmation, atomic index
  publication, and local web mutation protection with bearer, origin, CSRF,
  and confirmation gates.
- Added Milestone Three migration guidance and API/CLI documentation for
  resume versus replay, host versus sandbox execution, OAuth/API keys, and
  permission protection versus encryption.
- Added `StructuralCollapseStage` deterministic token saver stage to collapse
  excessive blank lines, divider rules, and trailing whitespace in tool outputs.
- Added dynamic combo routing strategies (`priority`, `latency`, `cost`) to
  `ComboProfile` and `ComboRouterProvider` for latency-aware and cost-optimized
  multi-tier execution.
- Added unified diff calculation and payload emission to `edit_file` tool.
- Added live token-saver preset indicator and unified diff color rendering to
  chat REPL status bar.
- Added sequential multi-agent pipeline orchestration (`@agent1 -> @agent2 -> @agent3`)
  via `DelegationCoordinator.pipeline()` with intermediate output handoff downstream.
- Added `resolve_budget_config()` in `avo.budget` for environment-driven session
  and daily spend caps (`AVO_BUDGET_HARD_LIMIT_USD`, `AVO_BUDGET_WARNING_USD`).
- Added official multi-stage `Dockerfile` with unprivileged `avo` operator user.
- Updated `docs/api-stability.md` with complete package layout, Web Control-Plane
  HTTP API specification, and full CLI subcommand surface.
- Updated `CONTRIBUTING.md` with custom model provider SDK guide and review policies.
- Fixed CI's optional keyring type-check dependency and native workflow
  bootstrap so native wheels build from the checkout without requiring a
  previously published `avo-native` package.

### Fixed

- Hardened ephemeral Docker container creation parameters: removed invalid `auto_remove`,
  `stdout`, and `stderr` kwargs, added `security_opt=["no-new-privileges:true"]` and
  writable tmpfs `/tmp`.
- Restricted project-level configuration (`.avo/config.toml`) from disabling sandbox
  enforcement (`sandbox_required = false`), forcing permission bypass, or enabling
  editable plugins.
- Hardened workspace git operations with `-c core.fsmonitor=false -c core.hooksPath=/dev/null`
  to prevent hook and fsmonitor execution on the host.
- Protected `.git` directory against modification from write tools (`write_file`,
  `edit_file`, `batch_replace`).
- Preserved operator `require_approval` tool lists across interactive `/permissions`
  mode switches.
- Fixed `CLAUDE.md` and `README.md` documentation pointers and aligned status badges
  with project development status.

## [0.7.2] - 2026-09-18

### Fixed

- Interactive `avo` sessions now use the terminal alternate screen buffer,
  hiding previous shell commands while Avo is running and restoring the shell
  view after `/quit`, EOF, or Ctrl+C without deleting scrollback.

## [0.7.1] - 2026-09-18

### Added

- Cross-platform user-global installer for Linux, macOS, Git Bash, and native
  Windows PowerShell, published from `avo.faqihhakim.tech`.
- Global `~/.avo` configuration support for provider defaults, permissions,
  instructions, plugins, skills, MCP registrations, and persistent history.
- Explicit `avo setup --allow-subscription` opt-in for subscription-backed OAuth
  inference, with matching `avo doctor` diagnostics.
- Refreshed documentation site and README branding using the new Avo logo.

### Fixed

- First-run chat now renders the ASCII Avo banner before the provider setup
  wizard instead of appearing to fall back to the old CLI experience.
- Documentation installer links no longer depend on a missing raw GitHub file;
  Netlify publishes `install.sh` and `install.ps1` at the site root.

## [0.1.6] - 2026-09-16

### Fixed

- Fix `CodexProvider` and `GeminiCliProvider` failing with `Expecting value: line 1 column 1`
  caused by attempting `response.json()` on Server-Sent Events (SSE) stream responses.
- Parse SSE `data:` payloads and aggregate completed items, text parts, and usage metadata.

## [0.1.5] - 2026-09-16

### Added

- Direct chat inference for authenticated web OAuth accounts (ChatGPT/Codex, Gemini, Claude web)
  allowing free and subscription users to chat without manual `AVO_ALLOW_SUBSCRIPTION` flag setting.
- Automatic provider detection from stored credentials in `~/.config/avo/auth.json` when `AVO_PROVIDER` is unset.
- Complete GitHub discoverability overhaul: rewritten README with hero banner, 4 core pillars,
  comparison matrix (Avo vs 9router vs LiteLLM vs OpenRouter vs Claude Code), 2-minute quickstart,
  curated topics, and architectural documentation.
- Subproject S2 — Combo Routing: multi-tier model orchestration with named profiles
  (`default`, `coder`, `budget`), automatic HTTP 429 and quota failover detection,
  and local Ollama as a zero-cost reliability floor.
- `avo combo` CLI tool with `list`, `show`, `new`, and `rm` subcommands for profile
  management persisted to `~/.config/avo/combos.json` (chmod 0600).
- Interactive REPL `/combo` slash command for inspecting tier health and hot-swapping
  active combos mid-session, accompanied by live fallback visual notices.
- SQLite durable `route_failover` event recording and invariant validation.
- Subscription OAuth authentication and universal credential management (`avo login`):
  PKCE OAuth authorization code flows for Anthropic Claude (claude.ai subscription),
  OpenAI ChatGPT Codex (`codex`), and Google Gemini CLI (`gemini-cli`), plus universal
  API key credential persistence in `~/.config/avo/auth.json` (chmod 0600).
- Subscription safety gate via `AVO_ALLOW_SUBSCRIPTION=1` to ensure users acknowledge
  platform subscription usage policies and risks.
- Automatic background OAuth token refresh lifecycle with in-flight deduplication
  and refresh-token rotation support.
- Credential import for existing CLI logins from Claude Code (`~/.claude.json`) and
  Codex CLI (`~/.codex/auth.json`).
- Provider factory, chat setup wizard, and `avo doctor` support for subscription
  providers and stored credential diagnostics without secret leakage.
- `docs/avo-reference.md` — the complete project API reference
  `CLAUDE.md` had always cited but never existed: 23 sections, every
  signature, enum member, env var, and container config read verbatim
  from source. Section numbering is frozen; CLAUDE.md cites it by
  number.
- Docs site branding: `logo.svg`/`logo.webp` copied into
  `docs/assets/`, wired as the Material `theme.logo` and `favicon`,
  and a centered logo banner on the docs home page.
- `AgentRuntime.run()`/`resume()` accept per-call
  `stream_callback`/`stream_interrupt_callback` that override the
  instance attributes for that run only. The chat REPL now binds its
  live printer per call instead of mutating the shared runtime, so a
  background `run()` can no longer snapshot the chat printer (the
  reverse of the existing in-flight-swap race).
- `stream_sse_chunks` in `avo.providers.http_common` — shared
  open → status-gate → iterate control flow for SSE transports, with
  the per-line interpretation injected as `parse_line`.
  `stream_openai_chunks` and `GeminiProvider.stream()` are now thin
  specializations of it (Gemini's duplicated status block removed).
- `avo-native` now publishes to PyPI: `native-release.yml` gained a
  `sdist` job and a `publish` job using the same Trusted Publishing
  setup as `release.yml` (environment `pypi`, OIDC `id-token`, no
  token secret). `avo[native]` installs the prebuilt abi3 wheel
  (`avo-native>=0.1.4`, cp311+) instead of building from source with
  maturin; the sdist covers platforms outside the matrix.

### Fixed

- `MCPServer` requests hung for the full 90 s timeout when the server
  subprocess died: the stdio read loop returned on EOF without waking
  the pending futures. The loop now fails every outstanding request
  with an `MCPError` as soon as the connection closes, which also
  removes the `test_mcp_servers.py` timeouts that flaked CI on loaded
  runners.
- The `OpenSSF Scorecard` workflow failed on every run: its action
  image moved from `gcr.io/openssf` (project shut down — pulls
  return "requires billing") to `ghcr.io` in action v2.4.4. Bumped
  `ossf/scorecard-action` v2.4.0 → v2.4.4.
- The `Docs` workflow built the site and uploaded it as a run
  artifact but published it nowhere — the Pages site was a 404.
  Main pushes now ship `site/` through `upload-pages-artifact` +
  `deploy-pages` (matching the repo's Pages source = GitHub Actions,
  CNAME `fqih.cloud`); `mkdocs.yml` `site_url` updated accordingly.
- Native release matrix ran every target on `ubuntu-latest`, so the
  darwin legs failed (`cc: error: unrecognized command-line option
  '-arch'`) and the MSVC legs could not link at all. macOS builds run
  on `macos-latest` (Apple clang crosses x86_64 from arm64), Windows
  on `windows-latest`. `aarch64-pc-windows-msvc` dropped from the
  matrix — the ARM64 MSVC toolchain is not reliably on the runner
  image; sdist covers it.
- Native extension version bumped to 0.1.4 to match the `avo`
  release line (it was never published, so no version is burned).

## [0.1.4] — 2026-09-15

### Added

- `avo.providers.gemini` — Google Gemini adapter over the native
  `generateContent` / `streamGenerateContent` REST APIs with bearer
  auth, tool calls, and `stream()` support. The non-streaming parser
  adopts the per-chunk `responseId` so both paths agree.
- Live token streaming in `avo chat` — assistant text prints as it
  arrives, gated by `AVO_CHAT_STREAM` (off by default). The event
  log stays byte-identical with or without streaming.
- Lossless streaming protocol — `ModelChunk` carries `responseId`
  and tool-call argument deltas so `collect_stream()` matches
  `generate()` field-for-field.
- `avo chat` and the web UI decomposed: `chat.py` and `web_ui.py`
  are re-export shims over focused submodules; the public API
  surface (`__all__`) is unchanged.
- `avo.providers.groq` and `avo.providers.cerebras` — OpenAI-compatible
  HTTP adapters for Groq (`https://api.groq.com/openai/v1`) and
  Cerebras (`https://api.cerebras.ai/v1`) with bearer auth and an
  injectable async client.
- `avo cost` CLI — aggregates the persistent `TokenLedger` into total
  + per-run + per-model token and USD summaries. Emits a human-readable
  table by default and machine-readable JSON via `--json`.
- `avo plugin init [NAME] [--directory DIR] [--force]` — scaffolds a
  working plugin directory: `pyproject.toml` declaring an `avo.tools`
  entry point, a `register()` stub returning a sample `FunctionTool`,
  `README.md`, and `.gitignore`.
- `avo --version` — prints the package version (`avo 0.1.4`).
- `.github/workflows/ci.yml` CycloneDX SBOM job — generates the SBOM
  on every push + PR and uploads it as an artifact.
- `avo.mcp_server` — MCP (Model Context Protocol) stdio server exposing
  a `ToolRegistry` over JSON-RPC 2.0 with LSP-style `Content-Length`
  framing. Implements `initialize`, `ping`, `tools/list`, and
  `tools/call`; notifications (`notifications/initialized`, `exit`)
  are handled fire-and-forget. Transport-agnostic: tests inject
  read/write callables, the CLI wires real stdio.
- `avo serve-mcp` CLI — builds the default tool registry (file, glob,
  grep, git tools) and serves it over stdio for MCP-compatible clients.
- `SandboxExecutor(language=...)` / `SandboxExecutor.for_language()` —
  resolves a Docker base image by language name (`python`, `node`,
  `typescript`, `go`, `rust`, `ruby`, `java`, `bash`, `generic`), and
  `language_from_path()` infers the language from a file extension.
  Explicit `image=` still wins.

### Changed

- `ci.yml` extended with `bandit`, `pip-audit`, and SBOM steps.
- `pyproject.toml` `[build]` target switched to `reproducible = true`
  to honor `SOURCE_DATE_EPOCH`.
- `release.yml` publishes to PyPI via Trusted Publishing: the
  `publish` job downloads the build artifact and authenticates with
  the job's GitHub OIDC `id-token` on the `pypi` environment
  (`pypa/gh-action-pypi-publish`); no `PYPI_TOKEN` secret needed.
  The wheel + sdist + SBOM still attach to the GitHub release.
- `release.yml` quality gates include the `[dev,providers,sandbox,otel]`
  extras so mypy sees httpx / docker / opentelemetry stubs.
- README updated with the 0.1.3 surface: `[otel]` extra, groq +
  cerebras providers, cost CLI, plugin init, sandbox run, bench, diff.
- `mcp_servers.git._run_git` timeout bumped from 10s to 30s for
  cold-cache CI environments.
- `pyproject.toml` PyPI metadata enriched: explicit `maintainers`
  field, 9-keyword list (agents, ai, llm, reliability, sqlite,
  runtime, state-machine, resumable, observable), 12-classifier
  set (Framework::AsyncIO, Intended Audience, OS Independent,
  Python 3::Only, Topic, Typing::Typed), and 3 extra `project_urls`
  (Changelog, Documentation, Funding). Author identity fixed to
  `Fqih <mhmdfkih21@gmail.com>`.

### Fixed

- `tests/test_mcp_servers.py::test_git_server_reports_status_and_log`
  now passes `git commit --no-verify` so the local pre-commit
  identity hook does not reject the fixture's per-repo author.

### Added

- `avo.providers.streaming` — `ModelChunk` / `StreamingModelProvider`
  protocol + `collect_stream()` helper, already shipped earlier as
  scaffolding. Now exercised by every HTTP provider.
- `avo.providers.http_common` — shared SSE byte-iterator parser
  (`iter_sse_lines`, `iter_anthropic_sse_events`), per-protocol chunk
  parsers (`parse_openai_stream_payload`, `parse_anthropic_stream_event`),
  and high-level `stream_openai_chunks` / `stream_anthropic_chunks`
  helpers.
- `OpenAICompatibleProvider.stream()`, `GroqProvider.stream()`,
  `CerebrasProvider.stream()`, `AnthropicProvider.stream()` —
  `async def stream(request) -> AsyncIterator[ModelChunk]` on the
  HTTP providers. Sets `stream: true` in the request payload,
  iterates SSE frames, and yields `ModelChunk` events with text
  deltas, finish reasons, and tool-call argument deltas. Falls
  back to a single-chunk emission of `generate()` when the injected
  client does not implement `stream()`.
- Shared `_AsyncHTTPClient` Protocol in `http_common.py` now declares
  `post()` + `stream()` + `aclose()`. All HTTP providers (openai,
  groq, cerebras, anthropic, minimax, ollama) import the shared
  protocol instead of redeclaring it inline.
- `tests/test_provider_streaming.py` — 19 tests covering the SSE
  parsers, OpenAI/Anthropic chunk translators, end-to-end
  `stream()` coroutines on four providers with a fake HTTP client,
  and `collect_stream()` integration.
- `avo.circuit_breaker` — three-state `CircuitBreaker`
  (CLOSED / OPEN / HALF_OPEN) with `CircuitBreakerPolicy` (failure
  threshold, cooldown seconds, half-open max probes) and an
  injectable monotonic clock. `BreakerOpen` exception carries a
  `retry_after_seconds` hint.
- `LoopPolicy.circuit_breaker` — optional breaker attached to the
  runtime. When set, `AgentRuntime` consults the breaker before each
  provider call, records success/failure, and short-circuits
  saturated upstream paths with a non-retryable `ProviderError`.
- `tests/test_circuit_breaker.py` — 12 tests covering state
  transitions, half-open probe semantics, LoopPolicy integration,
  and end-to-end runtime opening after consecutive provider failures.
- `avo.logging_config` — `JsonFormatter`, `install_json_handler`,
  `configure_logging()` for structured JSON log emission.
  One JSON object per record with `ts` (RFC 3339 UTC), `level`,
  `logger`, `message`, `exc_info` when set, and any `extra={}`
  keys passed at the call site. Idempotent under repeated
  configuration.
- `tests/test_logging_config.py` — 8 tests covering formatter
  output, extras serialization, exception capture, and idempotent
  reconfiguration.
- `release.yml` — Sigstore re-enabled via the `sigstore>=3`
  Python client (GitHub Action pinned stale TUF metadata). Keyless
  signing uses GitHub Actions OIDC; `.sig` files attach to the
  GitHub release alongside the wheel, sdist, and SBOM.
- `avo.providers.prompt_cache` — `CacheBreakpoints`,
  `compute_breakpoints`, `stable_prefix_size`,
  `annotate_anthropic_messages`, `cache_key_for_request`.
- `ModelRequest.cache` + `ModelRequest.cache_prefix_messages` — opt
  the request into per-provider cache hints. Anthropic providers
  inject `cache_control: {"type": "ephemeral"}` markers on the
  breakpoint and tail messages; OpenAI-compatible providers add a
  deterministic `prompt_cache_key` for cache partitioning.
- `tests/test_prompt_cache.py` — 12 tests covering breakpoint
  heuristics, Anthropic annotation, cache-key derivation, and
  end-to-end provider payload inspection.
- `avo.integrations.langchain_bridge` — `AvoLangChainModel`
  wrapping any avo `ModelProvider` as a LangChain `BaseChatModel`,
  plus `avo_messages_from_lc` (LC → avo message translation) and
  `avo_tool_from_lc` (LC `StructuredTool` → avo `FunctionTool`).
  Installed via `pip install avo[langchain]`; the bridge uses lazy
  imports so the rest of Avo never pulls LangChain at module load.
- `tests/test_langchain_bridge.py` — 10 tests covering role
  translation, tool-call fan-out, `BaseChatModel` sync + async
  entry points, streaming via `StreamingModelProvider`, and
  StructuredTool adaptation. Skips cleanly when `langchain-core`
  is not installed.
- `mkdocs.yml` + `docs/index.md`, `docs/cli.md`, `docs/changelog.md`,
  `docs/api/index.md` — Material for MkDocs site with light/dark
  palette, navigation tabs, mkdocstrings-powered API reference,
  and included changelog. Build with `mkdocs build --strict`
  (--strict fails on broken links, missing nav, malformed admonitions).
- `[docs]` optional extra — `mkdocs`, `mkdocs-material`,
  `mkdocstrings[python]`, `pymdown-extensions` for local preview
  and CI build.
- `.github/workflows/docs.yml` — strict build job on every push + PR
  touching docs / source / workflow; deploy job publishes to GitHub
  Pages on `main` only.
- `native/` PyO3 extension (`avo_native`) — streaming SHA-256 cache-key
  digest implemented in Rust via `pyo3` 0.23 with `abi3-py311` so a
  single wheel serves Python 3.11/3.12/3.13. Built with `maturin`;
  install with `pip install avo[native]` and `maturin build` from
  `native/`. The pure-Python fallback in
  `avo.providers.prompt_cache.cache_key_for_request` stays
  authoritative — both paths produce byte-identical keys.
- `avo._native` — lazy loader exposing `is_available()`, `version()`,
  `cache_key_hash_native()` for code that wants to short-circuit the
  import when the wheel is missing.
- `[native]` optional extra — `maturin>=1.5` so users can rebuild the
  wheel from source against their local interpreter.
- `.github/workflows/native.yml` — Linux build across Python 3.11
  through 3.13, wheel install, and the `test_native` + `test_prompt_cache`
  suites under the loaded extension.
- `netlify.toml` — publish config for the documentation site, set up
  so the user can deploy `site/` directly to Netlify without pulling
  in the GitHub Pages job.
- `.github/workflows/native-release.yml` — multi-platform
  `avo_native` wheel matrix via `PyO3/maturin-action@v1`:
  `x86_64/aarch64-unknown-linux-{gnu,musl}`, `x86_64/aarch64-apple-darwin`,
  and `x86_64/aarch64-pc-windows-msvc`. sccache caches the Rust build
  across matrix legs. Wheels attach to the GitHub release on tag
  pushes so `pip install avo_native` resolves to a binary that
  matches the avo release tag.

## [0.1.3] — 2026-09-01

### Added

- `avo.tools.to_json_schema`, `avo.tools.to_openai_function`,
  `avo.tools.to_anthropic_tool` — JSON Schema 2020-12 export helpers
  with OpenAI strict mode compliance (`additionalProperties: false`
  on every nested object).
- `avo.observability` — OpenTelemetry tracing behind the `[otel]`
  extra. Emits `gen_ai.*` semantic-convention spans per turn; no-op
  fallback when the extra is not installed.
- `avo.deprecation` — `@deprecated(since=, removal=, replacement=)`
  decorator and `deprecation_index()` for SemVer policy enforcement.
- `avo.bench` and `avo bench` CLI — deterministic benchmark harness
  with JSON report; foundation for cross-provider comparison.
- `avo.diff` and `avo runs diff` CLI — event log + token + step
  comparison between two persisted runs.
- `avo.cli_sandbox` and `avo sandbox run` CLI — Docker-as-a-service
  CLI independent of the agent loop.
- `docs/semver.md` — public SemVer commitment and deprecation policy.
- `docs/api-stability.md` — frozen public API surface declaration.
- `.github/dependabot.yml` — weekly dependency update PRs.
- `.github/workflows/scorecard.yml` — weekly OpenSSF Scorecard run.
- `.github/workflows/codeql.yml` — weekly CodeQL security analysis.
- `.github/workflows/release.yml` — release pipeline with
  Sigstore signing, SBOM, and PyPI publish.
- `scripts/audit.sh` — local mirror of the CI bandit + pip-audit
  gates for pre-PR runs.
- `src/avo/py.typed` marker verified for PEP 561 compliance.

### Changed

- `src/avo/runtime._drive` wraps each turn in an OpenTelemetry span
  when OTEL is enabled. Falls through to the no-op path otherwise.
- `src/avo/__init__.py` exports the new public helpers
  (`to_json_schema`, `to_openai_function`, `to_anthropic_tool`,
  `span_for_turn`, `configure_tracer`, `is_enabled`,
  `OtelDisabledError`, `deprecated`, `deprecation_index`,
  `DeprecatedSymbol`).

### Added

- `avo init` — scaffolds `.avo/skills/repo-overview/SKILL.md` and
  `AGENTS.md` in the current directory. Idempotent; respects existing
  files. Detects repo kind (python, node, rust, go, java, make, git,
  generic) and emits it in the output.
- Background jobs: a trailing `&` on a REPL line submits a turn as a
  background task. New slash commands: `/jobs`, `/job ID`, `/cancel ID`.
- `[jobs: N running]` indicator appended to the prompt when at least
  one background job is active.
- Typed `ContentBlock` model (`TextBlock`, `ImageBlock`) with
  per-provider translators for Anthropic, OpenAI, Ollama, and
  MiniMax.
- `/image <path>` slash command for one-off image input in the REPL.

### Changed

- `ChatContext` gained a `background: BackgroundJobManager` field.
- `run_shell` path uses `SandboxExecutor` exclusively; no direct
  `subprocess` host calls remain.
- Slash-command banner rendered as a multi-line box.
- Picker numbering aligned between `/sessions`, `/model`, `/skills`.

### Fixed

- `chat_shell_rc.py` now uses atomic write (`.tmp` + `os.replace`).
- Skill markdown frontmatter parser strips a leading UTF-8 BOM.
- `cli mcp add --env NAME=value` masks values whose key names suggest
  a secret (`*KEY`, `*TOKEN`, `*SECRET`, `*PASSWORD`, `*AUTH`,
  `*CREDENTIAL`).
- Destructive remove subcommands (`plugin remove`, `skill remove`,
  `mcp remove`) prompt for confirmation and accept `--yes` to skip.
- `provider` and `permission_mode` doctor output uses lowercase
  `yes` / `no` instead of capitalized booleans.
- README no longer duplicates the wordmark (SVG already shows it).
- Sweep of leftover `soteria` / `hernness` strings in code, docs,
  and benchmark fixtures.

## [0.1.2] — 2026-09-01

### Added

- Re-tag of 0.1.1 line with a documented release process (no code changes).

## [0.1.1] — 2026-07-21

### Added

- Re-tag of 0.1.0 line with corrected metadata (no code changes).

## [0.1.0] — 2026-07-21

### Added

- Initial alpha foundation.
- Provider-agnostic async state machine with strict `StopReason`
  taxonomy (13 enum values) and a finite set of `AgentState`
  transitions.
- Append-only event log persisted in SQLite.
- Deterministic `FakeProvider` for offline tests and replays.
- `SQLiteEventStore` with checkpoint snapshots and resume helpers.
- Provider adapters: Ollama (`/api/chat`), Anthropic Messages,
  OpenAI-compatible Chat Completions, MiniMax.
- Application tools: `read_file`, `write_file`, `edit_file`,
  `glob`, `grep`, `web_fetch`, `web_search`, `git_status`,
  `workspace_map`, `plan_tasks`, `task` (sub-agent dispatch).
- `Workspace` path validator; rejects `..`, absolute escapes,
  symlink leaves, null bytes, and empty strings.
- `AVO_TOOLS_REQUIRE_APPROVAL` env var drives per-tool approval
  callbacks.
- Permission modes: `default`, `accept_edits`, `plan`, `bypass`.
- Hook registry: `PreToolUse`, `PostToolUse`, `Stop`,
  `Notification`.
- MCP adapter: JSON-RPC 2.0 client + `FunctionTool` wrapper.
- Markdown skill loader (`<workspace>/.avo/skills/<name>/SKILL.md`).
- Cost tracking: `UsageTracker` + USD estimator + `TokenLedger`.
- Budget enforcement: `BudgetConfig` + `BudgetChecker`.
- Rate limiting: async token-bucket `RateLimiter`.
- Audit log: JSONL with deep secret redaction (suffix variants,
  JWT, AWS, GitLab, Stripe, query strings, cycle-safe, thread-safe,
  symlink-guarded).
- Eval harness: `EvalCase` + `EvalReport` with substring and
  tool-call assertions.
- Plugin discovery via `avo.tools`, `avo.providers`,
  `avo.notifiers` entry-point groups.
- Metrics: counter, gauge, histogram with label cardinality cap.
- Concurrency limiter: async semaphore + in-flight gauge.
- Schema registry for keyed Pydantic validation.
- Conversation store with persistent append-only turns.
- Retry policy with exponential backoff.
- Notification dispatcher (webhook + desktop).
- CLI subcommands: `avo runs list|inspect|resume`, `avo chat`,
  `avo doctor`.
- `project.md` deep technical reference; `README.md` user guide;
  `Makefile` for common dev tasks.

### Quality

- 219 offline tests in `tests/`.
- `mypy --strict` clean across `src/avo`.
- `ruff check` + `ruff format --check` clean.
- Coverage gate: `fail_under = 90`.

[Unreleased]: https://github.com/Fqih/avo/compare/v0.7.3...HEAD
[0.7.3]: https://github.com/Fqih/avo/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/Fqih/avo/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/Fqih/avo/compare/v0.1.7...v0.7.1
[0.1.6]: https://github.com/Fqih/avo/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/Fqih/avo/compare/v0.1.4...v0.1.5
[0.1.3]: https://github.com/Fqih/avo/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Fqih/avo/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Fqih/avo/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Fqih/avo/releases/tag/v0.1.0
