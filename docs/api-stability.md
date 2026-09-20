# API Stability Declaration

This document is the canonical list of avo's **public API surface**.
Any change to a symbol, signature, environment variable, or CLI
form listed here requires a SemVer bump per [`docs/semver.md`](./semver.md).

The public surface stabilizes through **0.7.x** towards a frozen contract
at version **1.0.0**.

## Package layout

```
avo
├── agent_profiles# @mention agent profiles & parsing
├── agents        # reserved for future agent templates
├── app_tools     # sandbox, file tools, shell, terminal
├── audit         # JSONL audit log
├── auth          # OAuth authentication & store
├── budget        # budget enforcement
├── capabilities  # capability metadata & tool classification
├── chat          # REPL orchestration
├── checkpoint    # run checkpoints
├── cli           # `avo` console-script entry
├── cli_init      # `avo init` scaffold
├── cli_mcp       # `avo mcp` registry
├── cli_models    # `avo models` discovery
├── cli_plugins   # `avo plugin` registry
├── cli_sandbox   # `avo sandbox` verification
├── cli_setup     # `avo setup` onboarding
├── cli_skills    # `avo skill` registry
├── combo         # multi-tier failover routing
├── compact       # context-window compaction
├── concurrency   # async semaphore + gauge
├── config        # `build_provider_from_env`
├── config_resolver# canonical security & runtime config resolution
├── content_blocks# typed ContentBlock union
├── credentials   # pluggable credential storage backends
├── delegation    # bounded isolated sub-agent delegation & pipelines
├── diff          # run comparison
├── doctor        # `avo doctor` diagnostics
├── eval          # EvalCase + EvalReport
├── events        # AgentEvent + EventType
├── exceptions    # AvoError hierarchy
├── hardware      # local GPU accelerator discovery
├── hooks         # hook registry
├── integrations  # memory adapters
├── ledger        # TokenLedger
├── mcp           # MCP adapter
├── mcp_servers   # built-in MCP servers
├── metrics       # MetricsRegistry
├── model_catalog_service # provider-neutral model catalog cache
├── model_discovery # live model discovery per provider
├── models        # Checkpoint, ToolCall, etc.
├── notifiers     # webhook + desktop
├── oauth         # OAuth PKCE & device authorization flows
├── permissions   # permission modes & approval callbacks
├── persona       # persona management
├── policies      # LoopPolicy, PermissionPolicy
├── progress      # ProgressDetector
├── provider_catalog # catalog metadata per provider
├── providers     # provider adapters
│   ├── anthropic
│   ├── cerebras
│   ├── codex
│   ├── fake
│   ├── gemini
│   ├── gemini_cli
│   ├── groq
│   ├── http_common
│   ├── minimax
│   ├── ollama
│   ├── openai
│   ├── openai_compatible
│   ├── openrouter
│   └── router
├── rate_limit    # RateLimiter
├── retry         # RetryPolicy
├── runtime       # AgentRuntime
├── savers        # deterministic token savers
├── schemas       # SchemaRegistry
├── skills        # SkillRegistry
├── state         # RunState, StopReason
├── storage       # SQLiteEventStore
├── tools         # FunctionTool, ToolRegistry
├── tracing       # TraceInspector
├── usage         # UsageTracker
├── web_api       # Web UI JSON API routes
├── web_http      # HTTP server mixins & security
├── web_pages     # Web UI dashboard page serving
├── web_playground# Web UI model playground
├── web_runs      # Web UI run history & traces
├── web_security  # destination URL validation (SSRF)
├── web_ui        # Web UI server entrypoint
└── web_workspace # Web UI workspace tree & file inspection
```

## Top-level package exports (`from avo import …`)

Stable since 0.1.0:

- `AgentEvent`, `AgentRuntime`
- `Checkpoint`
- `EventType`
- `FakeProvider`
- `FunctionTool`
- `LetheMemoryAdapter`, `MemoryProvider`
- `LoopPolicy`
- `ModelRequest`, `ModelResponse`
- `ProgressDetector`
- `RunRecord`, `RunResult`, `RunState`, `RunTrace`
- `ScriptItem`
- `StopReason`
- `TokenUsage`
- `Tool`, `ToolCall`, `ToolMetadata`, `ToolRegistry`, `ToolResult`
- `TraceEntry`, `TraceInspector`
- `__version__`

## Provider adapter protocol

Stable since 0.1.0 (see `avo.providers._base`):

```python
class ProviderAdapter(Protocol):
    name: str

    async def complete(self, request: ModelRequest) -> ModelResponse: ...
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]: ...  # optional
```

Additions after 0.2.0 must be additive (new optional methods with
default implementations).

## FunctionTool contract

Stable since 0.1.0:

```python
@dataclass
class FunctionTool:
    name: str
    description: str
    arguments_model: type[BaseModel]
    function: Callable[..., Awaitable[Any]]

    async def __call__(self, arguments: BaseModel) -> Any: ...
```

## Tool registry

Stable since 0.1.0:

```python
class ToolRegistry:
    def register(self, tool: FunctionTool) -> None: ...
    async def invoke(
        self, call: ToolCall, *, completed_tool_call_ids: set[str]
    ) -> ToolResult: ...
```

## State machine

Stable since 0.1.0:

- `RunState` enum (12 values): `CREATED`, `MODEL_PENDING`,
  `DECISION_RECEIVED`, `TOOL_PENDING`, `APPROVAL_PENDING`,
  `TOOL_EXECUTING`, `OBSERVATION_RECORDED`, `PAUSED`, `COMPLETED`,
  `FAILED`, `STOPPED`, `CANCELLED`.
- `StopReason` enum (13 values): see `avo.state.StopReason`.

## Event log

Stable since 0.1.0:

- Append-only SQLite log keyed by `run_id`.
- Migration path is additive (`schema_version` column is read on open).
- Event types in `EventType` enum (see `avo.events`).

## Hooks

Stable since 0.1.0:

- Events: `PreToolUse`, `PostToolUse`, `Stop`, `Notification`.
- Signature: `async def hook(event: AgentEvent, ctx: RunContext) -> HookDecision`.

## CLI surface

Stable forms:

```text
avo [--database PATH] [command] [...]
```

Subcommands:

- `avo` / `avo chat` — interactive REPL with auto-discovery and session resume.
- `avo combo [list|show|new|rm|auth]` — multi-tier model failover profiles.
- `avo saver [list|show|use|off]` — deterministic token-saver preset management.
- `avo models [discover|list|inspect]` — dynamic model discovery and local catalog.
- `avo runs list` — list persisted runs.
- `avo runs inspect RUN_ID` — render one run trace.
- `avo runs replay RUN_ID` — deterministic replay of persisted runs.
- `avo runs resume RUN_ID` — resume an incomplete run.
- `avo cost` — aggregate expenditure reporting from the ledger.
- `avo diff RUN_A RUN_B` — structured comparison between two persisted runs.
- `avo doctor` — diagnostics for providers, sandbox, permissions, and tools.
- `avo login [PROVIDER]` — interactive OAuth authentication and credential storage.
- `avo setup` — global workstation onboarding wizard.
- `avo plugin [install|list|show|remove|init]` — third-party plugin registry.
- `avo mcp [add|list|remove]` — MCP server registry.
- `avo skill [install|list|show|remove]` — skill registry.
- `avo init` — workspace scaffold.

Global flags:

- `--database / -d PATH` — SQLite path (default `avo.db`).

## Web Control-Plane HTTP API surface

When running `avo ui` or programmatic dashboard servers:

Endpoints:
- `GET /` — embedded dashboard SPA (with one-time `#token=` bootstrap).
- `POST /api/session` — session token exchange (returns `Set-Cookie` + CSRF token).
- `GET /api/status` — system diagnostics, masked credentials, active provider/model.
- `GET /api/cost` — aggregate token consumption and cost metrics.
- `GET /api/persona` — list active and registered persona profiles.
- `POST /api/persona` — switch active persona or register a custom persona.
- `GET /api/permissions` — active permission mode and approval list.
- `POST /api/permissions` — switch permission mode (`confirm: true` required).
- `GET /api/provider` — active provider and model status.
- `POST /api/provider` — hot-swap active provider and model (`confirm: true` required).
- `GET /api/router` — multi-tier fallback chain health and latency metrics.
- `GET /api/workspace/tree` — bounded workspace directory hierarchy.
- `GET /api/workspace/file` — read workspace file content (max 2MB, text only).
- `POST /api/workspace/file` — save workspace file mutations (`confirm: true` required).
- `GET /api/git` — workspace git status and branches.
- `POST /api/git/commit` — stage and commit changes (`confirm: true` required).
- `GET /api/runs` — list runs with status, steps, and duration.
- `GET /api/runs/<run_id>` — structured event trace for a specific run.
- `GET /api/sessions` — list conversation sessions.
- `GET /api/sessions/<session_id>` — session turn history and metadata.
- `POST /api/playground/generate` — test prompt generation against active provider.
- `POST /api/playground/stream` — SSE stream generation for playground testing.

Security & Authentication:
- Host binding defaults to loopback (`127.0.0.1`).
- All mutations require origin validation, bearer token or session cookie, CSRF header (`X-CSRF-Token`), and explicit confirmation (`{"confirm": true}`).
- CORS wildcard origins are rejected.

## Environment variables

Stable forms:

| Name | Purpose |
| --- | --- |
| `AVO_PROVIDER` | Active provider (`ollama`, `anthropic`, `openai`, `minimax`, `codex`, `gemini`, `gemini-cli`, `openrouter`, `groq`, `cerebras`, `router`, `combo`). |
| `AVO_MODEL` | Default model for the active provider. |
| `AVO_<PROVIDER>_API_KEY` | API key for a specific provider. |
| `AVO_<PROVIDER>_BASE_URL` | Base URL override for a specific provider. |
| `AVO_<PROVIDER>_MODEL` | Per-provider model override. |
| `AVO_MINIMAX_API_STYLE` | `anthropic` or `openai` request shape. |
| `AVO_DATABASE_PATH` | Default SQLite path. |
| `AVO_MAX_TOTAL_TOKENS` | Token budget per run. |
| `AVO_MAX_RUNTIME_SECONDS` | Wall-clock budget per run. |
| `AVO_REPEATED_ACTION_LIMIT` | Loop guard threshold. |
| `AVO_PERMISSION_MODE` | `default` / `accept_edits` / `plan` / `bypass`. |
| `AVO_TOOLS_REQUIRE_APPROVAL` | Comma-separated tool names requiring approval. |
| `AVO_SANDBOX_REQUIRED` | Require sandbox availability before execution (default `1`). |
| `AVO_SANDBOX_NETWORK` | Allow sandbox networking (default `0`). |
| `AVO_SANDBOX_TIMEOUT_SECONDS` | Bounded execution timeout, 0–3600 seconds. |
| `AVO_PLUGIN_EDITABLE` | Permit editable plugin installs only after explicit confirmation. |
| `AVO_PLUGIN_ACTIVATION` | Permit plugin activation only after explicit confirmation. |
| `AVO_WEB_ALLOWED_ORIGIN` | One explicit non-wildcard dashboard origin. |
| `AVO_WEB_CORS_ENABLED` | Enable configured web CORS (default `0`). |
| `AVO_SAVER` | Active token-saver preset (`terse`, `yagni`, `compact`, `full`). |
| `AVO_COMBOS_FILE` | Path override for combo routing profiles. |
| `AVO_BUDGET_HARD_LIMIT_USD` | Hard spend cap in USD. |
| `AVO_BUDGET_WARNING_USD` | Warning spend threshold in USD. |
| `AVO_USAGE_RATES_INPUT_PER_1K` | USD per 1K input tokens (cost estimator). |
| `AVO_USAGE_RATES_OUTPUT_PER_1K` | USD per 1K output tokens. |
| `AVO_NOTIFY_WEBHOOK` | Webhook URL for `Notification` hook events. |
| `AVO_NOTIFY_DESKTOP` | `1` to enable desktop notifications. |

Optional-extras environment additions are documented per-extra and
follow the same naming convention (`AVO_<EXTRA>_*`).

## Slash commands

Stable forms (REPL):

- `/provider` — show provider/model status.
- `/model [NAME]` — list known models or switch active model.
- `/combo [NAME]` — show tier health or switch combo profile.
- `/saver [PRESET]` — show or switch token saver preset.
- `/skills`, `/skill NAME` — list or run skills.
- `/sessions`, `/session ID`, `/new`, `/resume` — manage session threads.
- `/replay [RUN_ID]` — replay event ledger.
- `/inspect RUN_ID` — inspect run traces.
- `/context` — inspect active context and attachments.
- `/cost` — view session expenditure.
- `/permissions [MODE]` — view or switch permission mode.
- `/export [PATH]` — export session to markdown.
- `/image PATH` — attach an image.
- `/jobs`, `/job ID`, `/cancel ID` — background task management.
- `/help` — display command help.

## Internal surfaces (NOT public)

The following may change without notice:

- `avo._pytest_plugin` (test plugin entry point).
- SQLite schema details beyond the additive-migration guarantee.
- `tracing.TraceInspector` text output format.
- `avo_core` PyO3 surface (when applicable).
- Any module prefixed with a single underscore (`_`).

## Adding to this document

When you add a new public export, environment variable, CLI form,
or slash command:

1. Open a PR that updates this file in the same commit.
2. Note the change in `CHANGELOG.md` under the appropriate version.
3. If the addition is a breaking change, follow the SemVer policy
   in `docs/semver.md`.
