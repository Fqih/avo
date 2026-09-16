# S6 — Token savers: compression pipeline + prompt presets (design)

Date: 2026-09-16. Approved by user (brainstorm answers A/A/A:
one spec two groups; provider-decorator placement; explicit
opt-in gating). Roadmap DoD (NEXT-UPDATE.md §Targets S6):
"`avo saver` presets as skills; measured token reduction on the
deterministic benchmark, honest numbers in docs."

## Goals

Two deliverable groups, one subsystem `avo/savers/`:

1. **Pipeline** — deterministic, no-LLM, RTK-style input
   compression applied to `ModelRequest.messages` immediately
   before the inner provider, via a `SaverProvider` decorator
   (same combo-decorator precedent as `avo/combo/provider.py`).
2. **Presets** — named bundles `{prompt addendum?, pipeline
   config?}` whose addenda ship as built-in skill files
   (Caveman-style terse output, Ponytail-style YAGNI ladder).

Everything is **opt-in**: without `AVO_SAVER`/`avo saver use`,
behavior is bit-identical to today (zero overhead, no new
events, no prompt change).

## Non-goals

- No tiktoken / HF tokenizers (that door is S5's, benchmark-gated).
  Estimates here use the existing `context_advisor.
  estimate_text_tokens` heuristic (`len(text)//4`,
  `src/avo/context_advisor.py:58`); every reported number carries
  that caveat.
- No LLM-based rewriting. `compact.py:compact_messages` stays the
  `/compact` path; the pipeline complements it (cheap cuts first,
  LLM summary still available for the rest).
- No hooks change: `hooks.HookEvent` has no model-request hook and
  we do not add one — the decorator covers the request-time seam.
- No state-machine, event-store, or `AgentRuntime` core changes.
  One additive enum member only (§5), mirroring S2's
  `ROUTE_FAILOVER` precedent.
- No `skills.py` refactor. Built-in skills are plain files the
  existing `SkillRegistry` can already walk if pointed at them.

## Facts this design is pinned to (verified in-tree)

- `ModelRequest.messages: list[dict[str, JsonValue]]`
  (`src/avo/models.py:112`); `ModelRequest` is a frozen-ish pydantic
  `AvoModel` — mutate via `model_copy(update=...)`, never in place.
- `ModelProvider` protocol: `async generate(request) -> response`
  (`src/avo/providers/base.py:16`); `StreamingModelProvider` adds
  `stream` (`src/avo/providers/streaming.py:170-177`).
- Decorator precedent: `ComboRouterProvider(inner tiers…,
  event_callback=…, notifier=…)`, `name = "combo"`,
  `model = f"combo({profile.name})"`, callback invoked at
  `src/avo/combo/provider.py:160-162` (`await` if awaitable).
- Event enum has 20 members, additive append is legal mid-run
  (invariants at `src/avo/events.py:71-129` check only
  STATE_CHANGED/TOOL_*/terminal shapes).
- Provider factory: `config.build_provider_from_env`
  (`src/avo/config.py:200`); combo branch at `:267`.
- Skill system: flat `<root>/<name>.md`, slug pattern
  `^[a-z0-9][a-z0-9_-]{0,63}$` (`src/avo/skills.py:30`);
  chat REPL root is `<workspace>/.avo/skills` (`src/avo/chat.py:197-201`).
- Config dir helper: `auth.default_auth_dir()` honors
  `AVO_CONFIG_DIR`/`XDG_CONFIG_HOME` (`src/avo/auth.py:35-46`).
- CLI delegation pattern: `commands.add_parser("combo", …)` +
  `combo_main(tail or _tail_argv("combo"))` + membership in the
  trailing-args name set (`src/avo/cli.py:163-166, 184-187, 323`).

## 1. Module layout

```
src/avo/savers/
    __init__.py        # public exports
    stages.py          # SaverStage protocol + 4 stage classes
    pipeline.py        # PipelineConfig + run_pipeline()
    presets.py         # SaverPreset, BUILTIN_PRESETS, resolve_saver()
    provider.py        # SaverProvider
    config_store.py    # saver.json read/write
    cli.py             # avo saver list|show|use|off
src/avo/skills_builtin/
    __init__.py
    caveman-terse.md   # terse-output addendum (RTK/Caveman concept port)
    ponytail-yagni.md  # YAGNI-ladder addendum (Ponytail concept port)
```

`skills_builtin/*.md` ride the existing hatchling wheel target
(`[tool.hatch.build.targets.wheel] packages = ["src/avo"]` —
hatchling ships all files under the package dir, no pyproject
change needed; a test opens one via `importlib.resources` to
prove it resolves from the installed-tree package).

## 2. Stage contract

```python
class SaverStage(Protocol):
    name: str
    def apply(
        self, messages: list[dict[str, JsonValue]]
    ) -> list[dict[str, JsonValue]]: ...
```

Hard invariants (tested per stage):

- **Deterministic**: same input → same output; no wall clock, no
  randomness, no LLM, no I/O.
- **Immutable**: returns a new list; rewritten messages are new
  dicts (`dict(m)` then replace content). Input never mutated.
- **Structural safety**: length and per-index `role` never change;
  `tool_call_id`/`tool_calls` pairing fields never touched —
  stages may only shrink the *content* of `role == "tool"`
  messages. This keeps every provider request shape valid.
- **System pin**: index 0 and any `role == "system"` message is
  never modified (protects persona prefix and
  `cache_prefix_messages` cache alignment).
- **Identity on no-match**: input without compressible content →
  `==` output.

Stages (all operate on `content` values that are `str`, or
list-content blocks whose text parts are `str`):

| Stage | Behavior |
|---|---|
| `json_minify` | Tool content that parses as JSON re-serialized `json.dumps(obj, separators=(",", ":"))`; keep only if shorter. |
| `dedupe_tool_results` | First occurrence of an exact tool-result string kept; later identical contents → `[same as message #<n>]` (n = index of first occurrence; sha256 of exact content for the map). |
| `elide_verbose_output` | Tool content over `max_lines`: `keep_first` head + `\n[... N lines elided ...]\n` + `keep_last` tail. |
| `pre_trimmer` | When estimated request tokens > `trigger_tokens`: walk eligible tool messages oldest→newest (skipping the last `keep_recent` messages and system pins), shrink each to `tool_content_max_chars` via head/tail elision until estimate ≤ `target_tokens` or candidates exhausted. Never deletes messages. |

`estimate_text_tokens` applies to the `str(...)` rendering of each
message's `content` value, summed.

## 3. Pipeline + config

```python
class ElideConfig(AvoModel):      # max_lines: int = 80, keep_first: int = 20, keep_last: int = 20
class PreTrimConfig(AvoModel):    # trigger_tokens: int = 6000, target_tokens: int = 4500,
                                  # keep_recent: int = 8, tool_content_max_chars: int = 1200
class PipelineConfig(AvoModel):   # json_minify: bool = True
                                  # dedupe: bool = True
                                  # elide: ElideConfig | None = ElideConfig()
                                  # pre_trim: PreTrimConfig | None = None
```

Each config model adds
`model_config = ConfigDict(frozen=True, extra="forbid")` — the
combo pattern (`src/avo/combo/models.py:15,27,39`; `AvoModel`
itself is `extra="forbid", validate_assignment=True`, not
frozen).

Order: `json_minify → dedupe → elide → pre_trim` (cheapest and
safest first; pre_trim last so it sees post-compression sizes).

```python
@dataclass(frozen=True)
class PipelineResult:
    messages: list[dict[str, JsonValue]]
    stages_applied: tuple[str, ...]   # stages whose output != input
    tokens_before: int
    tokens_after: int

def run_pipeline(messages, config) -> PipelineResult
```

`stages_applied` computed by equality check per stage
(`out != in`), not by config flags — the event reports what
actually happened.

Validation: `target_tokens <= trigger_tokens`, all ints
positive (`ge=1`); bad config raises pydantic `ValidationError`
at construction — fail-closed like combo.

## 4. SaverProvider

```python
class SaverProvider(StreamingModelProvider):
    name = "saver"

    def __init__(
        self,
        inner: ModelProvider,
        preset: SaverPreset,
        *,
        event_callback: SaverEventCallback | None = None,
        notifier: SaverNotifier | None = None,
    ) -> None:
        self.model = f"saver({preset.name}:{inner.model})"
```

- Implements `generate` and `stream`, delegating to the inner
  provider (`stream` requires inner to have `stream`; mirror the
  combo `hasattr`/`response_to_chunks` handling — if inner is a
  plain `ModelProvider`, degrade like combo does for non-streaming
  tiers).
- On each request: `run_pipeline(request.messages,
  preset.pipeline)` (skip when pipeline is None) and, when the
  preset has a skill addendum, inject it as **one additional
  `role: "system"` message at index 1** — never touching index 0:
  `"Respond per this style guide:\n<body>"`. Idempotence guard:
  skip injection when index 1 already equals the addendum (guards
  retry/replay paths that re-send the same history).
- Delegate with `request.model_copy(update={"messages": new})`.
- Emit `SAVER_APPLIED` via `event_callback` (same await-or-call
  shape as `src/avo/combo/provider.py:160-162`) once per request
  when anything changed (stages applied or addendum injected).
  Original messages remain in the event log — the run stays
  replayable; compression is request-time only, never persisted
  back into history.
- Pass-through fast path: no preset pipeline + no addendum +
  nothing applied → no copy, no event; inner sees the exact
  request object.

## 5. EventType.SAVER_APPLIED

Additive member in `avo/events.py` (after `ROUTE_FAILOVER`):
`SAVER_APPLIED = "saver_applied"`. No new invariant checks —
generic append rules already allow it mid-run.

Payload (all `JsonValue`): `preset`, `stages_applied`
(list[str]), `tokens_before`, `tokens_after`, `saved_percent`
(float, 0 when before=0), `message_count`, `addendum` (bool).
`avo runs inspect` rendering: add a `SAVER_APPLIED` branch to the
per-type summary chain in `src/avo/tracing.py` (~line 140;
`ROUTE_FAILOVER` today falls through to bare
`return event_type.value` — S6 does better with a one-line
summary `saver: <preset> −<n>% (<before>→<after>)`).

## 6. Presets

```python
class SaverPreset(AvoModel):   # frozen
    name: str
    description: str
    skill_name: str | None = None      # key into skills_builtin
    pipeline: PipelineConfig | None = None

BUILTIN_PRESETS: dict[str, SaverPreset]
def resolve_saver(name: str) -> SaverPreset | None
def builtin_skill_body(skill_name: str) -> str   # importlib.resources
```

Built-ins:

| Preset | Addendum skill | Pipeline |
|---|---|---|
| `terse` | `caveman-terse` | none |
| `yagni` | `ponytail-yagni` | none |
| `compact` | none | `PipelineConfig()` |
| `full` | `caveman-terse` | `PipelineConfig(pre_trim=PreTrimConfig())` |

Skill files are plain `.md` slugs so `SkillRegistry(
Path(str(builtin_skills_dir())))` works out of the box (a test
asserts this — "presets as skills" per roadmap DoD) and operators
can copy one into `<workspace>/.avo/skills/` for `/skill` use.

Addendum content: original text written for Avo (concepts ported
only — RTK/Caveman/Ponytail are `port concept` per adoption
board, no code or license-compatibility issue; MIT concepts don't
need source blobs).

## 7. Configuration resolution

- `avo/savers/config_store.py`: `read_saver_setting() -> str |
  None` reads `<default_auth_dir()>/saver.json`
  `{"preset": "<name>"}`; missing file/dir/invalid JSON → None
  (never raises). `write_saver_setting(name | None)` writes or
  removes the file (`avo saver off` = remove).
- Precedence: `AVO_SAVER` env > `saver.json` > unset. Same
  env-over-file rule as combo.
- Unknown name at build time → `ConfigError` listing valid names
  (fail-closed, no silent passthrough).

## 8. Wiring — build_provider_from_env

`build_provider_from_env` has ~10 early `return`s, so wrapping is
done by extracting the existing body verbatim into a private
`_build_base_provider_from_env` (pure move, zero behavior change)
and having the public function resolve the preset (env/file) and
wrap the result once when set — so combos get
`saver(combo(...))`. Precedent note: the factory wires **no**
provider event callbacks today (even the combo branch returns
callback-less, `src/avo/config.py:290`) — callbacks are
programmatic-only. S6 adds one opt-in path: optional kwarg
`event_callback: Callable[[dict], Awaitable[None] | None] | None
= None` to `build_provider_from_env` (default None = today's
behavior) and pass it to the SaverProvider only. Chat/web
callers may pass one; runtimes built directly from env without a
callback still work (SAVER_APPLIED then simply not logged —
documented limitation, not silent data loss).

## 9. CLI

`avo saver list | show NAME | use NAME | off` — new subcommand in
`cli.py` following the combo delegation pattern exactly
(`add_parser("saver", …)`, `saver_main(tail or
_tail_argv("saver"))`, add `"saver"` to the trailing-args set at
`src/avo/cli.py:323`). Implement in `avo/savers/cli.py`
(`main(argv) -> int`).

- `list`: table name/description.
- `show NAME`: description + addendum body preview + pipeline
  summary.
- `use NAME`: validate against `BUILTIN_PRESETS`, write
  `saver.json`, print reminder that running chats need a restart.
- `off`: remove `saver.json`.

## 10. Benchmark (honest numbers)

`benchmark/savers/` (mirrors `benchmark/native/` shape per S5
convention):

- `fixtures.py` — deterministic tool-heavy transcripts, seed 42
  (fake `run_shell`/`git log`/`grep` outputs, pretty-printed JSON
  payloads ≥ 40 lines, repeated identical tool results, long
  verbose logs). Determinism asserted by sha256 print, same as S5
  Task 2.
- `run_saver_bench.py` — per-stage and per-preset estimated token
  reduction over 3 transcripts (small/medium/large), stdlib
  `len//4` estimator, writes `RESULTS.md` (committed) with the
  caveat sentence: estimates are the `len(text)//4` heuristic,
  not real tokenizer counts.
- No claim of −20–40% in docs unless measured; ROADMAP
  adoption-board line "−20–40%" stays a hypothesis until
  RESULTS.md says otherwise.

## 11. Tests

All offline, no network, no Docker, mirrors existing suite style.

- `tests/test_savers_stages.py` — per stage: no-match identity,
  structural invariants (length/roles/pins), immutability (input
  list deep-compared after apply), boundary sizes (exactly
  max_lines, empty content, non-str content, list-content blocks),
  dedupe ordering + marker text, json_minify only-if-shorter.
- `tests/test_savers_pipeline.py` — order effects,
  `stages_applied` reflects real change, config validation
  rejects `target > trigger`.
- `tests/test_savers_provider.py` — with
  `avo.providers.fake.FakeProvider` (`src/avo/providers/fake.py:17`):
  inner receives compressed messages (index 0
  untouched, addendum at index 1, marker contents), request
  object passthrough fast path (same object when nothing
  changed), `model` string shape, stream delegation, addendum
  idempotence on repeated send.
- `tests/test_savers_events.py` — `SAVER_APPLIED` appends fine
  mid-run via `validate_event_append` (generic rule), payload
  full-shape assertion, replay test: run with fake provider +
  in-memory EventStore, log contains originals while inner saw
  compressed.
- `tests/test_savers_presets.py` — `SkillRegistry` walks
  `skills_builtin`, every preset name resolves, skill slugs match
  `_NAME_PATTERN`.
- `tests/test_savers_config_store.py` — env > file > None
  precedence, corrupt file → None, `off` removes file.
- `tests/test_savers_cli.py` — list/show/use/off, unknown name
  nonzero exit with valid names in message.
- `tests/test_config.py` additions — `build_provider_from_env`
  returns unwrapped provider when `AVO_SAVER` unset (fallback:
  behavior bit-identical), wraps when set (including over combo),
  unknown preset raises `ConfigError`.

## 12. Docs

- `docs/avo-reference.md` (main layout tree § + relevant section)
  new "Token savers" section; if `docs/redesign` merged first,
  update `docs/reference/` split pages instead (check
  `ls docs/reference/` at execution time, same note as S5 plan).
- `docs/reference/storage-and-config.md`-equivalent env table:
  `AVO_SAVER` row.
- CLI doc: `avo saver …` row.
- Changelog entry (0.1.7 minor — additive surface).

## 13. Quality gates + commit rules (binding)

- `ruff check . && ruff format --check . && mypy src/avo &&
  pytest` — every task; coverage gate ≥ 90%.
- Commits: single author **Fqih**, no `Co-Authored-By`, no
  generated-with footers (user global rule overrides any
  attribution reminder).
- Push only via `~/.local/bin/git-push-notify origin
  feat/token-savers`. No merge to main without user approval.
- Core deps unchanged: pydantic only; no new extras; the whole
  subsystem is stdlib + pydantic.
