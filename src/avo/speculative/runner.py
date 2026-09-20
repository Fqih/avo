"""Speculative execution and test-driven self-correction for Avo."""

from __future__ import annotations

import secrets
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from avo.runtime import AgentRuntime


@dataclass(frozen=True)
class SpeculativeResult:
    """Outcome of a speculative execution run."""

    success: bool
    rolled_back: bool
    attempts: int
    output: str | None = None
    error: str | None = None


class WorkspaceSnapshot:
    """Captures and restores workspace git state for speculative modifications."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._snapshots: dict[str, str | None] = {}

    def capture(self, name: str | None = None) -> str:
        """Create a stash snapshot of the current workspace state."""
        tag = name or f"avo-snap-{secrets.token_hex(4)}"
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        stash_sha: str | None = None
        if status:
            subprocess.run(
                ["git", "stash", "push", "--include-untracked", "-m", tag],
                cwd=self.root,
                check=True,
                capture_output=True,
            )
            res = subprocess.run(
                ["git", "rev-parse", "-q", "--verify", "refs/stash"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            stash_sha = res.stdout.strip()
            subprocess.run(
                ["git", "stash", "apply"],
                cwd=self.root,
                check=True,
                capture_output=True,
            )

        self._snapshots[tag] = stash_sha
        return tag

    def restore(self, tag: str) -> bool:
        """Roll back working directory to the exact state at capture time."""
        try:
            # 1. Discard all dirty changes and untracked files introduced by speculative run
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=self.root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=self.root,
                check=True,
                capture_output=True,
            )

            # 2. Re-apply the initial uncommitted state captured in stash
            stash_sha = self._snapshots.get(tag)
            if stash_sha:
                apply_res = subprocess.run(
                    ["git", "stash", "apply", stash_sha],
                    cwd=self.root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if apply_res.returncode != 0:
                    return False

            return True
        except subprocess.CalledProcessError:
            return False

    def discard(self, tag: str) -> None:
        """Discard a snapshot stash when no longer needed (e.g. on success)."""
        stash_sha = self._snapshots.pop(tag, None)
        if not stash_sha:
            return
        res = subprocess.run(
            ["git", "stash", "list"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        for line in res.stdout.splitlines():
            if tag in line:
                stash_id = line.split(":", 1)[0].strip()
                subprocess.run(
                    ["git", "stash", "drop", stash_id],
                    cwd=self.root,
                    check=False,
                    capture_output=True,
                )
                break


class SpeculativeRunner:
    """Runs tasks with test-driven verification and automatic rollback on failure."""

    def __init__(
        self,
        runtime: AgentRuntime,
        workspace_root: Path,
        test_command: str | Sequence[str] = "pytest -q",
        max_attempts: int = 2,
    ) -> None:
        self.runtime = runtime
        self.workspace_root = Path(workspace_root).resolve()
        self.test_command = test_command
        self.max_attempts = max(1, max_attempts)
        self.snapshot = WorkspaceSnapshot(self.workspace_root)

    def _run_tests(self) -> tuple[bool, str]:
        """Execute test command in workspace. Return (passed, output)."""
        try:
            cmd = (
                shlex.split(self.test_command)
                if isinstance(self.test_command, str)
                else list(self.test_command)
            )
            res = subprocess.run(
                cmd,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=60.0,
                check=False,
            )
            passed = res.returncode == 0
            output = (res.stdout + "\n" + res.stderr).strip()
            return passed, output
        except Exception as exc:
            return False, str(exc)

    async def run_speculative(
        self,
        prompt: str,
        action_fn: Callable[[], Any] | None = None,
    ) -> SpeculativeResult:
        """Run task speculatively; rollback workspace if tests fail."""
        tag = self.snapshot.capture()
        last_output: str | None = None
        last_error: str | None = None

        for attempt in range(1, self.max_attempts + 1):
            if action_fn:
                action_fn()
            else:
                run_res = await self.runtime.run(prompt)
                last_output = run_res.output

            passed, test_output = self._run_tests()
            if passed:
                return SpeculativeResult(
                    success=True,
                    rolled_back=False,
                    attempts=attempt,
                    output=last_output,
                )
            last_error = f"Tests failed:\n{test_output}"

        # If tests still fail after max attempts, roll back workspace
        self.snapshot.restore(tag)

        return SpeculativeResult(
            success=False,
            rolled_back=True,
            attempts=self.max_attempts,
            output=last_output,
            error=last_error,
        )
