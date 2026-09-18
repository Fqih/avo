# Milestone Two Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose named agent profiles, `@mention` routing, bounded parallel delegation, and deterministic event-ledger replay through Avo's existing runtime and CLI.

**Architecture:** Add a small profile/parser module for built-in and workspace agents, a delegation coordinator that creates isolated child `AgentRuntime` instances, and a replay module that validates recorded model requests/responses without network or tool execution. Wire them into `ChatContext`, slash commands, and the top-level `runs` command while preserving plain prompts, attachments, and existing `task` behavior.

**Tech Stack:** Python 3.11+, Pydantic 2, asyncio, prompt-toolkit, existing `AgentRuntime`/`EventStore`, SQLite, pytest/pytest-asyncio, ruff, mypy, bandit.

**Spec:** `docs/superpowers/specs/2026-09-18-milestone-two-design.md`

**Implementation status (2026-09-18):** Tasks 1–5 are implemented and verified.
The focused Milestone 2 suite passes; the full repository suite is currently
environment-blocked by pre-existing OAuth callback/refresh tests that wait for
external authentication state. The bounded full-suite result is reported in
the handoff rather than treated as a product pass.

## Global Constraints

- Preserve Python 3.11, 3.12, and 3.13 support.
- Do not add a required runtime dependency.
- Do not modify the user's untracked `.avo/` directory while testing or implementing.
- Write and run a failing test before each production behavior change.
- Keep profile files workspace-contained, bounded, and free of credentials.
- Keep child output bounded and never print provider credentials or raw environment variables.
- Preserve existing `AgentRuntime.run`, `task_tool`, attachment parsing, plain prompts, and slash-command compatibility.
- Parallel child failures are reported independently and never cancel unrelated siblings.
- Replay is read-only and never calls a network provider or application tool.

## File Map

- `src/avo/agent_profiles.py`: immutable profile records, workspace loader, mention/pipe parser, and picker data.
- `src/avo/delegation.py`: child-runtime construction, tool filtering, bounded parallel execution, and result records.
- `src/avo/replay.py`: recorded model response provider, request/event validation, fingerprints, and report rendering.
- `src/avo/chat.py`: add registry/factory state to `ChatContext` and route recognized mentions before the normal turn.
- `src/avo/chat_commands.py`: `/agents`, `/delegate`, and `/replay` handlers.
- `src/avo/chat_render.py`: help entries and compact delegation/replay summaries.
- `src/avo/cli.py`: `avo runs replay RUN_ID [--json]`.
- `src/avo/__init__.py`: export stable public profile/replay types only where existing package conventions require it.
- `tests/test_agent_profiles.py`: profile loading and parser behavior.
- `tests/test_delegation.py`: child isolation, policy/tool filtering, concurrency, and partial failure.
- `tests/test_replay.py`: deterministic transcript, divergence, and unsafe-run rejection.
- `tests/test_chat_agents.py`: chat/slash integration with fake runtime/provider.
- `tests/test_cli.py` or `tests/test_cli_help.py`: top-level replay command and help.
- `README.md`, `docs/cli.md`, `CHANGELOG.md`: user-facing Milestone 2 documentation.

### Task 1: Agent profiles and safe mention parsing

**Files:**
- Create: `src/avo/agent_profiles.py`
- Test: `tests/test_agent_profiles.py`
- Modify: `tests/test_attachments.py`

**Interfaces:**
- `AgentProfile(name: str, description: str, system_prompt: str, capability: AgentCapability, builtin: bool)` is frozen and JSON-safe.
- `AgentCapability` has `READ_ONLY` and `INHERITED` values.
- `AgentMention(agent: AgentProfile, prompt: str)` is immutable.
- `DelegationRequest(parts: tuple[AgentMention, ...])` is immutable and exposes `is_parallel: bool`.
- `AgentProfileRegistry(workspace_root: Path, *, max_profile_bytes: int = 32768)` exposes `get(name)`, `list()`, `load()`, and `parse_prompt(text)`.
- `parse_prompt` recognizes only a registered slug at the beginning of the prompt or after a top-level `|`; it leaves unknown `@words`, `@src/file.py`, `file://...`, and email text untouched.

- [x] **Step 1: Write failing profile and parser tests.**

  Cover built-ins, workspace override precedence, valid Markdown profiles, invalid names, symlink escape, file-size bounds, unknown mentions, attachment disambiguation, quoted pipes, empty segments, single mentions, and stable ordering.

- [x] **Step 2: Run the focused tests and verify RED.**

  Run: `python -m pytest -q tests/test_agent_profiles.py tests/test_attachments.py -k "agent or mention or attachment"`

  Expected: import/API failures because the profile registry does not exist.

- [x] **Step 3: Implement the immutable records, loader, and parser.**

  Resolve `.avo/agents` and each profile path before reading, reject paths outside the workspace, load only regular files, parse the first non-empty line as description, and cap the prompt body. Split pipes with a small quote-aware scanner rather than shell execution. Use a strict slug regex such as `^[a-z][a-z0-9-]{0,31}$`.

- [x] **Step 4: Run the focused tests and refactor only while green.**

  Run: `python -m pytest -q tests/test_agent_profiles.py tests/test_attachments.py -k "agent or mention or attachment"`

- [x] **Step 5: Commit the profile boundary.**

  Run: `git add src/avo/agent_profiles.py tests/test_agent_profiles.py tests/test_attachments.py && git commit -m "feat: add workspace agent profiles and mention parsing"`

### Task 2: Isolated delegation coordinator

**Files:**
- Create: `src/avo/delegation.py`
- Test: `tests/test_delegation.py`
- Modify: `src/avo/app_tools/task_tool.py`

**Interfaces:**
- `DelegationResult(agent_name: str, child_run_id: str, status: str, output: str | None, error: str | None, steps: int, token_usage: TokenUsage)` is immutable.
- `DelegationCoordinator(parent_runtime: AgentRuntime, *, provider_factory: Callable[[], ModelProvider] | None = None, event_store_factory: Callable[[], EventStore] = InMemoryEventStore, max_concurrency: int = 4)` exposes `async run(parent_run_id, requests, *, user_state=None) -> tuple[DelegationResult, ...]`.
- `child_tools(profile, parent_tools)` keeps `read_file`, `grep`, `glob`, `symbols`, `workspace_map`, `git_status`, and `git_diff` for read-only profiles and returns all parent tools for `coder`/inherited profiles.

- [x] **Step 1: Write failing coordinator tests.**

  Use a fake provider factory and event-store factory. Test child IDs are `<parent>.<slug>.<ordinal>`, read-only profiles never advertise write/terminal tools, coder inherits the approval callback, stores are distinct, result order follows request order, concurrency never exceeds four, and one provider failure does not cancel successful siblings.

- [x] **Step 2: Run the focused tests and verify RED.**

  Run: `python -m pytest -q tests/test_delegation.py`

  Expected: import/API failures because the coordinator does not exist.

- [x] **Step 3: Implement child runtime creation and bounded gather.**

  Create a fresh provider from `provider_factory` when supplied. Otherwise use a snapshot/from-snapshot clone for providers that expose both methods and fail clearly for a mutable provider that cannot safely be cloned for parallel use. Create a fresh store and runtime per child, pass the profile system prompt to `run`, place parent/profile metadata in `user_state`, wrap each call in an `asyncio.Semaphore`, and convert exceptions into bounded `DelegationResult.error` values.

- [x] **Step 4: Preserve existing `task_tool` behavior and run tests.**

  Make its child construction reuse the coordinator's tool filtering helper without changing its public arguments or return shape. Run: `python -m pytest -q tests/test_delegation.py tests/test_task_tool.py`

- [x] **Step 5: Commit the delegation core.**

  Run: `git add src/avo/delegation.py src/avo/app_tools/task_tool.py tests/test_delegation.py && git commit -m "feat: add bounded isolated agent delegation"`

### Task 3: Chat context, `@mention`, picker, and parallel UX

**Files:**
- Modify: `src/avo/chat.py`
- Modify: `src/avo/chat_commands.py`
- Modify: `src/avo/chat_render.py`
- Modify: `src/avo/chat_turn.py`
- Create: `tests/test_chat_agents.py`

**Interfaces:**
- `ChatContext.agent_profiles: AgentProfileRegistry` and `ChatContext.provider_factory: Callable[[], ModelProvider]` are initialized in `build_chat_context`.
- `_run_agent_request(ctx, request, out, err) -> bool` executes one or more parsed agent mentions and renders compact child summaries.
- `/agents`, `/agents list`, `/agent add NAME DESCRIPTION`, `/delegate`, and `/replay` are handled without sending command text to the model.

- [x] **Step 1: Write failing chat integration tests.**

  Assert `/agents list` prints built-ins, `/agent add` creates a profile, `@explore task` invokes a child instead of the parent provider path, `@explore one | @reviewer two` preserves result order, unknown `@word` remains a normal prompt, and `/delegate` uses the same picker/request parser.

- [x] **Step 2: Run the focused tests and verify RED.**

  Run: `python -m pytest -q tests/test_chat_agents.py`

  Expected: missing context fields/commands and no delegation output.

- [x] **Step 3: Wire the registry and provider factory into context construction.**

  Keep the original provider instance for the parent runtime. The factory recreates the configured provider from a copied environment for children. Do not persist credentials in the context or factory closure's rendered output. Load profiles lazily at context creation so a missing `.avo/agents` directory is not an error.

- [x] **Step 4: Route mentions before normal turn execution.**

  In the REPL submit path, parse recognized mentions after slash-command handling and before attachment preparation. A single child renders `• @name …`; parallel children render one compact result per segment. Do not print child stream deltas into the parent's answer area. Record the user request in the chat session once and keep normal prompts unchanged.

- [x] **Step 5: Add interactive agent selection and help.**

  Reuse the existing prompt-toolkit picker style used by `/resume`, with type-to-filter and arrow selection. `/delegate` without a valid explicit request selects an agent, then prompts for its task; non-TTY tests receive a deterministic textual fallback. Add command palette/help entries and update `/list agents`.

- [x] **Step 6: Run focused chat tests and commit.**

  Run: `python -m pytest -q tests/test_chat_agents.py tests/test_chat_repl.py -k "agent or delegate or command"`

  Commit with: `git add src/avo/chat.py src/avo/chat_commands.py src/avo/chat_render.py src/avo/chat_turn.py tests/test_chat_agents.py && git commit -m "feat: route chat mentions to named agents"`

### Task 4: Deterministic replay service

**Files:**
- Create: `src/avo/replay.py`
- Test: `tests/test_replay.py`
- Modify: `src/avo/tracing.py` only if a shared fingerprint helper is needed

**Interfaces:**
- `ReplayTranscript.from_events(run: RunRecord, events: Sequence[AgentEvent]) -> ReplayTranscript` validates terminal state, extracts ordered `MODEL_REQUESTED`, `MODEL_RESPONDED`, and completed/failed tool events, and rejects unsafe incomplete sequences with `ReplayError`.
- `DeterministicReplayProvider(transcript: ReplayTranscript)` implements `generate(ModelRequest) -> ModelResponse`, validates the canonical request fingerprint, and exposes `remaining`.
- `ReplayReport(run_id: str, verified: bool, matched_events: int, divergences: tuple[str, ...], fingerprint: str)` exposes `to_text()` and `to_json()`.
- `async replay_run(store: EventStore, run_id: str) -> ReplayReport` is read-only and never calls a tool or network provider.

- [x] **Step 1: Write failing replay tests.**

  Build a real fake-provider runtime with a text response and a tool response. Assert the transcript extracts the responses, identical requests verify with a stable fingerprint, modified request text creates a divergence, missing model response and unresolved `TOOL_STARTED` are rejected, and a real tool callable is never invoked during replay.

- [x] **Step 2: Run the focused tests and verify RED.**

  Run: `python -m pytest -q tests/test_replay.py`

  Expected: import/API failures because the replay service does not exist.

- [x] **Step 3: Implement canonical event/request normalization.**

  Normalize JSON with sorted keys and compact separators. Exclude event IDs, timestamps, absolute paths, credential-shaped keys, and provider metadata that is not part of the model decision. Preserve response/tool-call arguments and durable tool results. Require a matching request before consuming each recorded response.

- [x] **Step 4: Implement report generation and run tests.**

  Report every divergence with sequence and reason, include the deterministic transcript fingerprint, and use `ReplayError` for unsupported runs rather than returning a false verification. Run: `python -m pytest -q tests/test_replay.py tests/test_fake_provider.py tests/test_tracing.py`

- [x] **Step 5: Commit replay core.**

  Run: `git add src/avo/replay.py tests/test_replay.py src/avo/tracing.py && git commit -m "feat: add deterministic event ledger replay"`

### Task 5: CLI, slash command, and documentation surface

**Files:**
- Modify: `src/avo/cli.py`
- Modify: `src/avo/chat_commands.py`
- Modify: `src/avo/chat_render.py`
- Modify: `README.md`
- Modify: `docs/cli.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_help.py`

**Interfaces:**
- Add `runs replay` to the existing runs parser with required `RUN_ID` and optional `--json`.
- `/replay RUN_ID` prints the same report against the active store.
- Help/docs describe `@agent`, pipe-separated delegation, `.avo/agents`, and replay's read-only semantics.

- [x] **Step 1: Write failing CLI/help tests.**

  Assert parser accepts `avo runs replay RUN_ID --json`, JSON output contains `verified`, `matched_events`, and `fingerprint`, slash help lists the new commands, and docs mention the profile directory without exposing any local secrets.

- [x] **Step 2: Run focused tests and verify RED.**

  Run: `python -m pytest -q tests/test_cli.py tests/test_cli_help.py -k "replay or agent or delegate"`

- [x] **Step 3: Wire the CLI and slash handlers.**

  Reuse one `SQLiteEventStore`, close it in `finally`, map `ReplayError` to a non-zero actionable CLI error, and preserve all existing `runs list|inspect|diff|resume` branches. Slash handlers should not mutate the run ledger.

- [x] **Step 4: Update docs and changelog.**

  Document concrete command examples, capability boundaries, unknown-mention behavior, and the distinction between replay and resume. Keep existing Avo branding and logo references unchanged.

- [x] **Step 5: Run focused tests and commit.**

  Run: `python -m pytest -q tests/test_cli.py tests/test_cli_help.py tests/test_chat_agents.py -k "replay or agent or delegate or help"`

  Commit with: `git add src/avo/cli.py src/avo/chat_commands.py src/avo/chat_render.py README.md docs/cli.md CHANGELOG.md tests/test_cli.py tests/test_cli_help.py && git commit -m "feat: expose agent delegation and replay commands"`

### Task 6: Full verification and release notes

**Files:**
- Modify only files required by failing checks.
- Update: `docs/superpowers/plans/2026-09-18-milestone-two.md` checkboxes and `CHANGELOG.md` if the verified behavior differs from the plan.

- [x] **Step 1: Run the Milestone 2 focused suite.**

  Run: `python -m pytest -q tests/test_agent_profiles.py tests/test_delegation.py tests/test_chat_agents.py tests/test_replay.py tests/test_cli.py tests/test_cli_help.py tests/test_task_tool.py tests/test_attachments.py`

  Expected: all selected tests pass.

- [x] **Step 2: Run the offline quality gate.**

  Run: `ruff check . && ruff format --check . && python -m mypy src/avo && bandit -r src/avo -c pyproject.toml --severity-level medium && git diff --check`

  Expected: all checks pass with no secret or formatting findings.

- [x] **Step 3: Run the full offline suite with a bounded timeout.**

  Run: `timeout 90s python -m pytest -q`

  Expected: pass, or an explicitly reported environment-only timeout matching the known Codex refresh shutdown issue; do not hide assertion failures.

- [x] **Step 4: Review the final diff and status.**

  Run: `git diff --stat HEAD~6..HEAD`, `git status --short`, and `git log --oneline -8`. Confirm `.avo/` remains untracked and untouched, no credentials are present, and each plan task is marked `[x]` only after its verification.
