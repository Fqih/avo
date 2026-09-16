# S2 — Combo Routing Implementation Plan

- Status: ready for execution
- Branch: `feat/combo-routing`
- Spec: `docs/superpowers/specs/2026-09-16-combo-routing-design.md`
- Target: `origin/feat/combo-routing`

---

## Plan Overview

Deliver Subproject S2 via strict Test-Driven Development (TDD) and atomic task commits:

| Task | Component | Files | Verification |
|------|-----------|-------|--------------|
| 1 | Event Invariant & EventType | `src/avo/events.py`, `tests/test_event_invariants.py` | Unit tests |
| 2 | Combo Models & Store | `src/avo/combo/models.py`, `src/avo/combo/store.py`, `tests/test_combo_store.py` | Schema & persistence tests |
| 3 | Quota & 429 Error Classifier | `src/avo/combo/detector.py`, `tests/test_combo_detector.py` | Classification matrix tests |
| 4 | ComboRouterProvider | `src/avo/combo/provider.py`, `tests/test_combo_provider.py` | Failover & streaming tests |
| 5 | Config & Provider Factory Wiring | `src/avo/config.py`, `tests/test_combo_config.py` | Factory resolution tests |
| 6 | CLI Commands (`avo combo`) | `src/avo/cli.py`, `tests/test_combo_cli.py` | CLI parsing & execution tests |
| 7 | REPL Integration & Notices | `src/avo/chat_commands.py`, `src/avo/chat_render.py`, `src/avo/chat.py` | REPL turn & notice tests |
| 8 | Docs & Reference | `docs/guides/combo-routing.md`, `mkdocs.yml` | `mkdocs build --strict` |
| 9 | End-to-End Suite & Push | Whole repo | `ruff`, `mypy`, `pytest` (100% pass) |

---

## Detailed Task Breakdown

### Task 1: Event Type `ROUTE_FAILOVER` & Event Invariants
- Add `EventType.ROUTE_FAILOVER = "route_failover"` to `src/avo/events.py`.
- Ensure `validate_event_append` permits `ROUTE_FAILOVER` during active runs before terminal events.
- Test in `tests/test_event_invariants.py`.

### Task 2: Combo Profile Models & JSON Store
- Create `src/avo/combo/models.py`: `ComboTier`, `ComboProfile`, `ComboCatalog`.
- Create `src/avo/combo/store.py`: `load_combos()`, `save_combo()`, `delete_combo()`, `get_combo()`, with built-in presets (`default`, `coder`, `budget`).
- Test in `tests/test_combo_store.py`.

### Task 3: Quota & 429 Classifier
- Create `src/avo/combo/detector.py`: `is_quota_or_rate_limit_error(exc) -> bool` and `classify_failover_reason(exc) -> str`.
- Test against error signatures from Anthropic, OpenAI/Codex, Gemini, Groq, OpenRouter, and Ollama in `tests/test_combo_detector.py`.

### Task 4: ComboRouterProvider
- Create `src/avo/combo/provider.py`: `ComboRouterProvider(profile, *, provider_factory=None, event_callback=None)` inheriting from `StreamingModelProvider`.
- Implements `generate` and `stream` with:
  - Highest-priority healthy tier selection.
  - Transparent failover on 429 / quota errors.
  - Event notification callback invocation.
  - Fail-closed behavior on syntax/auth errors.
- Test in `tests/test_combo_provider.py`.

### Task 5: Config & Factory Integration
- Modify `src/avo/config.py` to support `AVO_PROVIDER=combo` and `AVO_COMBO=<name>`.
- Wire `build_provider_from_env` to instantiate `ComboRouterProvider` with resolved underlying tier providers.
- Test in `tests/test_combo_config.py`.

### Task 6: CLI Subcommand `avo combo`
- Implement `avo combo list`, `avo combo new <name> --tier ...`, `avo combo show <name>`, `avo combo rm <name>`.
- Test in `tests/test_combo_cli.py`.

### Task 7: Chat REPL Integration
- Add `/combo` slash command to inspect/swap active combo in `src/avo/chat_commands.py`.
- Add failover visual notices to `src/avo/chat_turn.py` and `src/avo/chat_render.py`.
- Test in `tests/test_chat_repl.py`.

### Task 8: Documentation & Guides
- Create user guide `docs/guides/combo-routing.md`.
- Update `mkdocs.yml` nav and `CHANGELOG.md`.
- Verify `mkdocs build --strict`.

### Task 9: Full Quality Gates & Atomic Push
- Run `ruff check .`, `ruff format --check .`, `mypy src/avo`, and `pytest`.
- Push via `~/.local/bin/git-push-notify origin feat/combo-routing`.
