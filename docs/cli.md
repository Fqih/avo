# CLI

Avo ships a `avo` console script with the following subcommands.

## Installation and setup

Install the CLI for the current user with the OS-native installer:

```bash
# Linux, macOS, or Git Bash
curl -fsSL https://avo.faqihhakim.tech/install.sh | bash

# Native Windows PowerShell
irm https://avo.faqihhakim.tech/install.ps1 | iex
```

The installer is user-global and does not need root or Administrator access.
Run `install.sh --dry-run` or `install.ps1 -DryRun` to inspect its actions.
Afterward, run `avo setup` in the workspace that should receive its provider
and permission configuration.

| Command                | Purpose                                                  |
| ---------------------- | -------------------------------------------------------- |
| `avo --version`        | Print package version                                     |
| `avo doctor`           | Show resolved provider, model, endpoint, env, extras      |
| `avo chat`             | Interactive REPL with background tasks, image input      |
| `avo runs list`        | List runs persisted in the local SQLite event log        |
| `avo runs inspect`     | Show full event trace for a run                          |
| `avo runs resume`      | Resume a run from its last durable state                 |
| `avo runs diff`        | Compare two persisted runs (events + tokens + steps)     |
| `avo bench`            | Run a deterministic cross-provider benchmark             |
| `avo cost`             | Aggregate the persistent `TokenLedger` (table or JSON)    |
| `avo sandbox run`      | Run a one-off shell command in an ephemeral container    |
| `avo plugin init`      | Scaffold a working plugin directory                      |
| `avo mcp add/list/...` | Manage Model Context Protocol servers                     |
| `avo init`             | Scaffold `.avo/skills/repo-overview/SKILL.md` + `AGENTS.md` |

Run `avo <subcommand> --help` for full flag details. Every command exits
non-zero on failure so the CLI composes cleanly in CI and Make targets.
