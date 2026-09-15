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

```bash
git clone https://github.com/Fqih/avo.git
cd avo
python -m pip install -e ".[dev,providers,sandbox]"
```

### Optional extras

| Extra              | Adds                                          | When you need it                                |
| ------------------ | --------------------------------------------- | ----------------------------------------------- |
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

- **Deterministic agent loop** — strict `StopReason` taxonomy, finite
  `AgentState` transitions, replayable event log.
- **Provider-agnostic** — Anthropic, OpenAI-compatible, Groq, Cerebras,
  Ollama, MiniMax; bring your own.
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

## Links

- [GitHub repository](https://github.com/Fqih/avo)
- [PyPI package](https://pypi.org/project/avo/)
- [Changelog](changelog.md)
- [API stability policy](api-stability.md)
- [SemVer policy](semver.md)
