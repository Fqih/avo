# Changelog

All notable changes to avo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- `avo --version` — prints the package version (`avo 0.1.3`).
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
- `release.yml` publish step is now conditional on
  `secrets.PYPI_TOKEN`; the wheel + sdist + SBOM attach to the GitHub
  release regardless, so the release is verifiable even without a
  PyPI token configured.
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

[Unreleased]: https://github.com/Fqih/avo/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/Fqih/avo/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Fqih/avo/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Fqih/avo/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Fqih/avo/releases/tag/v0.1.0
