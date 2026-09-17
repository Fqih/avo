<div align="center">

<img src="assets/logo.svg" width="200" alt="Avo logo">

</div>

# Avo

**Provider-agnostic reliability runtime for bounded, observable, resumable, replayable AI agent loops.**

*Bounded. Resumable. Provider-agnostic. Honest about why it stopped.*

---

!!! warning "Alpha status"

    0.1 is an **alpha foundation**. Suitable for evaluation, deterministic
    tests, and local prototypes; **not production-ready**.

## Install

Requires Python 3.11+. Core runtime depends only on Pydantic.

### User-global CLI

The recommended end-user installation uses `uv tool` and stays inside the
current user's tool environment; it does not require `sudo` or Administrator
access.

Linux, macOS, and Git Bash:

```bash
curl -fsSL https://raw.githubusercontent.com/Fqih/avo/main/install.sh | bash
```

Native Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/Fqih/avo/main/install.ps1 | iex
```

Both installers install `avo[all]`, provision Python 3.13 with `uv`, and
register the user-global `avo` command. Use a dry run before installation:

```bash
bash install.sh --dry-run
# PowerShell: .\install.ps1 -DryRun
```

After the CLI is installed, configure a workspace with:

```bash
avo setup
avo doctor
```

The installer is also configurable with `AVO_PACKAGE` and
`AVO_PYTHON_VERSION`, or with `--package` and `--python` on `install.sh`.

### Development checkout

```bash
git clone https://github.com/Fqih/avo.git
cd avo
python -m pip install -e ".[dev,providers,sandbox]"
```

### Optional extras

| Extra              | Adds                                          | When you need it                                |
| ------------------ | --------------------------------------------- | ----------------------------------------------- |
| `[all]`            | Runtime integrations below                    | User-global CLI installation                    |
| `[dev]`            | pytest, mypy, ruff, coverage                  | Local dev + tests                               |
| `[providers]`      | httpx                                         | Talking to MiniMax, Anthropic, OpenAI endpoints |
| `[sandbox]`        | docker-py                                     | Using `run_shell` against a real Docker daemon  |
| `[otel]`           | opentelemetry-api, sdk, otlp exporter         | Emitting `gen_ai.*` spans for a run             |
| `[langchain]`      | langchain-core                                | Wrapping avo providers in LangChain pipelines   |
| `[mcp]`            | mcp SDK                                       | Authoring MCP servers or non-stdio transports   |
| `[live-benchmark]` | httpx, matplotlib                             | Running `python benchmark/run_benchmark.py`     |

Verify the install:

```bash
avo doctor
```

Prints resolved provider / model / endpoint without an HTTP call — cheapest smoke test.

## What Avo gives you

- **Subscription OAuth & Universal Login** — authenticate directly via
  browser PKCE against Claude Pro/Team, ChatGPT Plus/Team (Codex), or
  Google Gemini CLI (`avo login`).
- **Multi-Tier Combo Routing & Quota Failover** — organize models into
  prioritized tiers (`subscription` &rarr; `cheap` &rarr; `free local floor`).
  Fail over automatically on HTTP 429 or quota exhaustion mid-turn.
- **Deterministic agent loop** — strict `StopReason` taxonomy, finite
  `AgentState` transitions, replayable event log.
- **Provider-agnostic** — Anthropic, OpenAI-compatible, Groq, Cerebras,
  Ollama, MiniMax, Gemini, Codex; bring your own.
- **Resilience** — bounded retry with exponential backoff, three-state
  circuit breaker, request-level circuit trip on sustained upstream
  failure.
- **Sandbox** — Docker-as-a-service shell tool with ephemeral containers,
  default `network_mode="none"`, workspace-bounded file tools.
- **Approval gates** — per-tool approval callbacks driven by
  `AVO_TOOLS_REQUIRE_APPROVAL`.
- **Resumability** — append-only SQLite event log with checkpoint
  snapshots. Crash mid-turn, resume from the last durable state.
- **Observability** — OpenTelemetry `gen_ai.*` spans per turn, structured
  JSON logs, cost tracking with USD estimator, audit log with deep
  secret redaction.
- **Plugins** — entry-point groups for tools, providers, and notifiers.
  Scaffold one with `avo plugin init`.
- **Optional integrations** — LangChain bridge, OpenTelemetry, MCP.

## Quickstart

```python
import asyncio
from avo import AgentRuntime, LoopPolicy, ModelRequest, FakeProvider

async def main() -> None:
    provider = FakeProvider()
    policy = LoopPolicy()
    runtime = AgentRuntime(provider=provider, policy=policy)

    request = ModelRequest(
        run_id="hello-1",
        step=1,
        messages=[{"role": "user", "content": "Say hello"}],
        tools=[],
    )
    response = await runtime.run(request)
    print(response.content)

asyncio.run(main())
```

## Repository layout

```
src/avo/        # runtime, providers, tools, integrations
tests/          # offline-by-default suite, 880+ tests
benchmark/      # cross-provider benchmark harness
docs/           # this documentation site
```

## Acknowledgments

Avo is inspired by and builds upon the work of several exceptional open-source projects:

- **[decolua/9router](https://github.com/decolua/9router)** (MIT): Upstream reference and port for OAuth PKCE token exchange flow structures, vendor endpoint configurations, and token-refresh lifecycle design.
- **[clash-ru/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)** (MIT): Upstream Go lineage for subscription flow verification.
- **[BerriAI/liteLLM](https://github.com/BerriAI/litellm)** (MIT): Reference for provider fallback matrices and error categorization.
- **[Textualize/rich](https://github.com/Textualize/rich)** (MIT): Inspiration for CLI formatting and terminal rendering.
- **Anthropic, OpenAI, Google, and Ollama**: For AI models, developer APIs, and local inference engines.

## Links

- [GitHub repository](https://github.com/Fqih/avo)
- [PyPI package](https://pypi.org/project/avo/)
- [Changelog](changelog.md)
- [API stability policy](api-stability.md)
- [SemVer policy](semver.md)
