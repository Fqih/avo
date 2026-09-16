# NEXT-UPDATE.md — Avo development roadmap (v0.3 era)

Status date: 2026-09-16. This file is the single source of truth for
*where Avo development is going*: approved designs, adoption candidates,
targets, and plan locations. One section per subproject; each gets its
own spec → plan → implementation → approval cycle.

## Where we are

- v0.2 era items from the previous version of this file (chat REPL,
  first-run setup, git awareness, workspace tools, sandbox, approval
  gates, MCP, plugins/skills, cost ledger) are **done and tested** —
  see `docs/reference/` for what shipped.
- `avo` is published on PyPI (v0.1.x, trusted publishing).
- Docs site redesign complete on branch `docs/redesign` (8 commits,
  rebased on main, strict build, 0 broken links) — pending
  push/merge decision.
- Active feature branches: `feat/native-perf` (S5 spec+plan,
  execution delegated), `feat/token-savers` (S6 spec+plan,
  execution delegated).

## Subproject map

| ID | Subproject | Status | Spec / plan |
|----|------------|--------|-------------|
| S1 | Subscription OAuth auth (Claude, ChatGPT/Codex, Gemini) + universal key login | **done and tested** | `docs/superpowers/specs/2026-09-16-subscription-oauth-auth-design.md` + `docs/superpowers/plans/2026-09-16-subscription-oauth-auth.md` |
| S2 | Combo routing: one conversation, many models (named tiers, quota fallback) | **done and tested** | `docs/superpowers/specs/2026-09-16-combo-routing-design.md` + `docs/superpowers/plans/2026-09-16-combo-routing.md` |
| S3 | Ollama as free tier in combos | **done (folded into S2)** | — |
| S4 | GitHub discoverability: README rewrite, topics, Acknowledgments, demo | **done and verified** | `docs/superpowers/specs/2026-09-16-github-discoverability-design.md` + `docs/superpowers/plans/2026-09-16-github-discoverability.md` |
| S5 | Native performance: extend `avo_native` (Rust/pyo3) for benchmarked hot paths — SSE parse, token counting, event codec | spec+plan approved; execution delegated on feat/native-perf | `docs/superpowers/specs/2026-09-16-native-performance-design.md` + `docs/superpowers/plans/2026-09-16-native-performance.md` (on `feat/native-perf`) |
| S6 | Token savers: RTK-style input compression pipeline, Caveman-style terse-output preset, Ponytail-style YAGNI system prompt, context pre-trimmer | spec+plan approved; execution delegated on feat/token-savers | `docs/superpowers/specs/2026-09-16-token-savers-design.md` + `docs/superpowers/plans/2026-09-16-token-savers.md` |

## Decisions log (user-approved, 2026-09-16)

1. S1 pilot = all three subscription providers at once (shared PKCE
   infra; splitting only delays review twice).
2. Subscription endpoints are **explicit opt-in**: token store accepts
   subscription logins unconditionally, but inference to unofficial
   subscription backends requires `AVO_ALLOW_SUBSCRIPTION=1`. Docs
   state the ToS/suspension risk honestly. (9router itself marks all
   three grants `RISK_NOTICE` upstream.)
3. Cadence: one subproject at a time, each with its own approval gate.
4. Performance stays Python-first: Rust only via the existing
   `avo_native` pyo3 pattern (benchmark-gated, mandatory identical
   pure-Python fallback). Node.js rejected as a runtime language —
   second process = more RAM, only fits proxy-server shape, not an
   in-process library.
5. Core dependency policy unchanged: `pydantic` only in
   `dependencies` (reference §19). Anything else = optional extra.

## Adoption board

Filter: MIT/Apache/BSD licenses only; three adoption modes —
`depend (extra)`, `port + credit`, `reference only`.

| Upstream | License | Mode | What we take | Lands in |
|---|---|---|---|---|
| [decolua/9router](https://github.com/decolua/9router) | MIT | port + credit | OAuth flow shapes + per-provider constants (endpoints, client ids, scopes, ports, identity headers); token-refresh lifecycle | S1 |
| [clash-ru/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (Go) | MIT | reference only (9router's upstream lineage) | Flow-structure validation | S1 credits |
| [BerriAI/liteLLM](https://github.com/BerriAI/litellm) | MIT | reference only | Fallback/retry matrix, provider+cost tables (as *data* reference; not a dependency — too heavy) | S2, docs |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | MIT | optional later | Avo's `mcp.py`/`serve-mcp` already exist; SDK may replace hand-rolled client as `[mcp]` extra someday | backlog |
| [tiktoken](https://github.com/openai/tiktoken) / HF `tokenizers` (Rust) | MIT / Apache-2.0 | depend (extra) | Exact token counts for TokenLedger/budgets | S5 |
| RTK (Rust token-saver) | MIT | port concept | Prompt-compression pre-pass → −20–40% input tokens | S6 |
| Caveman / Ponytail prompt tricks | MIT | port concept | Terse-output + YAGNI-ladder presets via skills/hooks | S6 |
| [Textualize/rich](https://github.com/Textualize/rich) | MIT | depend (`[ui]` extra) | Pretty streaming, spinners, tables in `avo chat` / `avo runs` | S1-adjacent polish |
| pluggy, guidance, marvin, click/typer | — | **rejected** | Avo already has hooks/entry-points/plugins (§18); guidance needs local logits; marvin is a competing framework; argparse works | — |

## Targets (definition of done per subproject)

**S1** — `avo login claude|codex|gemini` end-to-end on a real account;
tokens survive reboot (store v2, 0600, auto-refresh incl. rotation);
`avo login <any-provider> --key-stdin` stores plain keys;
`AVO_ALLOW_SUBSCRIPTION` gate enforced; full offline test suite green
(no CI ever hits a vendor); `avo doctor`/`--status` show account +
expiry without leaking tokens.

**S2** — `avo combo new coder --tier subscription=... --tier cheap=...
--tier free=ollama/llama3.2`; one `runtime.run()` transparently fails
over on 429/quota/cooldown; every model switch is an event in the log
(replayable — Avo's differentiator vs 9router).

**S4** — repo topics ≥ 8, one-paragraph hook description, README with
S1+S2 demo, comparison table (9router/liteLLM/OpenRouter),
Acknowledgments block; stars goal: organic.

**S5** — benchmark harness committed under `benchmark/native/`;
`avo_native` extended only where ≥ 2x win on a proven hot path.

**S6** — `/avo saver` presets as skills; measured token reduction on
the deterministic benchmark, honest numbers in docs.

## Workflow rules (unchanged from v0.2, still binding)

- Never bypass EventStore / state machine / checkpoint / policy engine;
  every new feature emits observable events and stays resumable.
- Small atomic conventional commits (`feat(auth): ...`), single author,
  push via `~/.local/bin/git-push-notify`.
- Gates every task: `ruff check . && ruff format --check . && mypy
  src/avo && pytest` (coverage ≥ 90%).
- No new core dependency; extras follow `[sandbox]`/`[providers]`
  pattern.
