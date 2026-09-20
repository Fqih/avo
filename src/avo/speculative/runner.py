"""Speculative execution and test-driven self-correction for Avo."""

from __future__ import annotations

import json
import secrets
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from avo.runtime import AgentRuntime


@dataclass(frozen=True)
class SnapshotMetadata:
    """Durable metadata for a workspace git snapshot."""

    tag: str
    head_sha: str
    created_at: str
    stash_sha: str | None = None
    description: str | None = None


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
        self._git_dir = self._resolve_git_dir()
        self._metadata_path = self._git_dir / "avo_snapshots.json"

    def _resolve_git_dir(self) -> Path:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            raw = res.stdout.strip()
            p = Path(raw)
            return p if p.is_absolute() else (self.root / p).resolve()
        except Exception:
            return self.root / ".git"

    def _load_metadata(self) -> dict[str, SnapshotMetadata]:
        if not self._metadata_path.is_file():
            return {}
        try:
            content = self._metadata_path.read_text(encoding="utf-8")
            data = json.loads(content)
            res: dict[str, SnapshotMetadata] = {}
            for item in data:
                meta = SnapshotMetadata(**item)
                res[meta.tag] = meta
            return res
        except Exception:
            return {}

    def _save_metadata(self, metadata: dict[str, SnapshotMetadata]) -> None:
        try:
            self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "tag": m.tag,
                    "head_sha": m.head_sha,
                    "created_at": m.created_at,
                    "stash_sha": m.stash_sha,
                    "description": m.description,
                }
                for m in metadata.values()
            ]
            temp_path = self._metadata_path.with_suffix(f".tmp.{secrets.token_hex(4)}")
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_path.replace(self._metadata_path)
        except Exception:
            pass

    def get(self, tag: str) -> SnapshotMetadata | None:
        """Get snapshot metadata by tag."""
        return self._load_metadata().get(tag)

    def list_snapshots(self) -> list[SnapshotMetadata]:
        """List all captured snapshots, sorted newest first."""
        snaps = list(self._load_metadata().values())
        snaps.sort(key=lambda m: m.created_at, reverse=True)
        return snaps

    def capture(self, name: str | None = None, description: str | None = None) -> str:
        """Create a stash snapshot of the current workspace state and persist metadata."""
        tag = name or f"avo-snap-{secrets.token_hex(4)}"

        head_sha = ""
        try:
            res_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            head_sha = res_head.stdout.strip()
        except Exception:
            pass

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

        meta = SnapshotMetadata(
            tag=tag,
            head_sha=head_sha,
            created_at=datetime.now(UTC).isoformat(),
            stash_sha=stash_sha,
            description=description,
        )
        snaps = self._load_metadata()
        snaps[tag] = meta
        self._save_metadata(snaps)
        return tag

    def restore(self, tag: str) -> bool:
        """Roll back working directory to the exact state at capture time.

        Refuses to run destructive commands if tag is invalid or missing.
        """
        meta = self.get(tag)
        if meta is None:
            return False

        if meta.stash_sha:
            check_stash = subprocess.run(
                ["git", "rev-parse", "-q", "--verify", meta.stash_sha],
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
            )
            if check_stash.returncode != 0:
                return False

        try:
            # 1. Discard all dirty changes and untracked files introduced by speculative run
            subprocess.run(
                ["git", "reset", "--hard", meta.head_sha or "HEAD"],
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
            if meta.stash_sha:
                apply_res = subprocess.run(
                    ["git", "stash", "apply", meta.stash_sha],
                    cwd=self.root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if apply_res.returncode != 0:
                    subprocess.run(
                        ["git", "reset", "--hard", meta.head_sha or "HEAD"],
                        cwd=self.root,
                        check=False,
                        capture_output=True,
                    )
                    subprocess.run(
                        ["git", "clean", "-fd"],
                        cwd=self.root,
                        check=False,
                        capture_output=True,
                    )
                    return False

            return True
        except subprocess.CalledProcessError:
            return False

    def discard(self, tag: str) -> bool:
        """Discard a snapshot stash and its metadata when no longer needed."""
        snaps = self._load_metadata()
        meta = snaps.pop(tag, None)
        if meta is None:
            return False
        self._save_metadata(snaps)

        if not meta.stash_sha:
            return True

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
        return True


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
