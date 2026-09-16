# Combo Routing: Multi-Tier Model Failover

Combo Routing allows a single Avo conversation to orchestrate multiple models across prioritized tiers (for example: **subscription** &rarr; **cheap API** &rarr; **free local floor**).

When rate limits (HTTP 429) or quota errors occur mid-turn, Avo transparently fails over to the next configured tier, persisting full run history and recording a `route_failover` event in the SQLite ledger.

---

## 1. Architecture & Tier Concepts

```
┌─────────────────────────────────────────────────────────┐
│                      Avo Runtime                        │
└───────────────────────────┬─────────────────────────────┘
                            │
              ┌─────────────▼─────────────┐
              │    ComboRouterProvider    │
              └─────────────┬─────────────┘
                            │
      ┌─────────────────────┼─────────────────────┐
      │ (Tier 1: Primary)   │ (Tier 2: Cheap)     │ (Tier 3: Free Floor)
┌─────▼──────────────┐┌─────▼──────────────┐┌─────▼──────────────┐
│ Claude / ChatGPT   ││ OpenRouter / Groq  ││ Ollama (Local)     │
│ (Subscription)     ││ (Pay-per-token)    ││ (Zero Cost / Free) │
└────────────────────┘└────────────────────┘└────────────────────┘
         │ (429/Quota)         │ (429/Quota)
         └──────► Fallback ────┴──────► Fallback ────► Done
```

### Failover Trigger Matrix

| Condition | Detected Error Signatures | Action |
|---|---|---|
| **Rate Limited (429)** | `429 Too Many Requests`, `rate_limit_exceeded` | Failover to next tier, mark current tier cooling down |
| **Quota / Credits Exhausted** | `insufficient_quota`, `credit balance too low`, `RESOURCE_EXHAUSTED` | Failover to next tier, mark current tier cooling down |
| **Client Error (400)** | `invalid_argument`, `context_length_exceeded` | **Fail-closed**: raise error immediately (do not mask bug) |
| **Authentication Error (401)** | `invalid_api_key`, `unauthorized` | **Fail-closed**: raise error immediately |

---

## 2. Built-in Preset Combos

Avo includes three built-in presets out of the box:

| Preset Name | Tier 1 (Subscription) | Tier 2 (Cheap API) | Tier 3 (Free Local Floor) |
|---|---|---|---|
| **`default`** | `claude` (`claude-sonnet-5`) | `openrouter` (`llama-3.3-70b-instruct`) | `ollama` (`llama3.2`) |
| **`coder`** | `claude` (`claude-sonnet-5`) | `openrouter` (`llama-3.3-70b-instruct`) | `ollama` (`qwen2.5-coder:7b`) |
| **`budget`** | *(none)* | `openrouter` (`llama-3.3-70b-instruct`) | `ollama` (`llama3.2`) |

---

## 3. CLI Management (`avo combo`)

Use the `avo combo` command-line tool to list, create, inspect, and remove combo profiles:

### Listing Combos

```bash
avo combo list
```

Output:
```text
NAME              TIERS                                          DESCRIPTION
budget            cheap (openrouter/meta-llama/...) -> free...   OpenRouter Llama -> Local Ollama
coder             subscription (claude/claude-sonnet-5) -> ...   Claude Sonnet -> OpenRouter Llama -> Local Qwen Coder
default           subscription (claude/claude-sonnet-5) -> ...   Subscription Claude -> OpenRouter Llama -> Local Ollama
```

To emit machine-readable JSON:
```bash
avo combo list --json
```

### Inspecting a Profile

```bash
avo combo show coder
```

Output:
```text
Profile     : coder (built-in preset)
Description : Claude Sonnet -> OpenRouter Llama -> Local Qwen Coder
Tiers (priority order):
  1. subscription    provider=claude     model=claude-sonnet-5      timeout=60.0s cooldown=60.0s
  2. cheap           provider=openrouter model=meta-llama/llama-... timeout=60.0s cooldown=60.0s
  3. free            provider=ollama     model=qwen2.5-coder:7b     timeout=60.0s cooldown=60.0s
```

### Creating a Custom Combo

Use `avo combo new` to define custom tiers. Tier specifications follow `[name:]provider:model[:timeout[:cooldown]]`:

```bash
avo combo new team_pipeline \
  --tier primary:anthropic:claude-3-5-sonnet-latest:60:120 \
  --tier secondary:openrouter:meta-llama/llama-3.3-70b-instruct:45:60 \
  --tier local:ollama:llama3.2:30:30 \
  --description "Production pipeline with local fallback"
```

### Removing a Custom Combo

```bash
avo combo rm team_pipeline
```

> [!NOTE]
> Built-in presets (`default`, `coder`, `budget`) cannot be deleted, but you can override them with a custom profile of the same name. Deleting the override restores the built-in preset.

---

## 4. Interactive Chat REPL Integration

### Starting the REPL with a Combo

Set `AVO_PROVIDER=combo` and select your profile via `AVO_COMBO`:

```bash
export AVO_PROVIDER=combo
export AVO_COMBO=coder
avo chat
```

The startup banner displays the active combo configuration:

```text
       ▄██▄           Avo CLI 0.1.0
     ▄██████▄         Fqih
    ███    ███        provider: combo · model: coder [subscription -> cheap -> free]
   ███  ▄▄  ███       workspace: ~/Project/Loopward
   ███  ▀▀  ███       session: a1b2c3d4e5f6
  ──────────────────────────────────────────────────────
```

### The `/combo` Slash Command

Inspect the live health and circuit breaker status of each tier during a session:

```text
/combo
```

Output:
```text
Active Combo Profile: coder
Description: Claude Sonnet -> OpenRouter Llama -> Local Qwen Coder

Tiers (priority order):
  Tier           Provider     Model                    Status    Cooldown  Latency  Fails
  -------------- ------------ ------------------------ --------- --------- -------- -----
  subscription   claude       claude-sonnet-5          HEALTHY   0s        240.2ms  0
  cheap          openrouter   meta-llama/llama-3.3...  HEALTHY   0s        -        0
  free           ollama       qwen2.5-coder:7b         HEALTHY   0s        -        0

Available combo profiles: budget, coder, default, team_pipeline
Switch with: /combo <NAME>
```

### Hot-Swapping Combos Mid-Session

You can switch combo profiles dynamically without restarting the chat:

```text
/combo budget
```

```text
Switched to combo profile 'budget'. Next turn will route through its tiers.
```

### Live Fallback Notices

When a tier fails due to quota or rate limits, Avo displays an inline notice before streaming from the fallback tier:

```text
⤾ Fallback: switched from 'subscription' (claude) to 'cheap' (openrouter) [rate_limited_429]
```

The conversation proceeds smoothly and the reply is rendered without interruption.

---

## 5. Setting up Ollama as the Free Tier Floor

Using Ollama as your lowest tier guarantees that your agent loops will never crash due to vendor outages, rate limits, or billing issues.

1. **Install and launch Ollama:**
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama serve
   ```

2. **Pull the fallback models:**
   ```bash
   ollama pull llama3.2
   ollama pull qwen2.5-coder:7b
   ```

3. **Verify connectivity:**
   ```bash
   curl http://localhost:11434/api/tags
   ```

Avo's default Ollama provider connects to `http://localhost:11434` unless `AVO_OLLAMA_BASE_URL` is set.
