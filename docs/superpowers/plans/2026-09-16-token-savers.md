# S6 — Token savers: execution plan

Spec: `docs/superpowers/specs/2026-09-16-token-savers-design.md`
(approved). Branch: `feat/token-savers` (from main, already cut).
Executor: implementing agent, TDD per task. Read the spec fully
before Task 1; spec wins over this plan on any conflict.

Binding contract (from spec §13 + repo rules):

- Gates every commit: `ruff check . && ruff format --check . &&
  mypy src/avo && pytest` (offline suite; coverage ≥ 90%).
- Core deps: pydantic only — no new dependency, no new extra.
- Never mutate `ModelRequest` or message dicts in place; always
  new list + `model_copy`.
- No state-machine / event-store / runtime-core edits; the only
  core-file touches are one enum line (`events.py`), one summary
  branch (`tracing.py`), factory wrap (`config.py`), CLI
  delegation (`cli.py`).
- Commits: single author Fqih, **no Co-Authored-By, no generated
  footer**. Push only
  `~/.local/bin/git-push-notify origin feat/token-savers`.
  Do not merge to main.

## Task 1 — Stages (`savers/stages.py`)

RED first: `tests/test_savers_stages.py` from spec §2 + §11
matrix (identity, invariants, immutability, boundaries).
Implement 4 stages + `SaverStage` protocol. Helper
`_content_str(m) -> str | None` for "text-addressable" content
(str, or list-blocks joined text — follow spec §2 note; if a
message's content is a list of blocks, rewrite only the `text`
parts of blocks, keep other fields).

Keep files < 400 lines: stages in one file is fine; split if not.

Commit: `feat(savers): deterministic message compression stages`

## Task 2 — Pipeline (`savers/pipeline.py`)

`tests/test_savers_pipeline.py` → `ElideConfig`,
`PreTrimConfig`, `PipelineConfig`, `run_pipeline`,
`PipelineResult`, fixed order, `stages_applied` by real diff,
validation (`target <= trigger`, `ge=1` ints).

Commit: `feat(savers): pipeline config and ordered runner`

## Task 3 — Skill files + presets

`src/avo/skills_builtin/{__init__.py,caveman-terse.md,
ponytail-yagni.md}` — write the addenda: terse-output rules
(short answers, no filler, fragments allowed, code unchanged)
and YAGNI ladder (stdlib before new code, one function before
classes, refactor later). Each ≤ ~40 lines, slug-valid.

`savers/presets.py`: `SaverPreset` (frozen, skill_name,
pipeline), `BUILTIN_PRESETS` (terse/yagni/compact/full per spec
table), `resolve_saver`, `builtin_skill_body`
(`importlib.resources.files("avo.skills_builtin")`).
`tests/test_savers_presets.py` incl. `SkillRegistry(
skills_builtin dir).names()` == the two slugs.

Commit: `feat(savers): built-in prompt-skill presets`

## Task 4 — EventType + provider decorator

- `events.py`: add `SAVER_APPLIED = "saver_applied"` after
  `ROUTE_FAILOVER` (one line).
- `tracing.py`: summary branch in the `_summarize` chain (line
  ~140 region):
  `saver: <preset> −<saved_percent>% (<tokens_before>→<tokens_after>)`.
- `savers/provider.py`: `SaverProvider` per spec §4 — generate +
  stream (require inner stream, else degrade via
  `response_to_chunks` like combo `provider.py:238`), addendum
  injection at index 1 with idempotence guard,
  `model_copy(update=...)`, event emission only on change,
  pass-through fast path.
- Tests: `tests/test_savers_provider.py` (use
  `avo.providers.fake.FakeProvider`),
  `tests/test_savers_events.py` (`validate_event_append` accepts
  mid-run; payload shape; tracing summary; replay-keeps-originals
  test with in-memory store per existing event-store test
  fixtures).

Commit: `feat(savers): SaverProvider decorator + SAVER_APPLIED event`

## Task 5 — Config store + factory wiring

- `savers/config_store.py` + `tests/test_savers_config_store.py`
  (isolate `AVO_CONFIG_DIR` with monkeypatched tmp dir — mirror
  how `tests/` isolate `auth.default_auth_dir`; see eed764f
  "isolate path home" for the precedent to follow).
- `config.build_provider_from_env`: extract current branch-body
  returns into private `_build_base_provider_from_env` (pure
  move, zero behavior change — diff must show only extraction),
  public function wraps result in `SaverProvider` when
  `AVO_SAVER`/`saver.json` resolves (env > file), passes
  optional `event_callback` kwarg added to the signature
  (default None → today's behavior unchanged). Unknown preset →
  `ConfigError` listing names.
- `tests/test_config.py` additions: unset = same provider type
  object (no wrapper), set = `SaverProvider` wrapping, over combo
  too, `AVO_SAVER=bogus` raises.

Commit: `feat(savers): saver.json setting + env factory wrapping`

## Task 6 — CLI

`savers/cli.py` `main(argv) -> int` (list/show/use/off per spec
§9) + `cli.py` delegation (parser near `combo` block ~:163,
dispatch ~:184, trailing-args set ~:323 add `"saver"`).
`tests/test_savers_cli.py`: subprocess-free — call `main` with
monkeypatched config dir; unknown name exit code; `off` deletes
file.

Commit: `feat(cli): avo saver subcommand`

## Task 7 — Benchmark (honest numbers)

`benchmark/savers/{fixtures.py,run_saver_bench.py}` per spec §10;
seed 42; runner prints sha256 of each transcript (determinism
assert, same as S5); generates committed `RESULTS.md` — per
stage × 3 transcripts + per preset, `len//4` estimates with the
heuristic caveat sentence. If measured reduction is small on
some transcripts, report it anyway; do not tune fixtures to
flatter the pipeline.

Commit: `bench(savers): deterministic token-reduction harness + RESULTS`

## Task 8 — Docs + close-out

- `avo saver` guide text: reference doc — main layout tree §, a
  "Token savers" section, env table `AVO_SAVER` row, CLI table
  row, changelog entry (0.1.7). NOTE: if `docs/redesign` has
  been merged by then (`ls docs/reference/`), update the split
  pages (`storage-and-config.md`, `mcp-and-cli.md`,
  `state-and-events.md` for SAVER_APPLIED's 21 members,
  `index.md` counts) instead of legacy sections; keep both in
  sync if the legacy `avo-reference.md` still exists.
- README: one line under features (token savers, opt-in).
- NEXT-UPDATE.md: S6 row → done and tested + spec/plan paths.
- Full gates + `uv pip install -e . && pytest tests/test_savers_*`
  from a clean venv to prove the .md files resolve from the
  installed package.
- Push: `~/.local/bin/git-push-notify origin feat/token-savers`.
  STOP — report; merge is the user's call.

Commit: `docs(savers): token-saver reference, changelog, roadmap`

## Decision gates for the human

- After Task 4: SAVER_APPLIED payload shape is frozen for
  docs/bench — flag any deviation you had to make.
- After Task 7: report RESULTS.md numbers verbatim before
  writing them into docs (honest-numbers rule).
