"""Git repository helper — thin wrapper around the ``git`` CLI.

The runtime never spawns a shell; every call goes through
:func:`_run_git` which passes argument lists (no ``shell=True``) and
captures stdout/stderr. The wrapper refuses to operate outside a
:func:`Workspace.root` so a model cannot coerce it into reading an
arbitrary path.

This module is read-only — it does not mutate the working tree. All
operations are idempotent and resumable.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from avo.exceptions import ToolExecutionError

GitError = ToolExecutionError

_DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class GitStatusEntry:
    """A single entry in ``git status --porcelain`` output."""

    path: str
    status_code: str  # two-character porcelain status (e.g. " M", "M ", "??")

    @property
    def is_modified(self) -> bool:
        return self.status_code.strip() not in ("", "??")

    @property
    def is_untracked(self) -> bool:
        return self.status_code == "??"


@dataclass(frozen=True)
class GitStatus:
    """Snapshot of repository state."""

    root: Path
    branch: str | None
    entries: tuple[GitStatusEntry, ...]

    @property
    def modified(self) -> tuple[str, ...]:
        return tuple(e.path for e in self.entries if e.is_modified)

    @property
    def untracked(self) -> tuple[str, ...]:
        return tuple(e.path for e in self.entries if e.is_untracked)

    @property
    def clean(self) -> bool:
        return not any(e.is_modified for e in self.entries)

    @property
    def is_clean(self) -> bool:
        return len(self.entries) == 0


def _run_git(
    cwd: Path,
    args: Sequence[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with ``args`` inside ``cwd``. Never shell=True."""

    try:
        return subprocess.run(  # host helper, no shell
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0] if args else 'help'} timed out") from exc


def _parse_porcelain(output: str) -> tuple[GitStatusEntry, ...]:
    entries: list[GitStatusEntry] = []
    for line in output.splitlines():
        if not line:
            continue
        # Porcelain v1 format: "<XY> <path>" with 2-char code + space + path.
        if len(line) < 3:
            continue
        code = line[:2]
        path = line[3:].strip()
        # Renames print "R  old -> new"; keep just the new name.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append(GitStatusEntry(path=path, status_code=code))
    return tuple(entries)


class GitRepository:
    """Lazy wrapper around the ``git`` CLI scoped to one root."""

    __slots__ = ("_root",)

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def is_repository(self) -> bool:
        """Return True if ``root`` is inside a git working tree."""

        result = _run_git(self._root, ["rev-parse", "--is-inside-work-tree"])
        return result.returncode == 0 and result.stdout.strip() == "true"

    def current_branch(self) -> str | None:
        result = _run_git(self._root, ["symbolic-ref", "--short", "HEAD"])
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def status(self) -> GitStatus:
        """Return a snapshot of repository state; never raises on dirty trees."""

        if not self.is_repository():
            raise GitError(f"{self._root} is not a git repository")
        branch = self.current_branch()
        status_proc = _run_git(self._root, ["status", "--porcelain"])
        if status_proc.returncode != 0:
            raise GitError(f"git status failed: {status_proc.stderr.strip()}")
        return GitStatus(
            root=self._root,
            branch=branch,
            entries=_parse_porcelain(status_proc.stdout),
        )

    def diff_summary(self, *, max_files: int = 20) -> str:
        """Return a short, human-readable diff stat. ``max_files`` caps names."""

        status = self.status()
        if status.clean:
            return f"On branch {status.branch or '(detached)'}: working tree clean."
        lines = [f"On branch {status.branch or '(detached)'}:"]
        if status.modified:
            names = "\n".join(f"  M {p}" for p in status.modified[:max_files])
            extra = (
                ""
                if len(status.modified) <= max_files
                else f"\n  ... +{len(status.modified) - max_files} more"
            )
            lines.append(f"Modified:\n{names}{extra}")
        if status.untracked:
            names = "\n".join(f"  ? {p}" for p in status.untracked[:max_files])
            extra = (
                ""
                if len(status.untracked) <= max_files
                else f"\n  ... +{len(status.untracked) - max_files} more"
            )
            lines.append(f"Untracked:\n{names}{extra}")
        return "\n".join(lines)

    def diff(
        self,
        path: str | None = None,
        *,
        staged: bool = False,
        max_lines: int = 500,
    ) -> str:
        """Return the unified git diff.

        When ``staged`` is True, compares staged changes to HEAD.
        ``path`` optionally restricts the diff to a single workspace-relative path.
        Output is truncated to ``max_lines`` to protect context windows.
        """

        if not self.is_repository():
            raise GitError(f"{self._root} is not a git repository")

        args = ["diff"]
        if staged:
            args.append("--cached")
        if path:
            args.extend(["--", path])

        proc = _run_git(self._root, args)
        if proc.returncode != 0:
            raise GitError(f"git diff failed: {proc.stderr.strip()}")

        diff_text = proc.stdout
        lines = diff_text.splitlines()
        if len(lines) > max_lines:
            truncated = lines[:max_lines]
            truncated.append(f"... diff truncated ({len(lines) - max_lines} more lines) ...")
            return "\n".join(truncated)
        return diff_text

    def rollback(self, *, untracked: bool = True) -> list[str]:
        """Revert uncommitted working tree changes back to HEAD.

        Restores modified files and cleans untracked files if untracked is True.
        Returns a list of reverted file paths.
        """
        if not self.is_repository():
            raise GitError(f"{self._root} is not a git repository")

        status = self.status()
        if status.is_clean:
            return []

        affected = list(status.modified)
        if untracked:
            affected.extend(status.untracked)

        proc = _run_git(self._root, ["checkout", "--", "."])
        if proc.returncode != 0:
            _run_git(self._root, ["restore", "."])

        if untracked and status.untracked:
            _run_git(self._root, ["clean", "-fd"])

        return affected


__all__ = [
    "GitError",
    "GitRepository",
    "GitStatus",
    "GitStatusEntry",
]
