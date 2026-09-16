# S4 — GitHub Discoverability Implementation Plan

- Status: ready for execution
- Branch: `feat/github-discoverability`
- Spec: `docs/superpowers/specs/2026-09-16-github-discoverability-design.md`
- Target: `origin/feat/github-discoverability`

---

## Plan Overview

Deliver Subproject S4 through disciplined atomic task commits:

| Task | Component | Files | Verification |
|---|---|---|---|
| 1 | Spec & Plan | `docs/superpowers/specs/...`, `docs/superpowers/plans/...` | Design inspection |
| 2 | README Rewrite | `README.md` | Content & link check, code snippet validation |
| 3 | Upstream Attribution & Acknowledgments | `README.md`, `docs/index.md` | Attribution verification against Adoption Board |
| 4 | GitHub Metadata & Topics | `.github/topics.txt`, `docs/guides/` | Verification of topic tags (≥ 8) |
| 5 | Full Quality Gates & Sweep | Whole repo | `mkdocs build --strict`, `ruff`, `mypy`, `pytest` (100% pass) |

---

## Detailed Task Breakdown

### Task 1: Spec & Implementation Plan
- Document S4 design and plan according to `NEXT-UPDATE.md`.
- Commit: `docs(plan): S4 github discoverability and readme rewrite plan`.

### Task 2: High-Impact README Rewrite
- Refactor `README.md` with:
  1. Centered ASCII/SVG hero header with badges and one-paragraph elevator pitch.
  2. Four key pillars: Subscription OAuth, Combo Failover, Replayable Event Ledger, Hardened Sandbox.
  3. Visual terminal walkthrough of `avo login` and `avo chat` with combo fallback.
  4. Quickstart: install, login, and 1-turn python agent with fake provider or live provider.
  5. Detailed feature comparison matrix against 9router, LiteLLM, OpenRouter, and Claude Code.
  6. Command-line cheat sheet (`avo combo`, `avo login`, `avo doctor`, REPL slash commands).
- Commit: `docs(readme): rewrite readme with subscription oauth, combo routing, and comparison matrix`.

### Task 3: Upstream Lineage & Acknowledgments
- Add formal Acknowledgments section in `README.md` and `docs/index.md` honoring:
  - `decolua/9router` for OAuth PKCE token exchange flows and lifecycle constants.
  - `clash-ru/CLIProxyAPI` for subscription flow validation.
  - `BerriAI/liteLLM` for matrix and fallback reference.
  - Anthropic, OpenAI, Google, and Ollama.
- Commit: `docs: add formal acknowledgments and upstream lineage attribution`.

### Task 4: GitHub Topics & Discoverability Metadata
- Document and record repository topics in `.github/topics.txt` (targeting 12 curated topics).
- Commit: `chore(github): record curated repository discoverability topics`.

### Task 5: Quality Gates & Atomic Push
- Run `mkdocs build --strict`.
- Run `ruff check . && ruff format --check . && mypy src/avo && pytest`.
- Push via `~/.local/bin/git-push-notify origin feat/github-discoverability`.
