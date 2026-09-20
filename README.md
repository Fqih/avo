<div align="center">

<img src="logo.png" width="130" height="130" alt="Avo Logo" style="border-radius: 24px; filter: drop-shadow(0 4px 20px rgba(16, 185, 129, 0.25));">

# Avo 🥑

**The Autonomous AI Coding Agent That Never Quits.**  
*Multi-model failover (Claude → OpenAI → Ollama), Git worktree isolation, SQLite event-sourced replay, and zero-Python standalone CLI.*

[![Release](https://img.shields.io/github/v/release/Fqih/avo?color=10b981&label=version)](https://github.com/Fqih/avo/releases)
[![PyPI](https://img.shields.io/pypi/v/avo?color=10b981&label=pypi)](https://pypi.org/project/avo/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-slate.svg)](#installation)
[![Standalone](https://img.shields.io/badge/executable-zero--python%20standalone-emerald.svg)](#installation)
[![Docs](https://img.shields.io/badge/docs-avo.faqihhakim.tech-teal.svg)](https://avo.faqihhakim.tech/)

[**Quickstart**](#quickstart) • [**Installation**](#installation) • [**Why Avo?**](#why-avo-vs-claude-code--aider) • [**Architecture**](#how-it-works) • [**Documentation**](https://avo.faqihhakim.tech/)

</div>

---

## ⚡ Quickstart (Zero-Python, 10 Seconds)

Install Avo directly on your system. **No Python, `uv`, `node`, or package manager required:**

```bash
# Linux, macOS, or Git Bash
curl -fsSL https://avo.faqihhakim.tech/install.sh | bash

# Native Windows PowerShell
irm https://avo.faqihhakim.tech/install.ps1 | iex
```

Once installed, launch Avo in any git repository:

```bash
# 1. Connect your models (Claude, ChatGPT, Gemini, or local Ollama)
avo setup

# 2. Start autonomous coding in your workspace
avo
```

*(Prefer Python package? Run `uv tool install avo[all]` or `pip install avo`)*

---

## 💥 Why Avo? (The Problem We Solve)

Current AI coding tools (Claude Code, Aider, Cursor) crash or stall the moment:
1. **You hit an HTTP 429 quota exhaustion or cloud outage** — your session aborts, context is lost, and you wait hours for quota to reset.
2. **The agent modifies files recklessly** — a bad tool call or broken speculative rollback pollutes your git working tree with dirty diffs.
3. **Complex multi-step tasks get stuck in infinite loops** — burning thousands of tokens repeating the exact same failed tool command.

**Avo solves this with runtime infrastructure:**

- 🔀 **Zero-Drop Multi-Tier Failover (Combo Routing)**: Seamlessly failover across **Claude 3.7 → OpenAI GPT-4o → Gemini → Local Ollama (Qwen 2.5 Coder)** in the exact same turn with zero context loss.
- 🌿 **Git Worktree Isolation**: Agents work on disposable git worktrees (`.avo/worktrees/<run_id>`) on isolated branches. Speculative edits and tests never touch your uncommitted work until approved and verified.
- 🛡️ **Dual-Layer Sandboxing**: Runs dangerous commands in rootless Linux Bubblewrap (`bwrap`) with unshared PID/network namespaces or ephemeral Docker containers (`network_mode="none"`).
- 📜 **Durable SQLite Event Sourcing**: Complete event log (`AgentEvent`) and crash-safe checkpoints. Kill the process anytime, and `avo resume` picks up exactly where it left off.
- 🔔 **Durable Webhook Approval**: Pending approvals persist in SQLite. Approve dangerous tools via terminal, Web UI, or REST webhook (`POST /api/approvals/{id}/decision`) even after restarting the daemon.
- 💸 **Token Savers Built-In**: Deterministic tool compression and Caveman terse presets slash token expenditure by **26%+** on tool-heavy sessions.

---

## 📊 Comparison: Avo vs Other Tools

| Feature | Avo 🥑 | Claude Code | Aider | LangGraph |
|:---|:---:|:---:|:---:|:---:|
| **Zero-Python Standalone Executable** | **✅ Yes (Single binary)** | ❌ (Needs Node.js) | ❌ (Needs Python/pip) | ❌ (Needs Python) |
| **Multi-Model Dynamic Failover** | **✅ Claude → GPT → Ollama** | ❌ Anthropic only | ❌ Single model per run | ⚠️ Manual code |
| **Git Worktree Workspace Isolation** | **✅ Built-in (`.avo/worktrees`)** | ⚠️ Hook-based | ❌ Direct working tree | ❌ None |
| **Crash-Safe Step Resume** | **✅ SQLite Checkpoints** | ⚠️ Session log | ❌ Terminal scrollback | ✅ Checkpointer |
| **Rootless Linux Sandbox (`bwrap`)** | **✅ Built-in namespaces** | ❌ Host execution | ❌ Host execution | ❌ None |
| **Durable Webhook Approvals** | **✅ SQLite Table + REST API** | ❌ Terminal only | ❌ CLI prompt only | ⚠️ In-memory |
| **Offline Local Compute Floor** | **✅ Auto-detect ROCm/CUDA** | ❌ Cloud only | ⚠️ Manual Ollama | ⚠️ Custom setup |

---

## 🖥️ Interactive CLI & Web Cockpit

Avo gives you full control over how you work:

```
$ avo
🥑 Avo v0.7.4 (mode: code, model: combo/tier1-claude -> tier2-ollama)
Type /help for slash commands, or describe your task:

avo> Implement git worktree isolation for our test suite and run tests
→ [PLAN] Creating isolated branch avo/task-a1b2 in .avo/worktrees/task-a1b2
→ [TOOL] read_file("tests/test_runner.py")
→ [TIER 1] Anthropic Claude 3.7 Sonnet (2,410 tokens)
→ [TOOL] write_file("src/workspace.py")
→ [SANDBOX] bwrap --ro-bind /usr /usr --unshare-net pytest tests/
→ [CHECKPOINT] Step 4 saved to .avo/runs.db (state: GREEN)
✓ Completed in 4 steps. Merged cleanly to main branch.
```

Prefer a visual dashboard? Launch the **Avo Web Cockpit**:
```bash
avo web --port 43111
```
View live execution DAGs, real-time token spend ledgers, circuit breaker health, and approve tool authorizations from your browser.

---

## 🛠️ Python SDK (Embedding Avo in Your Apps)

Avo is both a high-productivity CLI and a modular, dependency-light Python runtime:

```python
import asyncio
from avo import AgentRuntime, LoopPolicy
from avo.app_tools import GitWorktreeManager, read_file_tool, write_file_tool
from avo.config import build_provider_from_env

async def main():
    # 1. Isolate agent workspace in a dedicated git worktree
    worktree = GitWorktreeManager(".").create_worktree("audit-run-01")

    # 2. Initialize runtime with hard step, token, and circuit breaker bounds
    runtime = AgentRuntime(
        provider=build_provider_from_env(),
        policy=LoopPolicy(max_steps=20, token_budget=60_000),
        tools=[read_file_tool, write_file_tool],
        database_path=".avo/runs.db",
    )

    # 3. Execute with deterministic replay and SQLite event sourcing
    result = await runtime.run("Refactor database connection pooling and verify tests.")
    print(f"Outcome: {result.state} (StopReason: {result.stop_reason})")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 📦 Installation Options

### 1. Zero-Python Standalone Binary (Recommended)
Download single-file executables with zero host dependencies:
```bash
# Linux / macOS / Git Bash
curl -fsSL https://avo.faqihhakim.tech/install.sh | bash

# Windows PowerShell
irm https://avo.faqihhakim.tech/install.ps1 | iex
```

### 2. User-Global via `uv`
```bash
uv tool install avo[all]
```

### 3. Standard `pip`
```bash
pip install avo
```

---

## 🗺️ Roadmap

<<<<<<< HEAD
### 📊 1. Measurable ROI & Financial Governance
- **Deterministic Token Reduction:** Automatically minifies tool JSON payloads, deduplicates redundant outputs, and elides verbose lines, reducing token consumption by up to **26%+** on long tool-heavy sessions (see [benchmark results](benchmark/savers/RESULTS.md)).
- **Semantic Token Deduction:** Automatically eliminates redundant system instructions and file context, slashing recurring API overhead by up to **48%**.
- **Tiered Spending Caps:** Organizations leverage free or pre-paid enterprise quota first, spill over to fractional-cent pay-as-you-go providers second, and maintain a zero-cost local compute floor as the final safety net.
- **Granular Cost Transparency:** View exact per-turn and aggregate expenditures categorized by provider tier directly within the operational ledger.

### 🛡️ 2. Business Continuity & High Availability
- **Mid-Turn State Preservation:** When an upstream vendor encounters service degradation, Avo migrates pending turns to alternate providers without requiring developers to restart their session.
- **On-Premise Hardware Autonomy:** Automated hardware discovery configures local GPU accelerators (AMD ROCm, NVIDIA CUDA, Apple Metal) so core coding loops remain operable even during complete internet connectivity loss.

### 🔒 3. Enterprise Security & Audit Compliance
- **POSIX Boundary Containment:** Strict kernel-level path resolution blocks directory traversal, null-byte poisoning, and symlink replacement attacks.
- **Containerized Isolation:** Code execution operates within disposable, ephemeral Docker sandboxes with disabled external networking by default.
- **Immutable SQLite Ledger:** Every prompt, decision boundary, tool mutation, and model switch is stored chronologically for compliance review and security auditing.

### ⚡ 4. Operational Observability (Avo Web UI)
- **Real-Time Operational Cockpit:** Visual telemetry dashboard displaying live trace timelines, model latency meters, circuit breaker triggers, and hardware resource saturation.
- **Extensible Enterprise Plugins:** Seamless integration with company-internal ticketing systems, GitHub pull request automation, and incident alert channels via standard extension points.
=======
- [x] **v0.7.0**: Multi-Tier Combo Router & OAuth Subscription Auth (Claude, ChatGPT, Gemini).
- [x] **v0.7.2**: Autonomous Loop, AST Code Intelligence, Shared Blackboard Memory.
- [x] **v0.7.3**: Rootless Bubblewrap Sandbox (`bwrap`) & Web Cockpit Full-Duplex.
- [x] **v0.7.4**: Zero-Python Standalone Executable, Git Worktree Isolation & Durable Webhook Approval.
- [ ] **v0.8.0**: Distributed PostgreSQL EventStore & Celery/Redis Remote Worker Mesh.
- [ ] **v0.9.0**: Native Headless Browser Sandbox (Playwright verification).
>>>>>>> 9faa1d4 (docs(readme): rewrite readme with dev-first focus, standalone install, and comparison matrix)

---

## 🤝 Contributing & Community

We welcome issues, PRs, and architectural discussions!
- **Issues & Discussions:** [GitHub Issues](https://github.com/Fqih/avo/issues)
- **Documentation:** [avo.faqihhakim.tech](https://avo.faqihhakim.tech/)
- **License:** [MIT License](LICENSE)

---

<div align="center">
<b>Built with reliability-first engineering by Fqih.</b>
<br>
<i>If Avo saves your coding session from a 429 quota crash, give us a ⭐ on GitHub!</i>
</div>
