# CLI

Avo ships a `avo` console script. Running `avo` with no subcommand starts the
chat REPL; `avo --help` is the canonical command map.

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
| `avo` / `avo chat`     | New interactive REPL session with background tasks       |
| `avo resume [SESSION]` | Resume the latest or a specific chat session             |
| `avo login PROVIDER`   | Official vendor browser login or API-key storage         |
| `avo combo auth NAME`  | Login to missing cloud vendors used by a combo            |
| `avo models ollama list` | List installed Ollama Local models                     |
| `avo models ollama recommend` | Recommend models for local hardware              |
| `avo models ollama pull MODEL` | Confirm and download a local model                |
| `avo models ollama cloud` | Show remote Ollama Cloud guidance                    |
| `avo models ollama cloud list` | List models exposed by the Cloud account       |
| `avo models ollama cloud health` | Check Cloud availability                       |
| `avo models ollama cloud usage` | Show optional Cloud quota metadata             |
| `avo saver list` | List built-in token-saver presets. |
| `avo saver show NAME` | Inspect a preset's style guide and compression pipeline. |
| `avo saver use NAME` / `avo saver off` | Enable or disable the persisted saver choice. |
| `avo runs list`        | List runs persisted in the local SQLite event log        |
| `avo runs inspect`     | Show full event trace for a run                          |
| `avo runs resume`      | Resume a run from its last durable state                 |
| `avo runs diff`        | Compare two persisted runs (events + tokens + steps)     |
| `avo runs replay`      | Verify recorded model/tool decisions without inference  |
| `avo bench`            | Run a deterministic cross-provider benchmark             |
| `avo cost`             | Aggregate the persistent `TokenLedger` (table or JSON)    |
| `avo sandbox run`      | Run a one-off shell command in an ephemeral container    |
| `avo plugin init`      | Scaffold a working plugin directory                      |
| `avo mcp add/list/...` | Manage Model Context Protocol servers                     |
| `avo init`             | Scaffold `.avo/skills/repo-overview/SKILL.md` + `AGENTS.md` |

Run `avo <subcommand> --help` for full flag details. Every command exits
non-zero on failure so the CLI composes cleanly in CI and Make targets.

Inside the REPL, `/resume` opens an interactive session picker. Use the
arrow keys and Enter to choose a session, type to filter the list, or press
Esc/q to cancel. The selected session transcript is shown before the next
message is sent.

### Provider reuse and model picker

After the first setup or a successful `avo login <provider>`, Avo remembers
the last provider and model in `~/.avo/config.json`. OAuth/API credentials are
never copied there; they remain in `~/.config/avo/auth.json`. Therefore a new
`avo` process can reuse the last route without reopening the provider wizard.
One-off environment variables such as `AVO_PROVIDER` and `AVO_MODEL` still
override the saved route.

The setup wizard displays a numbered model picker instead of requiring a
model name. In an interactive chat, `/model` first fetches the provider's
live catalog (with a bounded cache and clearly labelled static fallback), so
new vendor models do not require an Avo release. The selected model remains
subject to the vendor account, plan, quota, and region. A custom model name
can still be entered for compatible or newly released endpoints.

Use `/model` inside a chat to inspect the current provider's catalog and
`/model NAME` to switch models for the next turn.

### Security posture and diagnostics

`avo doctor` reports the resolved security posture and the source of every
setting without printing tokens. Resolution is CLI override, environment,
workspace config, user config, then defaults. The default posture requires a
sandbox, disables sandbox networking, and keeps plugin editable installs and
activation off.

Plugin installation is dependency-isolated: `avo plugin install` creates a
private virtualenv under `~/.avo/plugins/.venvs/<name>/` and records a source
SHA-256 digest. It never installs into Avo's active Python environment. Legacy
plugin records without an environment or digest are shown as legacy and must
be reinstalled before activation. Editable installs require the explicit
development override `AVO_PLUGIN_EDITABLE=1`.

These controls are intentionally separate:

- `avo resume` continues a prior chat session; `avo runs replay` verifies a
  recorded run without calling a provider or executing tools.
- Sandbox settings constrain execution; host execution is an explicit local
  development exception, not a sandbox feature.
- OAuth/API-key login authenticates a vendor; permission mode controls which
  tools may run. Permission protection is not encryption.
- Prefer `AVO_CREDENTIAL_BACKEND=keyring` (or `auto`) when an OS keyring is
  available. For headless hosts, install `avo[security]`, set
  `AVO_CREDENTIAL_BACKEND=encrypted-file`, and provide a Fernet key through
  `AVO_CREDENTIAL_ENCRYPTION_KEY`. The restricted file backend remains the
  dependency-free compatibility fallback.

See [`docs/migrations/0.7.x-to-milestone-three.md`](migrations/0.7.x-to-milestone-three.md)
for rollback-safe upgrade notes.

### Agent delegation and replay

`@coder`, `@explore`, and `@reviewer` are built-in workspace agents. A prompt
starting with a registered mention delegates to that isolated profile:

```text
@explore locate the OAuth entry points
@reviewer inspect the error paths | @explore find tests for them
```

Use `/agents` to open the searchable picker, `/agents list` for a
non-interactive list, and `/agent add NAME DESCRIPTION` to create
`.avo/agents/NAME.md`. Delegated children use fresh event stores and inherit
the parent workspace and approval boundary; a failed sibling does not cancel
the others.

For a completed run, `avo runs replay RUN_ID` validates the persisted event
ledger and prints a stable fingerprint. `--json` is intended for CI. Replay
never sends a provider request, invokes a tool, mutates the original run, or
resumes an incomplete run. The REPL equivalent is `/replay RUN_ID`.

### Dynamic catalogs and attachments

Interactive `/model` first fetches the provider's live catalog, then uses a
bounded cache when the provider is unavailable, and only then shows the
maintained static catalog. The picker labels the source so a stale list is
never mistaken for current account access.

Paste or drag a workspace path into the prompt, or use `@path` / `file://path`.
Use `@clipboard` for an image in the Wayland/X11 clipboard. Avo validates
workspace containment, symlinks, file type, and size before sending content to
the provider.
