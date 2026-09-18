# Milestone Two: Agent Orchestration and Deterministic Replay

## Status

Approved design for inline implementation on `feat/token-savers`.

## Goal

Make Avo's existing isolated `task` runtime usable from the interactive chat by
adding named agent profiles, `@mention` routing, bounded parallel delegation,
and a deterministic replay command that consumes the persisted event ledger
without contacting a vendor.

## Scope

This milestone covers four connected capabilities:

1. A provider-neutral agent profile registry with built-in profiles and
   workspace profiles loaded from `.avo/agents/`.
2. Interactive `@agent` routing and an `/agents` picker, while preserving
   `@path` and `file://` attachment syntax.
3. Parent-owned delegation of isolated child runtimes, including explicit
   pipe-separated parallel requests with a bounded concurrency limit.
4. Event-ledger replay and verification for completed runs, exposed through
   `avo runs replay RUN_ID` and `/replay RUN_ID`.

The milestone does not add a new provider protocol, a new model catalog, a
remote job service, or an unrestricted autonomous swarm. Child runs reuse the
existing `AgentRuntime`, `EventStore`, `ToolRegistry`, approval callback, and
`BackgroundJobManager` contracts.

## User experience

### Named agents

The following built-ins are always available:

| Name | Capability | Default behavior |
| --- | --- | --- |
| `explore` | read-only | Inspect files, search, map the workspace, and summarize findings. |
| `reviewer` | read-only | Review code, tests, and diffs; never mutate the workspace. |
| `coder` | inherited write policy | Implement requested changes using the parent's approval policy. |

Workspace agents are Markdown files at `.avo/agents/<name>.md`. The filename
is the stable slug. The first non-empty line is the display description and the
remaining body is the system prompt. Invalid names, oversized files, and
malformed profiles are ignored with a diagnostic rather than crashing chat.

`@explore inspect the provider adapters` routes the request to one child run.
Only registered names are treated as mentions. An unknown `@word` remains
ordinary prompt text, and an existing attachment token such as `@src/app.py`
continues to be parsed by the attachment layer.

`/agents` opens an interactive picker with search and shows the selected
profile's capability. `/agents list` prints a non-interactive list for pipes and
tests. `/agent add NAME DESCRIPTION` creates a workspace profile with a safe
starter prompt; writing the body is explicit and does not execute a child run.

### Parallel delegation

The chat parser accepts a single top-level pipe between complete agent
mentions:

```text
@explore map the repository | @reviewer inspect the latest diff
```

Each segment must begin with a registered agent mention. A segment containing a
pipe inside a quoted string is not split. Delegation runs at most four children
at once, preserves input order in the rendered summary, and returns a stable
child identifier of the form `<parent-run-id>.<agent-slug>.<ordinal>`.

Every child receives:

- a fresh event store namespace and a fresh runtime execution lock;
- the parent's provider and model adapter without sharing mutable provider
  cursor state across simultaneous children;
- an agent system prompt plus the original task;
- the agent's filtered tool set;
- the parent's approval policy for tools that remain available.

`explore` and `reviewer` receive only read-only tools. `coder` receives the
parent tool set, but writes and terminal commands still use Avo's existing
permission callback. A child failure is reported beside successful siblings;
one failed child does not cancel unrelated siblings. Invalid syntax or an
unknown agent fails before any child is started.

## Architecture

### Agent profiles and mentions

Create `src/avo/agent_profiles.py` with immutable `AgentProfile` values and an
`AgentProfileRegistry`. The registry merges built-ins with workspace Markdown
profiles, giving explicit workspace profiles precedence by name. It exposes
`get`, `list`, `load`, and `parse_prompt` operations. Mention parsing returns a
typed `DelegationRequest` rather than modifying the raw prompt in place.

`src/avo/chat.py` owns one registry per `ChatContext`, rooted at the current
workspace. `src/avo/chat_commands.py` owns `/agents` and delegates picker
rendering to a small helper in `src/avo/agent_profiles.py` so non-interactive
callers can use the same data.

### Delegation coordinator

Create `src/avo/delegation.py` with a `DelegationCoordinator` that accepts a
parent `AgentRuntime`, the parent's tools and approval callback, an event-store
factory, and a concurrency limit. It creates child runtimes through the
existing runtime constructor and calls `asyncio.gather` behind a semaphore.
The coordinator returns immutable `DelegationResult` values containing profile,
child run ID, status, output, error, and token usage.

The parent chat turn persists the user request once. Child runs are persisted
as normal runtime events with parent metadata in `user_state`; the parent
renders only compact summaries so child model output is not double-printed.
The normal one-agent path remains unchanged when no recognized mention exists.

### Deterministic replay

Create `src/avo/replay.py` with:

- `ReplayTranscript.from_events(run, events)` extracting the recorded model
  responses and durable tool results in sequence order;
- `DeterministicReplayProvider`, a `ModelProvider` implementation that returns
  recorded `ModelResponse` values and validates each incoming request against
  the recorded request fingerprint;
- `ReplayReport` containing `run_id`, `matched_events`, `divergences`, and a
  deterministic transcript fingerprint.

Replay is read-only: it never invokes a network provider or tool callable. It
reconstructs the model decision sequence and checks that the recorded tool
result events are internally consistent. It reports unsupported runs (missing
model responses, pending unsafe tool calls, or incomplete event sequences) as
actionable errors. A replay does not overwrite the original run or create a
second production run.

The CLI command prints a concise verified/diverged result and supports `--json`
for automation. The slash command reuses the same service and prints the
transcript fingerprint. Existing `avo runs resume` behavior remains separate:
resume continues a non-terminal run, while replay verifies a terminal run from
recorded facts.

## Error handling and safety

- Agent names are lowercase ASCII slugs with a maximum length of 32.
- Profile files are read only from the workspace `.avo/agents` directory after
  resolving containment; symlinks escaping the workspace are ignored.
- Profile prompts and task text are bounded before child creation.
- Parallel delegation has a fixed default limit of four and rejects empty
  segments.
- Child tools are filtered before the model sees their schemas.
- Child output and exceptions are length-bounded in the parent summary.
- Replay fingerprints exclude timestamps, random event IDs, credentials, and
  absolute workspace paths.
- Provider credentials, attachment bytes, and raw environment variables never
  enter agent profile files, delegation metadata, or replay output.

## Compatibility

- `AgentRuntime.run`, `task_tool`, existing slash commands, and plain prompts
  retain their current signatures and behavior.
- `@file`, `file://`, and ordinary email-like text remain attachment/prompt
  behavior, not agent delegation, unless the token exactly matches a loaded
  agent slug at a valid mention boundary.
- Existing background jobs remain available; parallel delegation reports child
  runs through the same job status and trace interfaces where applicable.
- Python 3.11, 3.12, and 3.13 remain supported with no new required runtime
  dependency.

## Testing and acceptance criteria

The implementation is accepted when:

1. Unit tests cover built-in/workspace profile loading, containment, malformed
   profiles, mention/pipe parsing, and attachment disambiguation.
2. Delegation tests prove read-only tool filtering, parent policy propagation,
   bounded concurrency, stable ordering, isolated child stores, and partial
   failure handling.
3. Replay tests prove a recorded fake-provider run produces the same transcript
   fingerprint, detects request/response divergence, and rejects unsafe or
   incomplete runs without invoking a real provider or tool.
4. CLI and slash-command tests cover `/agents`, `/delegate`, `/replay`, and
   `avo runs replay --json`.
5. Focused tests, ruff, format check, mypy, bandit, and `git diff --check`
   pass. Full pytest is attempted with a bounded timeout and any environment-
   only timeout is reported honestly.

