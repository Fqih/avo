"""`avo init` — scaffold workspace-local skills + AGENTS.md.

Mirrors CC's ``/init`` and Codex's project bootstrap. The command scans
the current working directory for top-level markers (Python project,
git repo, ``package.json``, ``Cargo.toml``, etc.) and writes two files
when missing:

- ``.avo/skills/repo-overview/SKILL.md`` — a stub skill that future
  turns can expand as they learn the codebase.
- ``AGENTS.md`` — a short index that points operators (and agents) at
  the relevant avo surfaces: slash commands, env vars, the run/state
  guarantees, and the workspace skill location.

Existing files are never overwritten; the command prints what it
created and what it left alone.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from avo.exceptions import AvoError


class InitCliError(AvoError):
    """User-facing failure in `avo init`."""


_SKILL_BODY = """---
name: repo-overview
description: High-level context about this repository. Expand as turns explore it.
---

# Repository overview

This skill is the seed for project-local memory. The chat REPL loads
it into the preamble on every session start, so anything you write
here becomes standing context for the agent.

Recommended sections to fill in:

- What this project does and who uses it.
- Key directories and their roles.
- The build / test commands the operator runs day-to-day.
- Boundaries the agent must respect (data, infra, deploy).
"""

_AGENTS_BODY = """# AGENTS

Short index for humans and agents working in this repo with `avo`.

## Quick reference

- REPL: `avo chat`
- Auth / OAuth: `avo login`
- Multi-provider router: set `AVO_PROVIDER=router` (chains Ollama, OpenRouter, etc.)
- Slash commands: type `/help` inside the REPL.
- Workspace skills live in `.avo/skills/<name>/SKILL.md`.
- Persisted runs: `avo runs list` / `avo runs inspect RUN_ID`.
- MCP servers: `avo mcp add NAME -- echo hi` (no `--` separator).

## Runtime guarantees

The `AgentRuntime` is bounded, observable, resumable, and
provider-agnostic. Every run emits an append-only event log; the
SQLite file holds both runs and chat sessions.

## Configuration

All knobs are `AVO_*` env vars. `.env.example` in the avo repo is the
canonical list. The chat REPL's first-run wizard persists them to
`~/.zshrc` / `~/.bashrc` on request.

## Adding skills

Drop a markdown file at `.avo/skills/<name>/SKILL.md` with a YAML
frontmatter block (`name`, `description`). The REPL picks it up on
the next turn.
"""


_MARKER_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "setup.py",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Makefile",
    ".git",
)


def _repo_kind(cwd: Path) -> str:
    """Return a short label describing the kind of repo at ``cwd``."""

    if (cwd / "pyproject.toml").is_file():
        return "python"
    if (cwd / "package.json").is_file():
        return "node"
    if (cwd / "Cargo.toml").is_file():
        return "rust"
    if (cwd / "go.mod").is_file():
        return "go"
    if (cwd / "pom.xml").is_file():
        return "java"
    if (cwd / "Makefile").is_file():
        return "make"
    if (cwd / ".git").is_dir():
        return "git"
    return "generic"


def scaffold(cwd: Path) -> tuple[Path, ...]:
    """Create the avo scaffold under ``cwd``. Return the paths written."""

    written: list[Path] = []
    skill_path = cwd / ".avo" / "skills" / "repo-overview" / "SKILL.md"
    if skill_path.exists():
        pass
    else:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(_SKILL_BODY, encoding="utf-8")
        written.append(skill_path)

    agents_path = cwd / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(_AGENTS_BODY, encoding="utf-8")
        written.append(agents_path)

    return tuple(written)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avo init",
        description="Scaffold .avo/skills/ and AGENTS.md in the current directory.",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Target directory (default: current working directory).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        raise InitCliError(f"init target {cwd} is not a directory.")

    kind = _repo_kind(cwd)
    written = scaffold(cwd)

    print(f"Scanned {cwd} ({kind}).")
    if written:
        print("Created:")
        for path in written:
            print(f"  + {path}")
    else:
        print("No new files — `.avo/skills/` and `AGENTS.md` already present.")
    print("Next: `avo chat` to start a session with the new skill loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["InitCliError", "build_parser", "main", "scaffold"]
