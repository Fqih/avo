<div align="center">

<img src="logo.svg" width="220" alt="Avo Logo">

# Avo

**The Resilient, Observable AI Agent Runtime with Multi-Tier Combo Routing & Subscription OAuth**

*One conversation, many models. Automatic failover down to local Ollama. Zero core dependencies.*

[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests: 1331 passed](https://img.shields.io/badge/tests-1331%20passed-brightgreen.svg)](tests/)
[![Coverage: ≥90%](https://img.shields.io/badge/coverage-%E2%89%A590%25-brightgreen.svg)](tests/)
[![Docs](https://img.shields.io/badge/docs-fqih.cloud-indigo.svg)](https://fqih.cloud/)

</div>

---

**Avo** is an observable, resilient AI agent runtime that unites your Claude Pro, ChatGPT Plus, and Gemini subscriptions with multi-tier combo failover down to local Ollama. When API quotas or rate limits hit mid-flight, Avo switches models seamlessly without losing conversational state or task execution. Built on an event-sourced SQLite ledger with deterministic replay, safe workspace tools, and zero extra core dependencies (Pydantic only).

---

## What Makes Avo Different?

```
┌─────────────────────────────────────────────────────────────┐
│                      Avo Runtime                            │
└───────────────────────────┬─────────────────────────────────┘
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

### 1. 🔑 Subscription OAuth & Universal Login
Reuse your existing **Claude Pro/Team**, **ChatGPT Plus/Team (Codex)**, or **Google Gemini CLI** subscriptions via standard PKCE browser flows (`avo login claude`, `avo login codex`, `avo login gemini`). Tokens are encrypted with `chmod 0600`, refreshed automatically in the background with deduplication, and stored alongside API keys in `~/.config/avo/auth.json`.

### 2. 🔀 Multi-Tier Combo Routing & Quota Failover
Never suffer crashed agent runs from HTTP 429 or exhausted token quotas again. Configure named combo profiles (`default`, `coder`, `budget`) where models are organized in priority order (`subscription` &rarr; `cheap API` &rarr; `free local floor`). Avo detects rate limits and credit exhaustion mid-turn, switches to the next tier, and continues streaming.

### 3. 📜 Event-Sourced Ledger & Replay
Every decision, prompt, tool call, output, and failover is recorded as an immutable event in a durable SQLite ledger. Any past run can be inspected with chronological traces (`avo runs inspect <id>`) or resumed deterministically (`avo runs resume <id>`).

### 4. 🛡️ Hardened Workspace & Ephemeral Sandboxing
File tools strictly enforce POSIX `O_NOFOLLOW` boundaries—null bytes, symlink escapes, and `../` traversal are blocked before any I/O occurs. Shell commands run in isolated, ephemeral Docker containers with CPU/memory limits and disabled networking by default.

### 5. 🪶 Zero Extra Core Dependencies
The core agent loop, state machine, event store, and providers require **only Pydantic**. Additional capabilities (Docker sandbox, OpenTelemetry, CLI extras) remain opt-in.

---

## Terminal Visual Walkthrough

Experience interactive agent loops with real-time model failover:

```text
$ export AVO_PROVIDER=combo
$ export AVO_COMBO=coder
$ avo chat

       ▄██▄           Avo CLI 0.1.0
     ▄██████▄         Fqih (Subscription)
    ███    ███        provider: combo · model: coder [subscription -> cheap -> free]
   ███  ▄▄  ███       workspace: ~/Project/Loopward
   ███  ▀▀  ███       session: c8f921ab04e1
  ──────────────────────────────────────────────────────

> Analyze the authentication flow in src/avo/auth.py and write unit tests

⠋ Thinking...
⤾ Fallback: switched from 'subscription' (claude) to 'cheap' (openrouter) [rate_limited_429]

I've analyzed `src/avo/auth.py`. Here is the architecture breakdown and test suite...
```

---

## Quickstart (Under 2 Minutes)

### 1. Installation

Requires Python 3.11+.

```bash
git clone https://github.com/Fqih/avo.git
cd avo
python -m pip install -e ".[dev,providers,sandbox]"
```

### 2. Authenticate

Log in via subscription OAuth or plain API keys:

```bash
# OAuth Subscription login (Claude, ChatGPT Codex, or Gemini)
avo login claude

# Or store plain API keys securely
avo login openrouter --key-stdin
```

Verify your environment configuration with one command:
```bash
avo doctor
```

### 3. Interactive Chat REPL

Launch the agent with multi-tier combo routing:

```bash
AVO_PROVIDER=combo AVO_COMBO=coder avo chat
```

Inside the REPL:
- Type `/combo` to view real-time tier health and latencies.
- Type `/combo budget` to hot-swap to another profile.
- Type `/diff` to inspect uncommitted workspace modifications.
- Type `/model` to pick a specific standalone model.

---

### 4. Python API Example

Embed Avo's resilient loop directly into your Python service:

```python
import asyncio
from pydantic import BaseModel

from avo import AgentRuntime, FunctionTool, ModelResponse, TokenUsage, ToolCall
from avo.providers import FakeProvider


class AddArguments(BaseModel):
    left: int
    right: int


async def add(arguments: AddArguments) -> object:
    return {"sum": arguments.left + arguments.right}


async def main() -> None:
    # 100% offline testable fake provider
    provider = FakeProvider(
        [
            ModelResponse(
                tool_call=ToolCall(
                    tool_call_id="addition-1",
                    name="add",
                    arguments={"left": 2, "right": 3},
                ),
                usage=TokenUsage(input_tokens=12, output_tokens=5),
            ),
            ModelResponse(
                content="The sum is 5.",
                usage=TokenUsage(input_tokens=18, output_tokens=6),
            ),
        ]
    )

    runtime = AgentRuntime(
        provider=provider,
        tools=[
            FunctionTool(
                name="add",
                description="Add two integers.",
                arguments_model=AddArguments,
                function=add,
            )
        ],
    )
    result = await runtime.run("What is 2 + 3?")
    print(result.status.value, result.stop_reason.value, result.output)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Comparison Matrix

How does Avo compare to alternative model proxies and CLI tools?

| Feature | **Avo** | **9router** | **LiteLLM** | **OpenRouter** | **Claude Code** |
|---|:---:|:---:|:---:|:---:|:---:|
| **In-Process Agent Runtime** | ✅ Yes | ❌ (Proxy only) | ❌ (Gateway only) | ❌ (Hosted API) | ✅ Yes |
| **Subscription OAuth (Claude/Codex/Gemini)** | ✅ Yes | ✅ Yes | ❌ No | ❌ No | ⚠️ (Claude only) |
| **Multi-Tier Failover (Sub → Cheap → Local)** | ✅ Yes | ⚠️ (Basic route) | ✅ Yes | ⚠️ (Model fallbacks) | ❌ No |
| **Zero-Cost Local Floor (Ollama)** | ✅ Yes | ⚠️ (Via endpoint) | ✅ Yes | ❌ No | ❌ No |
| **Event-Sourced Ledger & Replay (SQLite)** | ✅ Built-in | ❌ No | ❌ No | ❌ No | ❌ No |
| **POSIX Safe Workspace (O_NOFOLLOW)** | ✅ Built-in | ❌ No | ❌ No | ❌ No | ❌ No |
| **Ephemeral Docker Sandbox** | ✅ Built-in | ❌ No | ❌ No | ❌ No | ⚠️ (Host shell) |
| **Core Dependency Footprint** | **Pydantic only** | Go binary | Heavy Python deps | N/A (Cloud) | Node.js |

---

## Supported Providers

| Provider | Identifier | Auth Mechanism | Primary Use Case |
|---|---|---|---|
| **Anthropic Claude** | `anthropic` | Subscription OAuth or API Key | High-reasoning agent turns |
| **ChatGPT Codex** | `codex` | Subscription OAuth | Complex coding & planning |
| **Google Gemini** | `gemini` / `gemini-cli`| Subscription OAuth or API Key | Fast, multimodal turns |
| **Ollama** | `ollama` | Local HTTP (no auth) | Zero-cost reliability floor |
| **OpenRouter** | `openrouter` | API Key | 300+ models gateway |
| **Groq** | `groq` | API Key | Ultra low-latency inference |
| **Cerebras** | `cerebras` | API Key | Wafer-scale speed inference |
| **MiniMax** | `minimax` | API Key | Cost-effective Anthropic style |
| **OpenAI-Compatible** | `openai` | API Key | vLLM, llama.cpp, LocalAI |
| **Multi-Tier Combo** | `combo` | Orchestrates all above | Automatic 429 & quota fallback |

---

## CLI & REPL Cheat Sheet

### Core CLI Commands

| Command | Action |
|---|---|
| `avo chat` | Start interactive chat REPL. |
| `avo login [PROVIDER]` | Authenticate via subscription OAuth or API key. |
| `avo combo list` | List configured multi-tier combo profiles (`--json` supported). |
| `avo combo show <NAME>` | Inspect tier configuration, timeouts, and cooldowns. |
| `avo combo new <NAME> --tier ...` | Create a custom combo route. |
| `avo combo rm <NAME>` | Remove a custom combo profile. |
| `avo doctor` | Smoke-test configuration without network calls. |
| `avo cost` | Aggregate token usage and USD spend across recorded runs. |
| `avo runs list` | List recorded execution runs. |
| `avo runs inspect <RUN_ID>` | Chronological trace of steps, tools, and events. |
| `avo runs diff <A> <B>` | Compare execution deltas between two runs. |

### Essential Chat Slash Commands

- `/combo [NAME]`: View live tier health status or hot-swap combo profiles.
- `/model [NAME]`: Switch active provider or model on the fly.
- `/provider`: Inspect resolved endpoint and credentials.
- `/context`: Snapshot token usage, session turns, and active persona.
- `/diff`: Display unified git diff or status in the current workspace.
- `/undo`: Revert uncommitted workspace modifications.
- `/export [PATH]`: Export session history to formatted Markdown.
- `/clear`: Clear terminal screen.
- `/quit`: Exit REPL.

---

## Acknowledgments & Upstream Lineage

Avo is built upon and inspired by excellent open-source projects:

- **[decolua/9router](https://github.com/decolua/9router)** (MIT): Upstream reference and port for OAuth PKCE authorization-code token exchange shapes, vendor endpoints, and token-refresh lifecycle patterns.
- **[clash-ru/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)** (MIT): Upstream Go lineage for subscription flow verification.
- **[BerriAI/liteLLM](https://github.com/BerriAI/litellm)** (MIT): Reference for provider fallback matrices and error categorization.
- **[Textualize/rich](https://github.com/Textualize/rich)** (MIT): Inspiration for terminal styling and visual design.
- **Anthropic, OpenAI, Google, and Ollama**: For developer APIs and local inference engines.

---

## Documentation

Full documentation, architecture specs, and user guides are available at [fqih.cloud](https://fqih.cloud/):

- [Subscription OAuth Guide](docs/guides/subscription-auth.md)
- [Combo Routing & Failover Guide](docs/guides/combo-routing.md)
- [Full Project API Reference](docs/avo-reference.md)
- [SemVer & Stability Policy](docs/semver.md)

---

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.
