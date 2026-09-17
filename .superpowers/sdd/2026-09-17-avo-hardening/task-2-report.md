# Task 2 Report: Central database path resolution

## Status

Implementation verified. `resolve_database_path()` applies `--database` first, then
`AVO_DATABASE_PATH`, then `avo.db`. The main, cost, and diff CLI entry points use the
resolver. Unrelated unstaged changes were preserved.

## Initial focused command

```text
uv run pytest tests/test_config.py tests/test_cli.py tests/test_cost.py tests/test_diff.py -q
```

Result: exit 1 before pytest ran. `uv` could not resolve the optional
`avo-native>=0.1.4` dependency for its Python 3.15 resolution split. The repository's
existing `.venv` was used for all subsequent checks.

## TDD red

The inherited production implementation was temporarily reverted while retaining its
six regression tests.

```text
.venv/bin/python -m pytest tests/test_cli.py::test_cli_uses_database_path_from_environment_when_option_is_omitted tests/test_cli.py::test_cli_explicit_database_option_wins_over_environment tests/test_cost.py::test_cost_main_uses_database_path_from_environment_when_option_is_omitted tests/test_cost.py::test_cost_main_explicit_database_option_wins_over_environment tests/test_diff.py::test_diff_main_uses_database_path_from_environment_when_option_is_omitted tests/test_diff.py::test_diff_main_explicit_database_option_wins_over_environment -q
```

Result: exit 1, `3 failed, 3 passed in 0.23s`. All environment-fallback tests failed
against direct `Path("avo.db")` defaults; all explicit `--database` precedence tests
passed.

## TDD green

After restoring the central resolver:

```text
.venv/bin/python -m pytest tests/test_cli.py::test_cli_uses_database_path_from_environment_when_option_is_omitted tests/test_cli.py::test_cli_explicit_database_option_wins_over_environment tests/test_cost.py::test_cost_main_uses_database_path_from_environment_when_option_is_omitted tests/test_cost.py::test_cost_main_explicit_database_option_wins_over_environment tests/test_diff.py::test_diff_main_uses_database_path_from_environment_when_option_is_omitted tests/test_diff.py::test_diff_main_explicit_database_option_wins_over_environment -q
```

Result: exit 0, `6 passed in 0.12s`.

## Focused verification

```text
.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py tests/test_cost.py tests/test_diff.py -q
```

Result: exit 0, `51 passed in 0.36s`.

```text
.venv/bin/python -m ruff check src/avo/config.py src/avo/cli.py src/avo/cost.py src/avo/diff.py tests/test_cli.py tests/test_cost.py tests/test_diff.py
```

Result: exit 0, `All checks passed!`.

```text
.venv/bin/python -m ruff format --check src/avo/config.py src/avo/cli.py src/avo/cost.py src/avo/diff.py tests/test_cli.py tests/test_cost.py tests/test_diff.py
```

Result: exit 0, `7 files already formatted`.

```text
.venv/bin/python -m mypy src/avo/config.py src/avo/cli.py src/avo/cost.py src/avo/diff.py
```

Result: exit 0, `Success: no issues found in 4 source files`.

```text
git diff --cached --check
```

Result: exit 0 with no output.

## Final staged-snapshot verification

The Git index was exported to `/tmp/avo-task2-index.h1xcHl` so the final test covered
exactly the intended commit and excluded unrelated unstaged changes.

```text
env PYTHONPATH=/tmp/avo-task2-index.h1xcHl/src /home/faqihhakim/Project/avo/.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py tests/test_cost.py tests/test_diff.py -q
```

Result: exit 0, `51 passed in 0.48s`.

## Concerns

The repository-wide `uv run` dependency-resolution issue above is external to Task 2.
No full suite was run, per instruction.

## Fix round 1

### Findings addressed

- The `chat` subparser now suppresses its absent `--database` default, so an explicit
  parent option in `avo --database explicit.db chat` is retained. A chat-local
  `--database` remains supported.
- The delegated `cost` route now forwards the parsed argument tail and prepends a
  parsed parent-level database option when present. It no longer derives cost
  arguments from process-global `sys.argv`.
- Entry-point regression coverage now exercises explicit-over-environment and
  environment fallback behavior through `avo.cli.main` for chat, cost, and
  `runs diff`. The resolver's local `avo.db` fallback is covered directly.

### TDD red

```text
.venv/bin/python -m pytest tests/test_cli.py::test_cli_chat_entry_point_preserves_global_database_precedence tests/test_cli.py::test_cli_cost_entry_point_forwards_global_explicit_database tests/test_cli.py::test_cli_cost_entry_point_uses_environment_database tests/test_cli.py::test_cli_runs_diff_entry_point_preserves_global_database_precedence tests/test_config.py::test_resolve_database_path_falls_back_to_local_database -q
```

Result before production changes: exit 1, `3 failed, 2 passed in 0.39s`.
Chat selected the environment path instead of the explicit parent value, and both
cost cases attempted to parse an unrelated process-global argument.

### TDD green

The same focused regression command after the two `src/avo/cli.py` changes:

```text
.....                                                                    [100%]
5 passed in 0.10s
```

### Focused verification

```text
.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py tests/test_cost.py tests/test_diff.py -q
```

Result: exit 0, `56 passed in 0.37s`.

```text
.venv/bin/python -m ruff check src/avo/cli.py tests/test_cli.py tests/test_config.py
```

Result: exit 0, `All checks passed!`.

```text
.venv/bin/python -m ruff format --check src/avo/cli.py tests/test_cli.py tests/test_config.py
```

Result: exit 0, `3 files already formatted`.

```text
.venv/bin/python -m mypy src/avo/cli.py src/avo/config.py
```

Result: exit 0, `Success: no issues found in 2 source files`.

### Scope note

The `runs diff` entry-point test replaces only the synchronous diff operation and
asserts the real `SQLiteEventStore.path` selected by the parser/resolver route.
Executing the existing `diff_runs()` implementation inside `avo.cli.main()` would
exercise a separate pre-existing nested-`asyncio.run()` limitation outside this fix
round. No full suite was run, as requested.

### Final staged-snapshot verification

The Git index was exported to `/tmp/avo-task2-fix1.8NA9NW` so verification excluded
all unrelated unstaged worktree changes.

```text
env PYTHONPATH=/tmp/avo-task2-fix1.8NA9NW/src /home/faqihhakim/Project/avo/.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py tests/test_cost.py tests/test_diff.py -q
```

Result: exit 0, `56 passed in 0.48s`.
