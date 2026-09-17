# avo parity audit — Claude Code + Codex CLI

Reference snapshot of what avo 0.1 already covers, what it ships today,
and the gap vs Claude Code (CC) and Codex CLI. Status reflects the
`main` branch as of the 0.1.1 release.

## Feature matrix

| Capability                         | avo          | CC           | Codex        | Notes |
| ---------------------------------- | ------------ | ------------ | ------------ | ----- |
| Interactive REPL                   | ✅ `avo chat` | ✅ `claude`   | ✅ `codex`    | |
| Slash commands                     | ✅           | ✅           | ✅           | avo: `/sessions /resume /session /new /inspect /skills /skill /provider /help` |
| Provider-agnostic model layer      | ✅ 4 adapters | ✅           | ✅           | ollama, anthropic, openai, minimax |
| Strict state machine + event log   | ✅           | implicit     | ✅           | SQLite-backed |
| Resumable runs (SQLite)            | ✅           | ✅           | ✅           | |
| Conversation threading             | ✅ `/sessions` | implicit    | partial      | avo persists user+assistant turns per session |
| Skills system (markdown)           | ✅ `/skills /skill` | ✅ | ✅ | loaded from `<workspace>/.avo/skills/` |
| Plugin marketplace / install       | ✅ entry points | ✅ plugins marketplace | ✅ | avo has discover() but no `install/list/remove` yet — gap |
| MCP servers (stdio JSON-RPC)       | ✅           | ✅           | ✅           | avo ships 4 built-in: filesystem, sqlite, git, http_fetch |
| MCP registry (`avo mcp add/list/remove`) | ✅        | ✅           | ✅           | JSON-file backed, same shape as `.mcp.json` |
| Approval/permission modes          | ✅ callback  | ✅           | ✅           | `AVO_TOOLS_REQUIRE_APPROVAL` |
| Sandbox shell (Docker)             | ✅           | ✅           | ✅           | `app_tools.run_shell_tool` |
| Workspace-scoped file tools        | ✅           | ✅           | ✅           | `read_file/write_file/edit_file/grep/glob` |
| Web fetch                          | ✅           | ✅           | ✅           | |
| Web search                         | ✅ DuckDuckGo | ✅          | partial      | |
| Plan mode                          | ✅           | ✅           | ✅           | `AVO_PERMISSION_MODE=plan` |
| Sub-agents                         | ✅ `task`    | ✅ Task tool | partial      | |
| Auto-compact context               | ✅ `compact.py` | ✅ `/compact` | ✅       | |
| Hooks (Pre/Post/Stop)              | ✅ `hooks.py` | ✅          | ✅           | avo: PreToolUse/PostToolUse/Stop callbacks |
| Status line                        | 🟡           | ✅           | ✅           | avo prints banner at REPL start; live status line is a gap |
| Init command                       | ✅ `avo init` | ✅ `/init` | ✅           | scaffolds `.avo/skills/` + `AGENTS.md` |
| Diff display                       | 🟡           | ✅           | ✅           | inline render path exists; UI polish is a gap |
| Image input                        | ✅           | ✅           | ✅           | typed `ContentBlock` (text/image); per-provider translators |
| Cost tracking                      | ✅           | ✅           | ✅           | `ledger.py` + `usage.py` |
| Background tasks                   | ✅           | ✅           | partial      | trailing `&` + `/jobs /job /cancel` + `[jobs: N]` indicator |
| One-line install                   | ✅ `install.sh` / `install.ps1` | ✅ brew/curl | ✅ npm | user-global via `uv tool` |
| PyPI distribution                  | ✅ `avo 0.1.1` | n/a         | n/a          | CC is closed source, Codex is npm |

Legend: ✅ shipped · 🟡 partial · ❌ not yet

## What this round of work ships

1. **`avo plugin install/list/remove`** — third-party plugin installer
   that pulls a git URL (or local path) into `~/.avo/plugins/` and
   re-discovers Python entry points on next `avo chat`.
2. **`avo mcp add/list/remove`** — JSON-file backed registry of MCP
   server commands; the chat REPL auto-attaches every registered
   server, similar to CC's `.mcp.json` and Codex's `~/.codex/mcp.json`.
3. **`avo skill install/list/show/remove`** — install skill markdown
   into `~/.avo/skills/`. Workspace-local skills take precedence
   (matching CC behaviour).
4. **Plugin scaffold (`avo plugin init <name>`)** — generates a
   `avo-plugin.toml` + entry-point skeleton that publishes into the
   `avo.tools` group.
5. **`examples/mcp_demo.py`** and **`examples/plugins_demo.py`** —
   runnable demos that exercise the new registries.

## What stays on the roadmap

- Live status line at the prompt (provider/model/turn/elapsed).
- Diff-render polish for file edits.

## Design choices worth flagging

- **Single PyPI artifact.** Unlike CC (binary download) or Codex (npm),
  avo is one Python package — `avo[all]` through the OS-native installer. Extras cover
  `dev`, `providers`, `sandbox`, `live-benchmark`, `mcp`, and the runtime `all` bundle. No
  companion binaries, no separate CLI distribution.
- **Workspace is the security boundary.** File tools refuse any path
  outside the workspace root; the sandbox wraps shell execution in an
  ephemeral Docker container. CC's sandbox model uses Seatbelt (mac)
  / bubblewrap (linux) — same goal, different mechanism.
- **Plugins are normal Python packages.** The `avo.tools`, `avo.providers`,
  and `avo.notifiers` entry-point groups follow Python's standard
  packaging — no custom manifest, no `~/.avo/plugins/` shadow tree
  unless `avo plugin install` chose to copy. Both paths work; the
  entry-point path is canonical.
- **Skills are plain markdown with a YAML frontmatter block.** Same
  format as CC's `.claude/skills/*/SKILL.md`. No DSL.
