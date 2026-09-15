# API Stability Declaration

This document is the canonical list of avo's **public API surface**.
Any change to a symbol, signature, environment variable, or CLI
form listed here requires a SemVer bump per [`docs/semver.md`](./semver.md).

The public surface freezes at version **0.2.0**.

## Package layout

```
avo
├── agents        # reserved for future agent templates
├── app_tools     # sandbox, file tools, shell
├── audit         # JSONL audit log
├── budget        # budget enforcement
├── chat          # REPL orchestration
├── checkpoint    # run checkpoints
├── cli           # `avo` console-script entry
├── cli_init      # `avo init` scaffold
├── cli_mcp       # `avo mcp` registry
├── cli_plugins   # `avo plugin` registry
├── cli_skills    # `avo skill` registry
├── compact       # context-window compaction
├── concurrency   # async semaphore + gauge
├── config        # `build_provider_from_env`
├── content_blocks# typed ContentBlock union
├── doctor        # `avo doctor` diagnostics
├── eval          # EvalCase + EvalReport
├── events        # AgentEvent + EventType
├── exceptions    # AvoError hierarchy
├── hooks         # hook registry
├── integrations  # memory adapters
├── ledger        # TokenLedger
├── mcp           # MCP adapter
├── mcp_servers   # built-in MCP servers
├── metrics       # MetricsRegistry
├── models        # Checkpoint, ToolCall, etc.
├── notifiers     # webhook + desktop
├── policies      # LoopPolicy, PermissionPolicy
├── progress      # ProgressDetector
├── providers     # provider adapters
│   ├── anthropic
│   ├── fake
│   ├── http_common
│   ├── minimax
│   ├── ollama
│   └── openai_compatible
├── rate_limit    # RateLimiter
├── retry         # RetryPolicy
├── runtime       # AgentRuntime
├── schemas       # SchemaRegistry
├── skills        # SkillRegistry
├── state         # RunState, StopReason
├── storage       # SQLiteEventStore
├── tools         # FunctionTool, ToolRegistry
├── tracing       # TraceInspector
└── usage         # UsageTracker
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

Stable since 0.1.0:

```text
avo [--database PATH] <command> [...]
```

Subcommands:

- `avo runs list` — list persisted runs.
- `avo runs inspect RUN_ID` — render one run trace.
- `avo runs resume RUN_ID` — resume a `FakeProvider` run.
- `avo chat` — interactive REPL.
- `avo doctor` — verify provider config without HTTP.
- `avo plugin` — third-party plugin registry.
- `avo mcp` — MCP server registry.
- `avo skill` — skill registry.
- `avo init` — workspace scaffold.

Global flags:

- `--database / -d PATH` — SQLite path (default `avo.db`).

## Environment variables

Stable since 0.1.0:

| Name | Purpose |
| --- | --- |
| `AVO_PROVIDER` | Active provider (`ollama`, `anthropic`, `openai`, `minimax`). |
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
| `AVO_USAGE_RATES_INPUT_PER_1K` | USD per 1K input tokens (cost estimator). |
| `AVO_USAGE_RATES_OUTPUT_PER_1K` | USD per 1K output tokens. |
| `AVO_NOTIFY_WEBHOOK` | Webhook URL for `Notification` hook events. |
| `AVO_NOTIFY_DESKTOP` | `1` to enable desktop notifications. |

Optional-extras environment additions are documented per-extra and
follow the same naming convention (`AVO_<EXTRA>_*`).

## Slash commands

Stable since 0.1.0 (REPL):

- `/provider`, `/model`
- `/skills`, `/skill NAME`
- `/sessions`, `/session ID`, `/new`, `/resume`
- `/inspect RUN_ID`
- `/image PATH`
- `/jobs`, `/job ID`, `/cancel ID`
- `/help`

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
