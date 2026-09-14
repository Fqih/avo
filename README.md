<div align="center">

<img src="logo.svg" width="240" alt="avo logo">

**Provider-agnostic reliability runtime for bounded, observable, resumable, replayable AI agent loops.**

*Bounded. Resumable. Provider-agnostic. Honest about why it stopped.*

</div>

---

> ⚠️ 0.1 is an **alpha foundation**. Suitable for evaluation, deterministic tests, and local prototypes; **not production-ready**.

---

## Install

Requires Python 3.11+. Core runtime depends only on Pydantic.

```bash
git clone https://github.com/Fqih/avo.git
cd avo
python -m pip install -e ".[dev,providers,sandbox]"
```

### Optional extras

| Extra | Adds | When you need it |
|---|---|---|
| `[dev]` | pytest, mypy, ruff, coverage | Local dev + tests |
| `[providers]` | httpx | Talking to MiniMax, Anthropic, OpenAI-compatible endpoints |
| `[sandbox]` | docker-py | Using `run_shell` against a real Docker daemon |
| `[otel]` | opentelemetry-api, sdk, otlp exporter | Emitting `gen_ai.*` spans for a run |
| `[live-benchmark]` | httpx, matplotlib | Running `python benchmark/run_benchmark.py` |
| `[mcp]` | mcp SDK | Authoring MCP servers or non-stdio transports |

Verify the install:

```bash
avo doctor
```

Prints resolved provider / model / endpoint without an HTTP call — cheapest smoke test.

---

## Quickstart

One typed tool call, then a final reply. No API key:

```python
import asyncio
from pydantic import BaseModel

from avo import (
    AgentRuntime, FunctionTool, ModelResponse, TokenUsage, ToolCall,
)
from avo.providers import FakeProvider


class AddArguments(BaseModel):
    left: int
    right: int


async def add(arguments: AddArguments) -> object:
    return {"sum": arguments.left + arguments.right}


async def main() -> None:
    provider = FakeProvider(
        [
            ModelResponse(
                tool_call=ToolCall(
                    tool_call_id="addition-1", name="add",
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


asyncio.run(main())
```

`examples/basic_agent.py` ships this runnable end-to-end.

---

## Configuration

All knobs live in `AVO_*` env vars. The chat REPL's first-run wizard can persist them to `~/.zshrc` / `~/.bashrc`.

### Provider selection

| Variable | Required | Purpose |
|---|---|---|
| `AVO_PROVIDER` | yes | `ollama` \| `minimax` \| `anthropic` \| `openai` \| `groq` \| `cerebras` \| `openrouter` \| `gemini` |
| `AVO_MODEL` | yes | Default model name for the active provider |
| `AVO_OLLAMA_BASE_URL` | no | Ollama endpoint (default `http://localhost:11434`) |
| `AVO_OLLAMA_MODEL` | no | Ollama-specific model override |
| `AVO_OLLAMA_API_KEY` | no | Ollama auth header (rarely needed) |
| `AVO_MINIMAX_API_KEY` | yes for minimax | API key |
| `AVO_MINIMAX_BASE_URL` | no | Default `https://api.minimax.io` |
| `AVO_MINIMAX_MODEL` | no | Provider-specific override |
| `AVO_MINIMAX_API_STYLE` | no | `anthropic` (default) or `openai` |
| `AVO_ANTHROPIC_API_KEY` | yes for anthropic | API key |
| `AVO_ANTHROPIC_BASE_URL` | no | Default `https://api.anthropic.com` |
| `AVO_ANTHROPIC_MODEL` | no | Provider-specific override |
| `AVO_OPENAI_API_KEY` | yes for openai | API key |
| `AVO_OPENAI_BASE_URL` | no | Default `https://api.openai.com/v1` |
| `AVO_OPENAI_MODEL` | no | Provider-specific override |
| `AVO_GROQ_API_KEY` | yes for groq | Groq API key |
| `AVO_GROQ_BASE_URL` | no | Default `https://api.groq.com/openai/v1` |
| `AVO_GROQ_MODEL` | no | Provider-specific override |
| `AVO_CEREBRAS_API_KEY` | yes for cerebras | Cerebras API key |
| `AVO_CEREBRAS_BASE_URL` | no | Default `https://api.cerebras.ai/v1` |
| `AVO_CEREBRAS_MODEL` | no | Provider-specific override |
| `AVO_OPENROUTER_API_KEY` | yes for openrouter | OpenRouter API key |
| `AVO_OPENROUTER_BASE_URL` | no | Default `https://openrouter.ai/api/v1` |
| `AVO_OPENROUTER_MODEL` | no | Provider-specific override |
| `AVO_GEMINI_API_KEY` | yes for gemini | Gemini API key |
| `AVO_GEMINI_BASE_URL` | no | Default `https://generativelanguage.googleapis.com` |
| `AVO_GEMINI_MODEL` | no | Provider-specific override |

### Runtime + policy

| Variable | Default | Purpose |
|---|---|---|
| `AVO_DATABASE_PATH` | in-memory | SQLite path for the run/event store |
| `AVO_MAX_TOTAL_TOKENS` | unlimited | Override `LoopPolicy.max_total_tokens` |
| `AVO_MAX_RUNTIME_SECONDS` | `300` | Override `LoopPolicy.max_runtime_seconds` |
| `AVO_REPEATED_ACTION_LIMIT` | `3` | Override `LoopPolicy.repeated_action_limit` |
| `AVO_PERMISSION_MODE` | `default` | `default` / `accept_edits` / `plan` / `bypass` |
| `AVO_TOOLS_REQUIRE_APPROVAL` | empty | Comma-separated tool names gating on `approval_callback` |
| `AVO_USAGE_RATES_INPUT_PER_1K` | unset | Cost rate for input tokens |
| `AVO_USAGE_RATES_OUTPUT_PER_1K` | unset | Cost rate for output tokens |
| `AVO_NOTIFY_WEBHOOK` | unset | URL to POST run lifecycle events to |
| `AVO_NOTIFY_DESKTOP` | `0` | Set to `1` to enable desktop notifications |

See [`.env.example`](.env.example) for a copy-paste template.

---

## Providers

| Provider | Adapter | Notes |
|---|---|---|
| Ollama | `OllamaProvider` | Local HTTP, no key. Default for offline dev. |
| MiniMax | `MiniMaxProvider` | Anthropic-compatible (default) or OpenAI-compatible style. |
| Anthropic | `AnthropicProvider` | Native Anthropic Messages API. |
| OpenAI | `OpenAICompatibleProvider` | Any `/v1/chat/completions` endpoint — OpenAI, vLLM, llama.cpp. |
| Groq | `GroqProvider` | OpenAI-compatible Llama / Mixtral inference, low latency. |
| Cerebras | `CerebrasProvider` | OpenAI-compatible inference on Cerebras wafer-scale hardware. |
| OpenRouter | `OpenRouterProvider` | OpenAI-compatible gateway to 300+ models, free tier included. |
| Google Gemini | `GeminiProvider` | Native Gemini `generateContent` REST API with function calling. |

All eight implement the same `ModelProvider` Protocol. Swapping providers is one line.

---

## Application tools

`avo.app_tools` is the optional-but-default toolkit. Tools plug into the existing
`FunctionTool` / `ToolRegistry` contract — no changes to the runtime, state machine, or
event log.

| Tool | What it does |
|---|---|
| `read_file` / `write_file` / `edit_file` | Workspace-scoped file I/O |
| `glob` / `grep` / `workspace_map` | Workspace enumeration + search |
| `git_status` | Branch, modified, optional untracked files |
| `run_shell` | One shell command in an ephemeral Docker container |
| `plan_tasks` / `submit_plan` | Structured plan declaration + persistence |
| `task` | Dispatch isolated sub-agent run |
| `web_fetch` / `web_search` | HTTP GET with hard byte cap / DuckDuckGo HTML search |

### Workspace safety

`Workspace(root).validate_path(...)` rejects `../`, symlink escapes, absolute-path
escapes, and null bytes **before** any I/O. `validate_for_write` refuses to follow
symlinks at the leaf or any parent. `write_file` / `edit_file` open with `O_NOFOLLOW` on
POSIX. No path the model can ask for exits the workspace root.

### Shell sandbox

`SandboxExecutor` wraps docker-py. Each `run_shell` call creates a fresh container
(`remove=True`), runs with `network_mode="none"` by default, applies a `mem_limit` and
`cpu_quota`, times out via the runtime's `LoopPolicy.tool_timeout_seconds`, and removes
the container before returning. `run_shell` never calls `subprocess` on the host — the
sandbox is the only path to the shell.

Suit the network policy to your task:

```python
from avo.app_tools.sandbox import SandboxExecutor

sandbox = SandboxExecutor(
    network_mode="bridge",  # default "none" — switch when network is required
    mem_limit="512m",
    cpu_quota=100000,
)
```

### Approval policy

`AVO_TOOLS_REQUIRE_APPROVAL` lists tool names that must wait for explicit operator
approval. Tools not in the list auto-approve. Wire a custom callback:

```python
from avo.app_tools.approval import build_approval_callback

callback = build_approval_callback(
    on_require=lambda call: input(f"approve {call.name}? [y/N] ").lower() == "y",
)
runtime = AgentRuntime(provider=provider, tools=[...], approval_callback=callback)
```

---

## Observability (OpenTelemetry)

Set `AVO_OTEL_ENABLED=1` and the runtime wraps every `_drive` invocation in a span
tagged with the `gen_ai.*` semantic conventions:

```bash
python -m pip install -e ".[otel]"
export AVO_OTEL_ENABLED=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
avo chat
```

Spans carry `avo.run_id`, `gen_ai.system` (provider name), and `gen_ai.request.model`.
`avo.observability.record_usage(...)` writes input / output token counts into the
active span, so cost rollups line up with trace data. The module is a noop when the
extra is not installed — no exceptions at import time, no runtime overhead.

---

## Cost tracking

`avo cost` aggregates every persisted ledger entry (the same `avo.db` the event store
uses) and prints total tokens + USD spend, with per-run and per-model breakdowns.
Output is human-readable by default and machine-readable with `--json`:

```bash
avo cost --database avo.db --json
```

```json
{
  "run_count": 2,
  "total": {"input_tokens": 700, "output_tokens": 370, "total_tokens": 1070},
  "cost_usd": "0.0142",
  "runs": [...],
  "models": [...]
}
```

Set `AVO_USAGE_RATES_INPUT_PER_1K` / `AVO_USAGE_RATES_OUTPUT_PER_1K` to seed the ledger
with USD costs as each provider call returns.

---

## Scaffolding plugins

`avo plugin init` writes a working plugin to disk so you can iterate on a new tool
without touching project structure by hand:

```bash
mkdir ~/projects/my-tool && cd ~/projects/my-tool
avo plugin init my-tool
cd my-tool
avo plugin install .    # registers the sample echo tool
```

The scaffold ships a `pyproject.toml` declaring an `avo.tools` entry point, a
`register()` stub returning a sample `FunctionTool`, a `README.md`, and a `.gitignore`.
Replace the sample tool with your own and the runtime picks it up on the next
`avo plugin install .`.

---

## CLI

```bash
avo [-d DATABASE] <command> [args]
```

| Command | What it does |
|---|---|
| `avo doctor` | Verify `AVO_*` config without an HTTP call. |
| `avo chat [-d PATH] [--workspace-root DIR] [--session ID] [--new-session]` | Interactive REPL. First run with no provider triggers the setup wizard. |
| `avo runs list` | Print one line per run. |
| `avo runs inspect RUN_ID` | Render the chronological trace. |
| `avo runs resume RUN_ID` | Resume a persisted FakeProvider run with no pending tool call. |
| `avo plugin install URL \| PATH` | Install a plugin from git URL or local path. |
| `avo plugin list` / `show NAME` / `remove NAME [-y]` | Manage installed plugins. |
| `avo plugin init [NAME] [-d DIR] [--force]` | Scaffold a new plugin (pyproject + sample `FunctionTool`). |
| `avo mcp add NAME [--env KEY=VAL]... CMD ARGS...` | Register an MCP server. |
| `avo mcp list` / `remove NAME [-y]` | Manage MCP server registrations. |
| `avo skill install PATH` / `list` / `show NAME` / `remove NAME [-y]` | Manage skill packs. |
| `avo bench [--turns N] [--task ID] [--output PATH]` | Deterministic FakeProvider benchmark. |
| `avo runs diff RUN_A RUN_B [--json]` | Compare two persisted runs. |
| `avo cost [--database PATH] [--json]` | Aggregate token + USD spend across runs. |
| `avo sandbox run --image IMG --workspace DIR [--network MODE] -- COMMAND ARGS...` | One-shot ephemeral docker sandbox. |

### Chat REPL slash commands

| Slash command | Action |
|---|---|
| `/help` | Print the full slash-command list. |
| `/provider` | Print provider / model / base URL / key-presence. |
| `/model [NAME]` | Switch to `NAME` or pick from the catalog (`/model` alone). |
| `/inspect RUN_ID` | Render a stored trace. |
| `/resume RUN_ID` | Resume a stored run. |
| `/skills` | List skills under `<workspace>/.avo/skills`. |
| `/skill NAME` | Inject a skill body as the next user turn. |
| `/quit` / `/exit` | Exit the REPL. |

---

## Examples

`examples/` ships runnable Python files, all offline (no API key):

| File | Demonstrates |
|---|---|
| `examples/basic_agent.py` | One typed tool call, then a final reply. |
| `examples/repeated_action.py` | Deterministic repeated-action containment. |
| `examples/resume_after_interrupt.py` | Interrupt mid-flight, reopen SQLite, resume. |
| `examples/app_tools_demo.py` | Workspace + file tools + permissive approval. |
| `examples/live_providers/` | Real-API smoke tests per provider (need `AVO_*` keys). |

```bash
python examples/basic_agent.py
python examples/repeated_action.py
python examples/resume_after_interrupt.py
python examples/app_tools_demo.py
```

---

## Development

```bash
python -m pip install -e ".[dev,providers,sandbox]"
ruff check .
ruff format --check .
mypy src/avo
pytest
```

Quality gates:

- **ruff** lint + format — line-length 100, per-file ignores for `examples/`, `benchmark/`.
- **mypy** strict on `src/avo` (Pydantic plugin).
- **pytest** `--strict-config --strict-markers`, asyncio mode auto.
- **coverage** branch coverage, fail-under 90%.

The suite needs no Docker — `SandboxExecutor` accepts an injectable client so tests
inject a fake and assert the container config that would be sent. Live Docker integration
is opt-in, same pattern as `benchmark/live/tests/`.

---

## License

MIT — see [`LICENSE`](LICENSE).
