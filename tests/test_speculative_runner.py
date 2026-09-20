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


def test_workspace_snapshot_preserves_dirty_user_changes_on_rollback(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    # 1. User has uncommitted dirty changes and untracked file BEFORE capture
    (tmp_path / "README.md").write_text("user wip edit", encoding="utf-8")
    (tmp_path / "user_notes.txt").write_text("my notes", encoding="utf-8")

    snapshot = WorkspaceSnapshot(tmp_path)
    tag = snapshot.capture("wip-save")

    # Working tree still has user wip edit and notes
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "user wip edit"
    assert (tmp_path / "user_notes.txt").read_text(encoding="utf-8") == "my notes"

    # 2. Speculative task ruins the code and creates trash files
    (tmp_path / "README.md").write_text("corrupted by agent", encoding="utf-8")
    (tmp_path / "agent_trash.tmp").write_text("junk", encoding="utf-8")

    # 3. Rollback
    restored = snapshot.restore(tag)
    assert restored is True

    # 4. User's initial uncommitted changes MUST be preserved, agent trash deleted
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "user wip edit"
    assert (tmp_path / "user_notes.txt").read_text(encoding="utf-8") == "my notes"
    assert not (tmp_path / "agent_trash.tmp").exists()


def test_rollback_rejects_invalid_tag_without_resetting_workspace(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "important_work.txt").write_text("do not delete me", encoding="utf-8")
    (tmp_path / "README.md").write_text("dirty edit", encoding="utf-8")

    snapshot = WorkspaceSnapshot(tmp_path)
    # Attempting to restore non-existent tag must fail and NOT delete or reset files
    assert snapshot.restore("invalid-tag-123") is False
    assert (tmp_path / "important_work.txt").read_text(encoding="utf-8") == "do not delete me"
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "dirty edit"


def test_snapshot_metadata_persists_across_instances(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("edit 1", encoding="utf-8")

    snap1 = WorkspaceSnapshot(tmp_path)
    tag = snap1.capture("checkpoint-1")

    # Second instance representing a different command or process
    snap2 = WorkspaceSnapshot(tmp_path)
    meta = snap2.get(tag)
    assert meta is not None
    assert meta.tag == tag
    assert len(snap2.list_snapshots()) >= 1

    (tmp_path / "README.md").write_text("corrupted", encoding="utf-8")
    assert snap2.restore(tag) is True
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "edit 1"


def test_rollback_with_tracked_staged_untracked_and_multiple_snapshots(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    # State 1: staged file + tracked edit
    (tmp_path / "README.md").write_text("version 1", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("staged 1", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=tmp_path, check=True, capture_output=True)

    snap = WorkspaceSnapshot(tmp_path)
    tag1 = snap.capture("snap-1")

    # State 2: modify staged, add untracked
    (tmp_path / "README.md").write_text("version 2", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("untracked 2", encoding="utf-8")
    tag2 = snap.capture("snap-2")
    assert snap.get(tag2) is not None

    # Corrupt workspace
    (tmp_path / "README.md").write_text("corrupted version", encoding="utf-8")
    (tmp_path / "trash.tmp").write_text("rubbish", encoding="utf-8")

    # Roll back to snap-1
    assert snap.restore(tag1) is True
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "version 1"
    assert not (tmp_path / "trash.tmp").exists()
    assert not (tmp_path / "untracked.txt").exists()


@pytest.mark.asyncio
async def test_speculative_runner_discards_snapshot_on_success(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    runtime = AgentRuntime(provider=FakeProvider([ModelResponse(content="ok")]))
    runner = SpeculativeRunner(runtime=runtime, workspace_root=tmp_path, test_command=["true"])

    res = await runner.run_speculative(prompt="test", action_fn=lambda: None)
    assert res.success is True
    assert res.rolled_back is False
    assert len(runner.snapshot.list_snapshots()) == 0


@pytest.mark.asyncio
async def test_speculative_runner_supports_async_action_fn(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    runtime = AgentRuntime(provider=FakeProvider([ModelResponse(content="ok")]))
    runner = SpeculativeRunner(runtime=runtime, workspace_root=tmp_path, test_command=["true"])

    called = False

    async def _async_action() -> None:
        nonlocal called
        called = True

    res = await runner.run_speculative(prompt="test", action_fn=_async_action)
    assert res.success is True
    assert called is True


@pytest.mark.asyncio
async def test_speculative_runner_truncates_long_test_output(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    runtime = AgentRuntime(provider=FakeProvider([ModelResponse(content="ok")]))
    # Run command that produces massive output and fails
    runner = SpeculativeRunner(
        runtime=runtime,
        workspace_root=tmp_path,
        test_command=["sh", "-c", "python -c 'print(\"x\" * 10000)'; exit 1"],
        max_attempts=1,
        max_output_length=200,
    )

    res = await runner.run_speculative(prompt="test", action_fn=lambda: None)
    assert res.success is False
    assert res.error is not None
    assert "[output truncated]" in res.error
    assert len(res.error) < 1000


