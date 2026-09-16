# S4 — GitHub Discoverability & README Design Spec

- Status: approved candidate
- Author: Fqih
- Date: 2026-09-16
- Target: `README.md`, repository metadata, documentation
- Subproject: S4 of `NEXT-UPDATE.md`

---

## 1. Executive Summary & Core Hook

Avo has evolved through v0.2 and v0.3 from a foundational agent loop into a complete, resilient agent runtime featuring **Subscription OAuth** (S1) and **Multi-Tier Combo Routing** (S2).

The goal of S4 is to make Avo immediately legible, compelling, and discoverable to developers on GitHub.

### One-Paragraph Hook
> **Avo** is an observable, resilient AI agent runtime that unites your Claude Pro, ChatGPT Plus, and Gemini subscriptions with multi-tier combo failover down to local Ollama. When API quotas or rate limits hit mid-flight, Avo switches models seamlessly without losing conversational state or task execution. Built on an event-sourced SQLite ledger with deterministic replay, safe workspace tools, and zero extra core dependencies (Pydantic only).

---

## 2. GitHub Discoverability & Repository Topics

To ensure high discoverability on GitHub search and topic exploration, the repository will target 12 curated topics (minimum requirement: ≥ 8):

1. `ai-agents`
2. `llm`
3. `multi-model`
4. `resilience`
5. `oauth`
6. `ollama`
7. `failover`
8. `python`
9. `claude-code`
10. `codex`
11. `developer-tools`
12. `agentic-ai`

---

## 3. README Rewrite Blueprint

The `README.md` will be refactored into a high-impact, modern open-source presentation:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Hero Banner, Badges, & Elevator Pitch                    │
├─────────────────────────────────────────────────────────────┤
│ 2. What Makes Avo Different? (4 Core Pillars)               │
├─────────────────────────────────────────────────────────────┤
│ 3. Terminal Demo: Subscription OAuth & Combo Failover       │
├─────────────────────────────────────────────────────────────┤
│ 4. Quickstart (Under 2 Minutes)                             │
├─────────────────────────────────────────────────────────────┤
│ 5. Architectural Comparison Matrix                          │
│    (Avo vs 9router vs LiteLLM vs OpenRouter vs Claude Code) │
├─────────────────────────────────────────────────────────────┤
│ 6. Tooling & Sandboxing (Workspace Safety & Ephemeral Docker)│
├─────────────────────────────────────────────────────────────┤
│ 7. CLI & REPL Command Matrix                                │
├─────────────────────────────────────────────────────────────┤
│ 8. Acknowledgments & Upstream Lineage                       │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Comparison Matrix

| Feature | **Avo** | **9router** | **LiteLLM** | **OpenRouter** | **Claude Code** |
|---|:---:|:---:|:---:|:---:|:---:|
| **In-Process Agent Runtime** | ✅ Yes | ❌ (Proxy only) | ❌ (Gateway only)| ❌ (Hosted API) | ✅ Yes |
| **Subscription OAuth (Claude/ChatGPT/Gemini)**| ✅ Yes | ✅ Yes | ❌ No | ❌ No | ⚠️ (Claude only) |
| **Multi-Tier Failover (Sub → Cheap → Local)**| ✅ Yes | ⚠️ (Basic route)| ✅ Yes | ⚠️ (Model fallbacks)| ❌ No |
| **Zero-Cost Local Floor (Ollama)** | ✅ Yes | ⚠️ (Via endpoint)| ✅ Yes | ❌ No | ❌ No |
| **Event-Sourced Ledger & Replay (SQLite)** | ✅ Built-in | ❌ No | ❌ No | ❌ No | ❌ No |
| **Workspace Safety & Ephemeral Docker Sandbox**| ✅ Yes | ❌ No | ❌ No | ❌ No | ⚠️ (Local bash) |
| **Core Dependency Footprint** | **Pydantic only** | Go binary | Heavy Python deps | N/A (Hosted) | Node.js runtime |

### 3.2 Terminal Visual Demo

Demonstrating both S1 (Subscription OAuth) and S2 (Combo Routing with mid-turn 429 failover):

```text
$ avo chat
       ▄██▄           Avo CLI 0.1.0
     ▄██████▄         Fqih (Subscription)
    ███    ███        provider: combo · model: coder [subscription -> cheap -> free]
   ███  ▄▄  ███       workspace: ~/Project/Loopward
   ███  ▀▀  ███       session: c8f921ab04e1
  ──────────────────────────────────────────────────────

> Analyze the authentication flow in src/avo/auth.py and write unit tests

⠋ Thinking...
⤾ Fallback: switched from 'subscription' (claude) to 'cheap' (openrouter) [rate_limited_429]

I've analyzed `src/avo/auth.py`. Here is the architecture and test suite...
```

### 3.3 Acknowledgments & Attribution Block

In accordance with our adoption candidates policy in `NEXT-UPDATE.md`:
- **[decolua/9router](https://github.com/decolua/9router)** (MIT): Upstream reference and port for OAuth PKCE token exchange flows, endpoint definitions, and token-refresh lifecycle patterns.
- **[clash-ru/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)** (MIT): Upstream Go lineage for subscription flow verification.
- **[BerriAI/liteLLM](https://github.com/BerriAI/litellm)** (MIT): Reference for provider fallback matrices and error categorization.
- **[Textualize/rich](https://github.com/Textualize/rich)**: Inspiration for CLI formatting and terminal rendering.
- **Anthropic, OpenAI, Google, and Ollama**: For the underlying model platforms and local inference runtimes.

---

## 4. Verification & Quality Gates

- `README.md` passes markdown linter and all internal file links are valid.
- `mkdocs build --strict` remains 100% green without broken navigation or anchors.
- All code samples in the README Quickstart are executable and verified against unit tests.
