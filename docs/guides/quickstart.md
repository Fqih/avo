# Quickstart

This guide takes you from an installed CLI to your first Avo run. If you have
not installed Avo yet, start with [Installation](install.md).

## 1. Check the CLI

```bash
avo --version
avo doctor
```

`doctor` prints the provider, model, endpoint, and enabled integrations it
resolved. It does not make a provider request.

## 2. Configure a provider

Run the setup wizard in the project where you want to work:

```bash
cd path/to/your/project
avo setup
```

For a quick local experiment, use an API key provider. For subscription OAuth,
see [Subscription OAuth](subscription-auth.md). Keep credentials outside Git
and verify the result:

```bash
avo doctor
```

## 3. Start the REPL

```bash
avo chat
```

Useful commands inside the REPL:

| Command | What it does |
| --- | --- |
| `/help` | Show available chat commands |
| `/model` | Inspect or change the active model |
| `/combo` | Inspect routing tiers and provider health |
| `/diff` | Review uncommitted workspace changes |
| `/exit` | Close the session |

## 4. Understand what is persisted

Avo records the run in SQLite so you can inspect what happened later:

```bash
avo runs list
avo runs inspect RUN_ID
avo runs resume RUN_ID
```

The exact stop reason matters. A run can finish normally, stop at a budget or
step limit, pause for approval, or fail because an upstream provider was not
available.

## Python API in one file

The runtime is also usable without the CLI. `FakeProvider` keeps this example
offline and deterministic:

```python
import asyncio

from avo import AgentRuntime, FakeProvider, LoopPolicy, ModelRequest


async def main() -> None:
    runtime = AgentRuntime(provider=FakeProvider(), policy=LoopPolicy())
    request = ModelRequest(
        run_id="hello-1",
        step=1,
        messages=[{"role": "user", "content": "Say hello"}],
        tools=[],
    )
    result = await runtime.run(request)
    print(result)


asyncio.run(main())
```

From here, read the [API reference](../api/index.md) for providers, tools, and
loop policies, or the [project reference](../avo-reference.md) for the full
runtime model.
