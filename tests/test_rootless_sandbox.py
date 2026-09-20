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


@pytest.mark.asyncio
async def test_rootless_sandbox_rejects_invalid_workspace(tmp_path: Path) -> None:
    executor = RootlessSandboxExecutor()
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(SandboxError, match="invalid workspace"):
        await executor.run("echo hi", workspace_dir=non_existent)


@pytest.mark.asyncio
async def test_rootless_sandbox_isolation_level_metadata(tmp_path: Path) -> None:
    if not is_rootless_sandbox_supported():
        pytest.skip("bwrap not available on this host")

    executor = RootlessSandboxExecutor()
    result = await executor.run("echo hi", workspace_dir=tmp_path)
    assert result.isolation_level == "rootless:bwrap"


def test_parse_mem_limit_bytes() -> None:
    from avo.app_tools.rootless_sandbox import _parse_mem_limit_bytes

    assert _parse_mem_limit_bytes("256m") == 256 * 1024 * 1024
    assert _parse_mem_limit_bytes("1g") == 1024 * 1024 * 1024
    assert _parse_mem_limit_bytes("512k") == 512 * 1024
    assert _parse_mem_limit_bytes("1048576") == 1048576


@pytest.mark.asyncio
async def test_rootless_sandbox_prlimit_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from avo.app_tools.rootless_sandbox import RootlessSandboxExecutor

    monkeypatch.setattr("shutil.which", lambda cmd: f"/bin/{cmd}")
    monkeypatch.setattr(
        "avo.app_tools.rootless_sandbox.is_rootless_sandbox_supported",
        lambda: True,
    )

    captured_args = None

    async def _fake_exec(*args: object, **kwargs: object) -> AsyncMock:
        _ = kwargs
        nonlocal captured_args
        captured_args = list(args)
        proc = AsyncMock()
        proc.communicate.return_value = (b"output", b"")
        proc.returncode = 0
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    executor = RootlessSandboxExecutor(mem_limit="128m", pids_limit=64, timeout_seconds=15.0)
    res = await executor.run("echo test", workspace_dir=tmp_path)

    assert res.isolation_level == "rootless:bwrap"
    assert captured_args is not None
    assert captured_args[0] == "/bin/prlimit"
    assert f"--as={128 * 1024 * 1024}" in captured_args
    assert "--nproc=64" in captured_args
    assert "--cpu=16" in captured_args
    assert "/bin/bwrap" in captured_args
