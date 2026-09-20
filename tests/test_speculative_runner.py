"""Tests for Speculative Execution and Test-Driven Self-Correction."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from avo.models import ModelResponse
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime
from avo.speculative.runner import SpeculativeRunner, WorkspaceSnapshot


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "TestUser"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    readme = path / "README.md"
    readme.write_text("initial commit", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_workspace_snapshot_save_and_restore(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    snapshot = WorkspaceSnapshot(tmp_path)
    tag = snapshot.capture("before-edit")

    # Modify file
    (tmp_path / "README.md").write_text("corrupted content", encoding="utf-8")
    (tmp_path / "bad.py").write_text("syntax error !!!", encoding="utf-8")
    assert (tmp_path / "bad.py").is_file()

    # Restore snapshot
    restored = snapshot.restore(tag)
    assert restored is True
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "initial commit"
    assert not (tmp_path / "bad.py").exists()


@pytest.mark.asyncio
async def test_speculative_runner_auto_rolls_back_on_test_failure(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    test_file = tmp_path / "test_sample.py"
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)  # noqa: ASYNC221
    subprocess.run(  # noqa: ASYNC221
        ["git", "commit", "-m", "add test"], cwd=tmp_path, check=True, capture_output=True
    )

    runtime = AgentRuntime(provider=FakeProvider([ModelResponse(content="breaking code")]))

    # Test runner command that fails
    runner = SpeculativeRunner(
        runtime=runtime,
        workspace_root=tmp_path,
        test_command="false",  # always fails
        max_attempts=1,
    )

    def _break_file() -> None:
        (tmp_path / "test_sample.py").write_text("def test_ok(): assert False\n", encoding="utf-8")

    result = await runner.run_speculative(
        prompt="make breaking edit",
        action_fn=_break_file,
    )

    assert result.success is False
    assert result.rolled_back is True
    # Verify file was rolled back to clean state
    assert "assert True" in (tmp_path / "test_sample.py").read_text(encoding="utf-8")
    assert "assert True" in (tmp_path / "test_sample.py").read_text(encoding="utf-8")
