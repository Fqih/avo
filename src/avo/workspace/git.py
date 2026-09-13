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
from collections.abc import Mapping, Sequence
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
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with ``args`` inside ``cwd``. Never shell=True."""

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if extra_env:
        env.update(extra_env)

    try:
        return subprocess.run(  # host helper, no shell
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
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

    def add(self, paths: Sequence[str] | None = None) -> None:
        """Stage files in the git index.

        If ``paths`` is None or empty, stages all changes (``git add -A``).
        """
        if not self.is_repository():
            raise GitError(f"{self._root} is not a git repository")

        args = ["add", "-A"] if not paths else ["add", "--", *paths]
        proc = _run_git(self._root, args)
        if proc.returncode != 0:
            raise GitError(f"git add failed: {proc.stderr.strip()}")

    def commit(
        self,
        message: str,
        *,
        paths: Sequence[str] | None = None,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> str:
        """Commit staged or specified changes and return the new short commit hash.

        If ``paths`` is provided, those paths are staged first.
        If no files are currently staged, all modified/untracked files are staged.
        """
        if not self.is_repository():
            raise GitError(f"{self._root} is not a git repository")

        if not message.strip():
            raise GitError("commit message cannot be empty")

        if paths:
            self.add(paths)
        else:
            staged_diff = self.diff(staged=True)
            if not staged_diff.strip():
                status = self.status()
                if not status.is_clean:
                    self.add()

        staged_diff = self.diff(staged=True)
        if not staged_diff.strip():
            status = self.status()
            if status.is_clean:
                raise GitError("nothing to commit (working tree clean)")

        extra_env: dict[str, str] = {}
        if author_name:
            extra_env["GIT_AUTHOR_NAME"] = author_name
            extra_env["GIT_COMMITTER_NAME"] = author_name
        if author_email:
            extra_env["GIT_AUTHOR_EMAIL"] = author_email
            extra_env["GIT_COMMITTER_EMAIL"] = author_email

        proc = _run_git(self._root, ["commit", "-m", message], extra_env=extra_env or None)
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip()
            raise GitError(f"git commit failed: {err_msg}")

        rev_proc = _run_git(self._root, ["rev-parse", "--short", "HEAD"])
        if rev_proc.returncode == 0 and rev_proc.stdout.strip():
            return rev_proc.stdout.strip()
        return "HEAD"


def generate_commit_message_heuristic(status: GitStatus) -> str:
    """Generate a conventional commit message based on modified files."""
    all_files = list(status.modified) + list(status.untracked)
    if not all_files:
        return "chore: update workspace"

    if len(all_files) == 1:
        file = all_files[0]
        p = Path(file)
        if file.startswith("tests/") or "test" in p.stem:
            return f"test: update {p.stem}"
        if p.suffix in (".md", ".rst", ".txt"):
            return f"docs: update {p.name}"
        if file.startswith("src/avo/"):
            parts = p.parts
            sub = Path(parts[2]).stem if len(parts) > 2 else p.stem
            return f"feat({sub}): update {p.stem}"
        return f"chore: update {p.name}"

    test_only = all(f.startswith("tests/") or "test" in f for f in all_files)
    if test_only:
        return f"test: update test suite ({len(all_files)} files)"

    docs_only = all(Path(f).suffix in (".md", ".rst", ".txt") for f in all_files)
    if docs_only:
        return f"docs: update documentation ({len(all_files)} files)"

    src_only = all(f.startswith("src/avo/") for f in all_files)
    if src_only:
        domains: set[str] = set()
        for f in all_files:
            parts = Path(f).parts
            if len(parts) > 2:
                domains.add(Path(parts[2]).stem)
        if len(domains) == 1:
            domain = next(iter(domains))
            return f"feat({domain}): update {domain} implementation"

    return f"feat: update {len(all_files)} files across workspace"


__all__ = [
    "GitError",
    "GitRepository",
    "GitStatus",
    "GitStatusEntry",
    "generate_commit_message_heuristic",
]
