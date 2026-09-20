"""Tests for Rootless Sandbox Executor (Bubblewrap and process isolation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.app_tools.rootless_sandbox import (
    RootlessSandboxExecutor,
    SandboxError,
    is_rootless_sandbox_supported,
)


@pytest.mark.asyncio
async def test_rootless_sandbox_execution(tmp_path: Path) -> None:
    if not is_rootless_sandbox_supported():
        pytest.skip("bwrap not available on this host")

    test_file = tmp_path / "hello.txt"
    test_file.write_text("rootless-test", encoding="utf-8")

    executor = RootlessSandboxExecutor(network_mode="none", timeout_seconds=10.0)

    result = await executor.run(
        "cat hello.txt && echo 'success'",
        workspace_dir=tmp_path,
    )

    assert result.exit_code == 0
    assert "rootless-test" in result.stdout
    assert "success" in result.stdout
    assert result.image.startswith("bwrap")


@pytest.mark.asyncio
async def test_rootless_sandbox_timeout(tmp_path: Path) -> None:
    if not is_rootless_sandbox_supported():
        pytest.skip("bwrap not available on this host")

    executor = RootlessSandboxExecutor(timeout_seconds=0.5)

    with pytest.raises(SandboxError):
        await executor.run("sleep 2", workspace_dir=tmp_path)
