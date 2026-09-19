# Contributing to Avo

Avo welcomes focused changes that improve boundedness, observability,
recovery, or safety without expanding the project into a general agent
framework.

## Environment setup

Use Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

No API key or network access is needed after development dependencies are
installed.

## Local checks

Run the same gates used by CI:

```bash
ruff check .
ruff format --check .
mypy src/avo
pytest
python -m build
```

To measure core coverage:

```bash
coverage run -m pytest
coverage report
```

Run all offline examples and regenerate the benchmark when runtime behavior
changes:

```bash
python examples/basic_agent.py
python examples/repeated_action.py
python examples/resume_after_interrupt.py
python benchmark/run_benchmark.py
```

## Contribution workflow

1. Open an issue for substantial API or event-schema changes.
2. Create a focused branch and keep commits reviewable.
3. Preserve append-only history, state validation, and terminal-event
   invariants.
4. Add deterministic behavioral tests for every behavior change or bug fix.
5. Update `README.md`, `DESIGN.md`, examples, and benchmark results
   when user-visible contracts change.
6. Run every local check before opening a pull request.

Avoid real API calls in the default test suite. Error messages should name the
run, tool, provider operation, or database involved and explain what the user
can do next.

## Implementing a Custom Model Provider

To contribute or register an external model provider, implement the `ModelProvider`
or `StreamingModelProvider` protocol (`src/avo/providers/base.py`):

```python
from collections.abc import AsyncIterator
from avo import ModelRequest, ModelResponse
from avo.providers.base import ModelProvider
from avo.providers.streaming import ModelChunk, StreamingModelProvider

class CustomProvider(StreamingModelProvider):
    name: str = "custom"
    model: str = "custom-model"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        # 1. Translate request.messages to vendor format
        # 2. Invoke upstream API
        # 3. Return ModelResponse with content, tool_calls, and token usage
        ...

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        # Yield ModelChunk deltas for real-time text and tool streaming
        ...
```

Requirements for new providers:
1. Wrap network errors in `avo.exceptions.ProviderError`.
2. Map token counts into `avo.models.TokenUsage`.
3. Support function calling / tool definitions when upstream permits.
4. Include mock-based offline unit tests in `tests/test_<provider>_provider.py`.

## Pull requests

Describe the execution behavior before and after the change, the failure modes
considered, and the checks run. If the change affects persistence or resume,
include a close/reopen test and a failure-injection test at the relevant
checkpoint boundary.

## Maintenance & Review Policy

- All code must pass `ruff check .`, `ruff format --check .`, `mypy src/avo`, and `pytest`.
- Security-critical boundaries (sandbox, workspace containment, approval callbacks) require explicit review against `CLAUDE.md` and `docs/api-stability.md`.
- Attribution trailers (`Co-Authored-By`, `Generated with`) are strictly prohibited per project identity guidelines.

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
