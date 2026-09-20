<div class="avo-hero" markdown>

<div>

<div class="avo-hero__brand">
<img src="assets/logo.png" alt="Avo logo">
<span>Reliable agent infrastructure</span>
</div>

<h1>Avo</h1>

<p class="avo-hero__lead">Reliable runs for agents that need to keep going—observable, resumable, and safe to extend.</p>

<div class="avo-hero__actions">
<a class="md-button md-button--primary" href="guides/install/">Install Avo</a>
<a class="md-button" href="guides/quickstart/">See the quickstart</a>
</div>

</div>

<div class="avo-hero__terminal">
<header>Start here</header>

```bash
curl -fsSL \
  https://avo.faqihhakim.tech/install.sh | bash
avo setup
avo doctor
```

</div>

</div>

!!! warning "Alpha / Pre-Beta status"

    v0.7.3+ is an **alpha foundation / pre-beta candidate**. Suitable for evaluation, deterministic
    tests, and local prototypes; **not production-ready**.

## Start here

<div class="avo-card-grid" markdown>

<div class="avo-card" markdown>

<div class="avo-card__eyebrow">01 · Install</div>

### Get the CLI

Install Avo for your user account on Linux, macOS, Git Bash, or Windows. No root access is required.

[Installation guide →](guides/install.md)

</div>

<div class="avo-card" markdown>

<div class="avo-card__eyebrow">02 · Configure</div>

### Connect your providers

Run `avo` for the guided setup, open an official vendor login or add an API key,
then use `avo doctor` to see what Avo resolved.

[Provider login and quotas →](guides/subscription-auth.md)

</div>

<div class="avo-card" markdown>

<div class="avo-card__eyebrow">03 · Build</div>

### Run a reliable loop

Start with the CLI, or embed `AgentRuntime` in Python when you need full control over tools and state.

[Quickstart →](guides/quickstart.md)

</div>

</div>

<div class="avo-callout" markdown>

**The short version:** Avo records the important decisions of an agent run, bounds retries and tools, and gives you a durable place to resume when a model or process stops.

</div>

## What Avo solves

<div class="avo-card-grid" markdown>

<div class="avo-card" markdown>

### Provider failover

Route from an account or free quota to paid API to local Ollama when a quota, rate limit, or provider outage interrupts a turn.

</div>

<div class="avo-card" markdown>

### Durable state

Persist events and checkpoints in SQLite so a crashed run can be inspected or resumed instead of started from zero.

</div>

<div class="avo-card" markdown>

### Bounded tools

Keep file access inside the workspace, gate risky actions for approval, and run shell work in an optional isolated sandbox.

</div>

<div class="avo-card" markdown>

### Honest stopping

Every run has an explicit stop reason. You can tell whether it finished, hit a budget, failed upstream, or needs input.

</div>

<div class="avo-card" markdown>

### Observable execution

Inspect traces, token usage, cost estimates, structured logs, and OpenTelemetry spans without guessing what the agent did.

</div>

<div class="avo-card" markdown>

### Extensible by design

Add providers, tools, skills, plugins, MCP servers, or a LangChain bridge without changing the core loop.

</div>

</div>

## How a run behaves

<div class="avo-steps">

<div class="avo-step" markdown>

### 1. Receive a bounded request

Avo starts with a model request, policy, workspace, and tool registry—not an unbounded autonomous process.

</div>

<div class="avo-step" markdown>

### 2. Decide and record

The runtime asks the provider, records the response and tool decision, then validates the next state transition.

</div>

<div class="avo-step" markdown>

### 3. Execute with policy

Tools stay inside their configured boundaries. Approval callbacks and permission modes decide which actions may run.

</div>

<div class="avo-step" markdown>

### 4. Continue, fail over, or stop

Retries are bounded. A quota failure can move to the next provider tier; a hard stop leaves a trace and checkpoint.

</div>

</div>

## Install options

Requires Python 3.11+. The core runtime depends only on Pydantic.

For end users, use the [OS-native installer](guides/install.md). For contributors:

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

## A tiny Python example

```python
import asyncio
from avo import AgentRuntime, LoopPolicy, FakeProvider

async def main() -> None:
    provider = FakeProvider()
    policy = LoopPolicy()
    runtime = AgentRuntime(provider=provider, policy=policy)

    result = await runtime.run("Say hello")
    print(result.output)

asyncio.run(main())
```

## Find your next page

| If you want to... | Read |
| --- | --- |
| Install and configure Avo | [Installation](guides/install.md) |
| Understand the runtime model | [Architecture](avo-reference.md#1-overview-and-architecture) |
| Use the CLI and REPL | [CLI reference](cli.md) |
| Add provider credentials | [Provider login and quotas](guides/subscription-auth.md) |
| Resume a crashed run | [AgentRuntime](avo-reference.md#8-agentruntime-srcavoruntimepy) |
| Add tools or plugins | [Extensibility reference](avo-reference.md#18-extensibility-hooks-permissions-skills-plugins-subagents) |
| Inspect architecture and contracts | [Project reference](avo-reference.md) |

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
