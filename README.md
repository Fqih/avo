<div align="center">

<img src="logo.png" width="180" height="180" alt="Avo logo">

# Avo

**Reliable agent infrastructure for observable, resumable, provider-agnostic runs.**

*One conversation, many models: bounded tools, durable state, and automatic failover down to local Ollama.*

[![Python 3.11 | 3.12 | 3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Fqih/avo/actions/workflows/ci.yml/badge.svg)](https://github.com/Fqih/avo/actions/workflows/ci.yml)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](https://avo.faqihhakim.tech/)
[![Docs](https://img.shields.io/badge/docs-avo.faqihhakim.tech-indigo.svg)](https://avo.faqihhakim.tech/)

</div>

---

**Avo** is an observable, resilient AI agent runtime that can use API providers,
vendor-account OAuth, or local models behind one execution loop. When a quota,
rate limit, or provider outage interrupts a turn, Avo can switch tiers without
discarding the conversation or execution state. Runs are recorded in SQLite so
you can inspect, resume, and explain what happened.

The core package stays dependency-light (**Pydantic only**). Provider clients,
Docker sandboxing, MCP, OpenTelemetry, and the LangChain bridge are optional
extras, so you can install only the capabilities your deployment needs.

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
│ (Account / Quota)  ││ (Pay-per-token)    ││ (Zero Cost / Free) │
└────────────────────┘└────────────────────┘└────────────────────┘
         │ (429/Quota)         │ (429/Quota)
         └──────► Fallback ────┴──────► Fallback ────► Done
```

### 1. 🔑 Vendor Account Login & Universal Login
Open the official **Claude**, **ChatGPT/Codex**, or **Google Gemini** login in your browser via standard PKCE flows (`avo login claude`, `avo login codex`, `avo login gemini`). Free and paid accounts can have different model access and quotas; Avo never claims that a plan is required or bypasses vendor limits. `AVO_ALLOW_SUBSCRIPTION=1` is an explicit opt-in for OAuth-backed inference. Tokens are stored in the permission-protected fallback file `~/.config/avo/auth.json`, or in the optional OS keyring when `AVO_CREDENTIAL_BACKEND=keyring`/`auto` is selected.

Install `avo[keyring]` to enable the keyring backend. `avo doctor` reports the
active backend without printing secrets.

### 2. 🔀 Multi-Tier Combo Routing & Quota Failover
Never suffer crashed agent runs from HTTP 429 or exhausted token quotas again. Configure named combo profiles (`default`, `coder`, `budget`) where models are organized in priority order (`account` &rarr; `cheap API` &rarr; `free local floor`). Avo detects rate limits and credit exhaustion mid-turn, switches to the next tier, and continues streaming.

### 3. 📜 Event-Sourced Ledger & Replay
Every decision, prompt, tool call, output, and failover is recorded as an immutable event in a durable SQLite ledger. Any past run can be inspected with chronological traces (`avo runs inspect <id>`) or resumed deterministically (`avo runs resume <id>`).

### 4. 🛡️ Hardened Workspace & Ephemeral Sandboxing
File tools strictly enforce POSIX `O_NOFOLLOW` boundaries—null bytes, symlink escapes, and `../` traversal are blocked before any I/O occurs. Shell commands run in isolated, ephemeral Docker containers with CPU/memory limits and disabled networking by default.

### 5. 🪶 Zero Extra Core Dependencies
The core agent loop, state machine, event store, and providers require **only Pydantic**. Additional capabilities (Docker sandbox, OpenTelemetry, CLI extras) remain opt-in.

### 6. 🧠 Deterministic Token Savers
Long tool-heavy conversations can opt into internal `compact`, `full`, or
terse-output presets with `avo saver use NAME`. Avo preserves the original
event history and reports estimated savings; no external RTK/Caveman binary is
required.

### 7. 🔒 Explicit Milestone Three Security Boundaries

`avo doctor` now shows the resolved permission, sandbox, plugin, and local web
dashboard posture together with each setting's source, without printing
credentials. Settings resolve from CLI, environment, workspace config, user
config, then safe defaults. Sandboxed execution and disabled networking are the
defaults; child agents cannot escalate beyond the parent's capabilities.

Plugin installation requires an explicit confirmation, and the local dashboard
uses bearer/session authentication, origin checks, CSRF protection, and
confirmation before mutations. OAuth/API-key authentication, permission
protection, and encryption remain separate concerns. See the
[Milestone Three migration note](docs/migrations/0.7.x-to-milestone-three.md)
before changing security posture in an unattended deployment.

---

## Terminal Visual Walkthrough

Experience interactive agent loops with real-time model failover:

```text
$ export AVO_PROVIDER=combo
$ export AVO_COMBO=coder
$ avo

       ▄██▄           Avo CLI 0.7.2
     ▄██████▄         Fqih (account quota)
    ███    ███        provider: combo · model: coder [account -> cheap -> free]
   ███  ▄▄  ███       workspace: ~/Project/Loopward
   ███  ▀▀  ███       session: c8f921ab04e1
  ──────────────────────────────────────────────────────

> Analyze the authentication flow in src/avo/auth.py and write unit tests

⠋ Thinking...
⤾ Fallback: switched from 'account' (claude) to 'cheap' (openrouter) [rate_limited_429]

I've analyzed `src/avo/auth.py`. Here is the architecture breakdown and test suite...
```

---

## Quickstart (Under 2 Minutes)

### 1. Installation

Requires Python 3.11+.

#### User-global CLI installer

For a user-global command, install Avo with the OS-native installer. It uses
`uv tool` and does not require `sudo` or Administrator access.

Linux, macOS, or Git Bash on Windows:

```bash
curl -fsSL https://avo.faqihhakim.tech/install.sh | bash
```

Native Windows PowerShell:

```powershell
irm https://avo.faqihhakim.tech/install.ps1 | iex
```

The installer installs the `avo[all]` runtime bundle, ensures Python 3.13 is
available through `uv`, and updates the user PATH for `avo`. Review the plan
without making changes first:

```bash
bash install.sh --dry-run
# PowerShell: .\install.ps1 -DryRun
```

For a smaller installation, override the package or Python version:

```bash
AVO_PACKAGE='avo[providers]' AVO_PYTHON_VERSION=3.12 bash install.sh
```

The global installer only installs the CLI. Run `avo setup` once to create the
global `~/.avo` configuration, then use `avo doctor` to inspect what Avo will
resolve. Workspace state and run history remain local to the project.

For vendor-account OAuth providers, opt in explicitly so the choice is visible
and reproducible:

```bash
avo setup --allow-subscription
avo doctor
```

#### Development checkout

```bash
git clone https://github.com/Fqih/avo.git
cd avo
python -m pip install -e ".[dev,providers,sandbox]"
```

### 2. Authenticate and configure

Log in through an official vendor browser flow or plain API keys:

```bash
# Official browser login (Claude, ChatGPT Codex, or Gemini)
avo login claude
avo login codex
avo login gemini

# Or store plain API keys in permission-restricted auth.json
avo login openrouter --key-stdin

# Authenticate every missing cloud vendor used by a combo, one at a time
avo combo auth coder

# Inspect local hardware before choosing a model to download
avo models ollama recommend
avo models ollama pull qwen2.5-coder:7b
```

Verify the resolved provider, model, endpoint, and credential requirements with
one command. `doctor` does not make a provider request:

```bash
avo doctor
```

The first successful setup/login becomes Avo's remembered route in
`~/.avo/config.json`, so launching `avo` again reuses that provider and model.
The setup wizard offers a numbered model picker; available access still
depends on the account's plan and quota. Use `/model` in the REPL to inspect
the catalog or override the model for the next turn.

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

### Named agents and deterministic replay

Workspace agents live in `.avo/agents/<name>.md`. The built-ins `@coder`,
`@explore`, and `@reviewer` are available immediately. Mention one at the
start of a prompt to delegate an isolated task; use a top-level `|` to run
independent tasks in parallel:

```text
@explore map the authentication flow
@reviewer inspect the provider error handling | @explore find related tests
```

Use `/agents` for the searchable picker, `/agents list` for a script-friendly
list, and `/agent add NAME DESCRIPTION` to create a workspace profile. Child
agents inherit the parent permission boundary and are bounded by the same
workspace root.

Every terminal run stores model decisions and durable tool results in the
SQLite event ledger. Verify that material without calling a provider or
executing a tool again:

```bash
avo runs replay RUN_ID
avo runs replay RUN_ID --json
```

The interactive equivalent is `/replay RUN_ID`.

### Attach files and images

Paste or drag a workspace path into the prompt, or make the attachment
explicit with `@path` / `file://path`. Text and source files become labeled
text blocks; PNG, JPEG, GIF, and WebP images are sent as multimodal blocks
when the selected provider supports them. Use `@clipboard` to attach an
image from the Wayland/X11 clipboard:

```text
review @src/avo/runtime.py and @tests/test_runtime.py
describe @clipboard
```

Avo rejects symlink escapes, unsupported binary files, and oversized files
before inference. Defaults are 2 MiB per text file, 10 MiB per image, and
20 MiB total per turn.

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
| **Vendor-account OAuth (Claude/Codex/Gemini)** | ✅ Yes | ✅ Yes | ❌ No | ❌ No | ⚠️ (Claude only) |
| **Multi-Tier Failover (Account → Cheap → Local)** | ✅ Yes | ⚠️ (Basic route) | ✅ Yes | ⚠️ (Model fallbacks) | ❌ No |
| **Zero-Cost Local Floor (Ollama)** | ✅ Yes | ⚠️ (Via endpoint) | ✅ Yes | ❌ No | ❌ No |
| **Event-Sourced Ledger & Replay (SQLite)** | ✅ Built-in | ❌ No | ❌ No | ❌ No | ❌ No |
| **POSIX Safe Workspace (O_NOFOLLOW)** | ✅ Built-in | ❌ No | ❌ No | ❌ No | ❌ No |
| **Ephemeral Docker Sandbox** | ✅ Built-in | ❌ No | ❌ No | ❌ No | ⚠️ (Host shell) |
| **Core Dependency Footprint** | **Pydantic only** | Go binary | Heavy Python deps | N/A (Cloud) | Node.js |

---

## Supported Providers

| Provider | Identifier | Auth Mechanism | Primary Use Case |
|---|---|---|---|
| **Anthropic Claude** | `anthropic` | Vendor account OAuth or API key | High-reasoning agent turns |
| **ChatGPT Codex** | `codex` | Vendor account OAuth; free/paid quota varies | Complex coding & planning |
| **Google Gemini** | `gemini` / `gemini-cli`| Vendor account OAuth or API key | Fast, multimodal turns |
| **Ollama Local** | `ollama` | Local HTTP (no auth) | Zero-cost reliability floor |
| **Ollama Cloud** | `ollama-cloud` | Official API/device key | Remote large models |
| **OpenRouter** | `openrouter` | API Key | 300+ models gateway |
| **Groq** | `groq` | API Key | Ultra low-latency inference |
| **Cerebras** | `cerebras` | API Key | Wafer-scale speed inference |
| **MiniMax** | `minimax` | API Key | Cost-effective Anthropic style |
| **OpenAI-Compatible** | `openai` | API Key | vLLM, llama.cpp, LocalAI |
| **Multi-Tier Combo** | `combo` | Orchestrates all above | Automatic 429 & quota fallback |

### Live model catalogs and Antigravity

For `gemini-cli`, Avo does not need to maintain a second model list. In an
interactive terminal, `/model` fetches the current account catalog from
Antigravity's `agy models` command. If you use [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI),
point Avo at its OpenAI-compatible server instead; Avo fetches `GET /v1/models`
and sends turns to `POST /v1/chat/completions`:

```bash
export AVO_PROVIDER=gemini-cli
export AVO_GEMINI_CLI_TRANSPORT=cliproxyapi
export AVO_CLIPROXYAPI_BASE_URL=http://127.0.0.1:8317
export AVO_CLIPROXYAPI_API_KEY=your-local-proxy-key   # optional for an open localhost proxy
avo
```

When neither a proxy nor `agy` is available, Avo reports the missing
transport instead of silently using a stale model catalog. A live catalog is
cached briefly for offline continuity and the interactive picker labels
`live`, `cache`, `stale`, or `static` fallback. The coding agent
also exposes a `run_terminal` tool for commands, tests, and linters in the
active workspace; require explicit approval with
`AVO_TOOLS_REQUIRE_APPROVAL=run_terminal` when needed.

---

## CLI & REPL Cheat Sheet

### Core CLI Commands

| Command | Action |
|---|---|
| `avo` / `avo chat` | Start the interactive chat REPL. |
| `avo resume [SESSION]` | Resume the latest or a specific chat session. |
| `avo login [PROVIDER]` | Open the official vendor login or store an API key. |
| `avo combo auth <NAME>` | Login to every missing cloud vendor used by a combo, sequentially. |
| `avo models ollama list` | List installed Ollama Local models. |
| `avo models ollama recommend` | Recommend local models from this computer's hardware. |
| `avo models ollama pull MODEL` | Show size and ask for confirmation before downloading locally. |
| `avo models ollama cloud` | Show remote Ollama Cloud guidance; no local download. |
| `avo models ollama cloud list|health|usage` | Inspect Cloud models, health, or optional quota metadata. |
| `avo saver list` | List built-in token-saver presets. |
| `avo saver show NAME` | Inspect a preset's style guide and compression pipeline. |
| `avo saver use NAME` / `avo saver off` | Enable or disable the persisted saver choice. |
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

Full documentation, architecture specs, and user guides are available at [avo.faqihhakim.tech](https://avo.faqihhakim.tech/):

- [Quickstart](docs/guides/quickstart.md)
- [Installation & global CLI](docs/guides/install.md)
- [Provider login and quota guide](docs/guides/subscription-auth.md)
- [Combo Routing & Failover Guide](docs/guides/combo-routing.md)
- [CLI reference](docs/cli.md)
- [Full Project API Reference](docs/avo-reference.md)
- [SemVer & Stability Policy](docs/semver.md)

## Project status and boundaries

Avo is currently **alpha software**. The durable runtime, provider adapters,
CLI, event ledger, and offline test suite are usable for local prototypes and
evaluation. Treat subscription OAuth, sandbox execution, and provider failover
as evolving interfaces until the project reaches a stable release.

Before deploying Avo in an unattended environment, review the permission mode,
workspace boundaries, credential storage, and provider fallback policy for your
own threat model.

## Contributing

```bash
python -m pip install -e ".[dev,docs,providers]"
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Bug reports and focused pull requests are welcome. Start with the smallest
reproducible test, especially for provider behavior, replay, or workspace
security changes.

---

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.
