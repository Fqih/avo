"""Rootless process and Bubblewrap (bwrap) sandbox isolation fallback."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

from avo.exceptions import ToolExecutionError

from .sandbox import SandboxResult

SandboxError = ToolExecutionError


def is_rootless_sandbox_supported() -> bool:
    """Check if rootless bwrap isolation is available on the host."""
    return bool(shutil.which("bwrap"))


class RootlessSandboxExecutor:
    """Ephemeral command execution isolated with Linux Bubblewrap (bwrap)."""

    def __init__(
        self,
        *,
        network_mode: str = "none",
        timeout_seconds: float = 30.0,
        mem_limit: str = "256m",
    ) -> None:
        self.network_mode = network_mode
        self.timeout_seconds = timeout_seconds
        self.mem_limit = mem_limit

    async def run(
        self,
        command: str,
        *,
        workspace_dir: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> SandboxResult:
        """Run command in a rootless bwrap sandbox."""
        bwrap_path = shutil.which("bwrap")
        if not bwrap_path:
            raise SandboxError("Bubblewrap (bwrap) is not installed on this host.")

        effective_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        ws_resolved = str(Path(workspace_dir).resolve())  # noqa: ASYNC240

        args: list[str] = [
            bwrap_path,
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--bind",
            ws_resolved,
            ws_resolved,
            "--chdir",
            ws_resolved,
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
        ]

        if self.network_mode == "none":
            args.append("--unshare-net")

        args.extend(["--", "sh", "-c", command])

        merged_env = {**os.environ, **(env or {})}
        start_time = time.monotonic()

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            msg = f"Command timed out after {effective_timeout}s: {command!r}"
            raise SandboxError(msg) from exc

        duration_ms = (time.monotonic() - start_time) * 1000.0

        return SandboxResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
            image="bwrap:host",
            network_mode=self.network_mode,
            mem_limit=self.mem_limit,
        )
