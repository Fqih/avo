# S2 — Combo Routing: One Conversation, Many Models

- Status: draft specification for approval (2026-09-16)
- Branch: `feat/combo-routing`
- Related: S1 (Subscription OAuth & Universal Login — shipped in `feat/subscription-oauth`), S3 (Ollama Free Tier — folded into S2)
- Upstream reference: [BerriAI/liteLLM](https://github.com/BerriAI/litellm) (MIT, error classification & rate-limit fallback matrix as data reference), [decolua/9router](https://github.com/decolua/9router) (model fallback structure)

---

## 1. Goal

Allow an Avo AI agent session to operate across **multiple tiered models in a single conversation thread**:
1. **Named Tiers**: Organize models into semantic priority tiers (e.g. `subscription` / `primary` → `cheap` / `fast` → `free` / `local Ollama`).
2. **Transparent Quota & Rate-Limit Failover**: Automatically switch to the next tier when a provider encounters HTTP 429, quota exhaustion, or circuit-breaker cooldown without terminating the user turn or losing conversation state.
3. **Durable Event Logging**: Record every tier switch into Avo's SQLite Event Store as an append-only, sequenced `AgentEvent` (`EventType.ROUTE_FAILOVER`), ensuring complete observability, auditability, and replayability.
4. **Persistent Profiles**: Define, inspect, and select combos via CLI (`avo combo new|list|show|rm`) or config (`~/.config/avo/combos.json`, `AVO_COMBO=<name>`).
5. **First-Class Ollama Free Tier**: Native out-of-the-box integration for local Ollama models (e.g. `llama3.2`, `qwen2.5-coder`) as the zero-cost fallback floor.

### Non-Goals (Other Subprojects)
- Native pyo3 performance benchmarks (S5).
- Input token compression / RTK-style prompt reduction (S6).
- Public GitHub README & demo video polish (S4).

---

## 2. Current State & Gap Analysis

- **Existing Low-Level Router** (`src/avo/providers/router.py`):
  - Provides `BaseRouterProvider`, `FallbackRouterProvider`, and `RaceRouterProvider`.
  - Supports sequential fallback across static provider tuples and circuit-breaker cooldowns (`RouteHealth`).
  - Gaps:
    - No persistent combo profiles (requires manually specifying comma-separated env vars `AVO_ROUTER_PROVIDERS` and `AVO_ROUTER_MODELS`).
    - No tier naming semantics (`subscription`, `cheap`, `free`).
    - Fallbacks only log to Python `logging.getLogger()`; they do **not** persist into `EventStore`, making run history blind to which model answered each step.
    - No CLI management (`avo combo ...`).
    - No REPL slash command or visual feedback when a fallback occurs.

- **Available Providers in Avo**:
  - Subscription OAuth: `anthropic` (Claude Pro/Max), `codex` (ChatGPT Plus/Pro), `gemini_cli` (Google AI).
  - API Key & Local: `ollama`, `openrouter`, `groq`, `cerebras`, `minimax`, `openai`.

---

## 3. Architecture & Component Design

```
src/avo/
├── combo/
│   ├── __init__.py           # Public exports: ComboProfile, ComboManager, build_combo_provider
│   ├── models.py             # Pydantic models: ComboTier, ComboProfile, ComboCatalog
│   ├── store.py              # Persistence: ~/.config/avo/combos.json (0600, JSON schema)
│   ├── detector.py           # Quota & 429 error classifier (liteLLM-inspired heuristics)
│   └── provider.py           # ComboRouterProvider: implements StreamingModelProvider with EventStore notification
├── events.py                 # EventType.ROUTE_FAILOVER enum value
├── cli.py                    # avo combo {new,list,show,rm} CLI subcommands
├── chat_render.py            # REPL banner & notification formatting for combo sessions
└── chat_commands.py          # /combo slash command to inspect/swap active combo
```

---

## 4. Data Models (`src/avo/combo/models.py`)

```python
class ComboTier(BaseModel):
    name: str                       # e.g. "subscription", "cheap", "free", "primary"
    provider: str                   # e.g. "claude", "codex", "openrouter", "ollama"
    model: str                      # e.g. "claude-sonnet-5", "llama3.2"
    timeout_seconds: float = 60.0
    cooldown_seconds: float = 60.0

class ComboProfile(BaseModel):
    name: str                       # e.g. "coder", "general", "budget"
    description: str = ""
    tiers: list[ComboTier]          # Ordered in priority sequence (0 = highest)
    created_at: datetime
    updated_at: datetime
```

### Storage Location
- Default: `~/.config/avo/combos.json` (overridable via `AVO_CONFIG_DIR`).
- Permissions: 0600 file mode.
- Shipped Built-In Presets:
  - `default`: `subscription` (if logged in) → `cheap` (openrouter) → `free` (ollama).
  - `coder`: `claude` (subscription) → `openrouter/meta-llama/llama-3.3-70b-instruct` → `ollama/qwen2.5-coder:7b`.
  - `budget`: `ollama/llama3.2` (free) → `groq/llama-3.3-70b-versatile` (cheap).

---

## 5. Quota & Rate-Limit Detection Matrix (`src/avo/combo/detector.py`)

A route failure triggers failover to the next tier if the error matches quota, rate-limit, or circuit-breaker conditions:

| Condition | Indicators | Failover Action |
|-----------|------------|-----------------|
| HTTP 429 | Status code `429`, `"Too Many Requests"`, `"rate_limit"` | Mark route cooldown, switch to next tier |
| Quota Exhausted | `"insufficient_quota"`, `"quota_exceeded"`, `"credit_balance"`, `"billing"` | Mark route cooldown, switch to next tier |
| Resource Exhausted | HTTP 503, `"model_overloaded"`, `"capacity_exceeded"` | Mark route cooldown, switch to next tier |
| Circuit Breaker | `time.monotonic() < h.cooldown_until` | Skip route, try next tier |
| Syntax / Bad Request | HTTP 400, schema mismatch | Fail immediately (do not failover) |
| Auth Failure | HTTP 401, `"invalid_api_key"` | Fail immediately with auth error |

---

## 6. Durable Event Logging (`EventType.ROUTE_FAILOVER`)

When a failover occurs during `runtime.run()`, `ComboRouterProvider` invokes a callback that persists a durable event:

```json
{
  "event_type": "route_failover",
  "payload": {
    "combo": "coder",
    "from_tier": "subscription",
    "from_provider": "claude",
    "from_model": "claude-sonnet-5",
    "to_tier": "cheap",
    "to_provider": "openrouter",
    "to_model": "meta-llama/llama-3.3-70b-instruct",
    "reason": "rate_limited_429",
    "error_snippet": "HTTP 429: Usage limit exceeded for tier",
    "attempt": 1
  }
}
```

Invariants:
- Appended during `STATE_CHANGED` / `MODEL_REQUESTED` before the successful `MODEL_RESPONDED`.
- Traceable via `avo runs inspect <run_id>`.

---

## 7. CLI & REPL Experience

### CLI Commands
- `avo combo list`: List configured combo profiles with tier breakdown and status.
- `avo combo new <name> --tier <name>=<provider>/<model> ...`: Create or update a combo.
- `avo combo show <name>`: Show profile details, timeout, and route health.
- `avo combo rm <name>`: Remove a custom combo profile.

### REPL Integration
- Startup Banner displays active combo profile:
  `provider: combo (coder) · tiers: [subscription → cheap → free]`
- Failover Notice:
  `⤾ Fallback: switched from 'subscription' (Claude) to 'cheap' (OpenRouter) [rate_limited_429]`
- Slash Command `/combo [NAME]`: View active combo tiers or switch to another combo.

---

## 8. Test & Verification Discipline

- 100% offline unit tests via `FakeProvider` and mock HTTP responses.
- Tests cover:
  1. Profile serialization/deserialization with JSON schema.
  2. Failover on simulated 429 and quota error messages.
  3. Preservation of fail-closed behavior on 400 Bad Request.
  4. Durable event persistence in SQLite event store.
  5. Recovery when cooldown expires.
  6. CLI commands (`combo list/new/show/rm`).
  7. REPL banner and notice formatting.

---

## 9. Definition of Done (S2)

1. `avo combo` CLI allows creating, listing, inspecting, and deleting profiles.
2. `AVO_PROVIDER=combo AVO_MODEL=<name>` runs an AgentRuntime turn with multi-tier failover.
3. Mid-turn 429 or quota error switches models seamlessly and records `route_failover` event in SQLite.
4. Full test suite passes cleanly (`pytest`, `ruff check`, `ruff format --check`, `mypy src/avo`).
5. Complete user documentation added to MkDocs site.
