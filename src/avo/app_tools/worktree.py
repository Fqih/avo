"""Git worktree isolation for bounded, safe agent code modifications."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from avo.exceptions import AvoError

_LOG = logging.getLogger(__name__)

_VALID_RUN_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")


class GitWorktreeError(AvoError):
    """Raised when git worktree operations fail."""


class GitWorktreeManager:
    """Manages isolated git worktrees per agent run to protect the user's workspace."""

    def __init__(
        self,
        repo_root: Path | str,
        worktree_base_dir: Path | str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        if not (self.repo_root / ".git").exists():
            raise GitWorktreeError(f"Directory is not a git repository: {self.repo_root}")

        if worktree_base_dir is not None:
            self.worktree_base = Path(worktree_base_dir).resolve()
        else:
            self.worktree_base = (self.repo_root / ".avo" / "worktrees").resolve()

        self._ensure_gitignore()

    def _ensure_gitignore(self) -> None:
        """Ensure worktree base directory is ignored in .gitignore."""
        gitignore_path = self.repo_root / ".gitignore"
        relative_entry = ".avo/worktrees"

        if gitignore_path.exists():
            content = gitignore_path.read_text(encoding="utf-8")
            if relative_entry not in content and ".avo/" not in content:
                with gitignore_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n# Avo agent worktrees\n{relative_entry}/\n")

    def _validate_run_id(self, run_id: str) -> str:
        clean = run_id.strip()
        if not clean or not _VALID_RUN_ID.match(clean):
            msg = f"Invalid run_id: {run_id!r}. Must contain only letters, digits, '-', and '_'."
            raise GitWorktreeError(msg)
        return clean

    def get_worktree_path(self, run_id: str) -> Path:
        """Get the expected path of a worktree for the given run_id."""
        clean_id = self._validate_run_id(run_id)
        target = (self.worktree_base / clean_id).resolve()
        # Security check: must reside inside worktree_base
        if not str(target).startswith(str(self.worktree_base)):
            raise GitWorktreeError(f"Target worktree escapes base directory: {target}")
        return target

    def create_worktree(self, run_id: str, base_ref: str = "HEAD") -> Path:
        """Create a new git worktree on an isolated branch for the given run_id."""
        clean_id = self._validate_run_id(run_id)
        target = self.get_worktree_path(clean_id)
        branch_name = f"avo/task-{clean_id}"

        self.worktree_base.mkdir(parents=True, exist_ok=True)

        if target.exists():
            _LOG.warning("Worktree directory already exists, cleaning up first: %s", target)
            self.cleanup_worktree(clean_id, merge=False)

        cmd = [
            "git",
            "worktree",
            "add",
            "-b",
            branch_name,
            str(target),
            base_ref,
        ]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise GitWorktreeError(
                f"Failed to create git worktree at {target}: {res.stderr.strip()}"
            )

        _LOG.info("Created isolated git worktree: %s on branch %s", target, branch_name)
        return target

    def cleanup_worktree(
        self,
        run_id: str,
        merge: bool = False,
        target_branch: str | None = None,
    ) -> bool:
        """Remove a worktree, optionally merging its branch into target_branch."""
        clean_id = self._validate_run_id(run_id)
        target = self.get_worktree_path(clean_id)
        branch_name = f"avo/task-{clean_id}"

        if merge:
            dest = target_branch or "main"
            cb_proc = subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            original_branch = cb_proc.stdout.strip()
            need_switch = bool(dest and original_branch and dest != original_branch)
            if need_switch:
                co_proc = subprocess.run(
                    ["git", "checkout", dest],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if co_proc.returncode != 0:
                    raise GitWorktreeError(
                        f"Failed to switch to target branch {dest}: {co_proc.stderr.strip()}"
                    )

            try:
                # Merge branch into target branch
                merge_cmd = ["git", "merge", branch_name]
                m_res = subprocess.run(
                    merge_cmd, cwd=self.repo_root, capture_output=True, text=True, check=False
                )
                if m_res.returncode != 0:
                    raise GitWorktreeError(
                        f"Failed to merge branch {branch_name} into {dest}: {m_res.stderr.strip()}"
                    )
            finally:
                if need_switch and original_branch:
                    subprocess.run(
                        ["git", "checkout", original_branch],
                        cwd=self.repo_root,
                        capture_output=True,
                        check=False,
                    )

        # Remove the worktree from git
        if target.exists():
            rm_cmd = ["git", "worktree", "remove", "--force", str(target)]
            subprocess.run(rm_cmd, cwd=self.repo_root, capture_output=True, check=False)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

        # Delete the temporary branch
        del_branch = ["git", "branch", "-D", branch_name]
        subprocess.run(del_branch, cwd=self.repo_root, capture_output=True, check=False)

        return not target.exists()

    def list_active_worktrees(self) -> list[dict[str, Any]]:
        """List active git worktrees tracked by this repository."""
        cmd = ["git", "worktree", "list", "--porcelain"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return []

        worktrees: list[dict[str, Any]] = []
        current: dict[str, Any] = {}

        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                if current and "worktree" in current:
                    wt_path = Path(current["worktree"])
                    if str(wt_path).startswith(str(self.worktree_base)):
                        current["run_id"] = wt_path.name
                        worktrees.append(current)
                current = {}
                continue

            if line.startswith("worktree "):
                current["worktree"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
            elif line.startswith("HEAD "):
                current["head"] = line.split(" ", 1)[1]

        if current and "worktree" in current:
            wt_path = Path(current["worktree"])
            if str(wt_path).startswith(str(self.worktree_base)):
                current["run_id"] = wt_path.name
                worktrees.append(current)

        return worktrees
