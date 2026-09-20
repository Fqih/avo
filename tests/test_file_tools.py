"""Tests for the workspace-bound ``read_file`` / ``write_file`` tools."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from avo.app_tools.file_tools import (
    ReadFileArguments,
    WriteFileArguments,
    bind_workspace,
    read_file_tool,
    write_file_tool,
)
from avo.app_tools.terminal_tool import run_terminal_tool
from avo.app_tools.workspace import Workspace
from avo.config_resolver import resolve_security_config
from avo.models import ToolCall


@pytest.fixture
def workspace_tree(tmp_path: Path) -> Workspace:
    root = tmp_path
    (root / "file.txt").write_text("hello", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested content", encoding="utf-8")
    return Workspace(root)


@pytest.mark.asyncio
async def test_read_file_returns_content(workspace_tree: Workspace) -> None:
    with bind_workspace(workspace_tree):
        result = await read_file_tool().invoke({"path": "file.txt"})
    assert result == {
        "path": str((workspace_tree.root / "file.txt").resolve()),
        "size": 5,
        "content": "hello",
    }


@pytest.mark.asyncio
async def test_read_file_nested(workspace_tree: Workspace) -> None:
    with bind_workspace(workspace_tree):
        result = await read_file_tool().invoke({"path": "sub/nested.txt"})
    assert isinstance(result, dict)
    assert result["content"] == "nested content"
    assert result["size"] == len(b"nested content")


@pytest.mark.asyncio
async def test_read_file_rejects_traversal(workspace_tree: Workspace) -> None:
    tool = read_file_tool()
    with bind_workspace(workspace_tree), pytest.raises(Exception, match="escapes"):
        await tool.invoke({"path": "../escape.txt"})


@pytest.mark.asyncio
async def test_read_file_rejects_absolute_outside(workspace_tree: Workspace) -> None:
    tool = read_file_tool()
    with bind_workspace(workspace_tree), pytest.raises(Exception, match="escapes"):
        await tool.invoke({"path": "/etc/passwd"})


@pytest.mark.asyncio
async def test_write_file_creates_new_file(workspace_tree: Workspace, tmp_path: Path) -> None:
    tool = write_file_tool()
    with bind_workspace(workspace_tree):
        result = await tool.invoke({"path": "new.txt", "content": "fresh"})
    assert isinstance(result, dict)
    assert result["path"] == str((workspace_tree.root / "new.txt").resolve())
    assert result["size"] == 5
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "fresh"


@pytest.mark.asyncio
async def test_write_file_overwrites_existing(workspace_tree: Workspace, tmp_path: Path) -> None:
    tool = write_file_tool()
    with bind_workspace(workspace_tree):
        await tool.invoke({"path": "file.txt", "content": "rewritten"})
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "rewritten"


@pytest.mark.asyncio
async def test_write_file_rejects_traversal(workspace_tree: Workspace) -> None:
    tool = write_file_tool()
    with bind_workspace(workspace_tree), pytest.raises(Exception, match="escapes"):
        await tool.invoke({"path": "../escape.txt", "content": "x"})


@pytest.mark.asyncio
async def test_write_file_rejects_absolute_outside(workspace_tree: Workspace) -> None:
    tool = write_file_tool()
    with bind_workspace(workspace_tree), pytest.raises(Exception, match="escapes"):
        await tool.invoke({"path": "/tmp/escape.txt", "content": "x"})


@pytest.mark.asyncio
async def test_write_file_refuses_to_follow_symlink_at_leaf(
    workspace_tree: Workspace, tmp_path: Path
) -> None:
    """A symlink whose leaf is the file must not be silently followed."""

    from avo.exceptions import ToolExecutionError

    target = tmp_path / "external.txt"
    target.write_text("external", encoding="utf-8")
    os.symlink(target, tmp_path / "link_to_target.txt")

    tool = write_file_tool()
    with bind_workspace(workspace_tree), pytest.raises(ToolExecutionError, match="symlink"):
        await tool.invoke({"path": "link_to_target.txt", "content": "replacement"})


@pytest.mark.asyncio
async def test_read_file_no_binding_raises() -> None:
    """Setup errors propagate as ToolExecutionError per the registry contract."""

    from avo.exceptions import ToolExecutionError

    tool = read_file_tool()
    with pytest.raises(ToolExecutionError, match="WorkspaceNotBoundError"):
        await tool.invoke({"path": "file.txt"})


@pytest.mark.asyncio
async def test_write_file_no_binding_raises() -> None:
    from avo.exceptions import ToolExecutionError

    tool = write_file_tool()
    with pytest.raises(ToolExecutionError, match="WorkspaceNotBoundError"):
        await tool.invoke({"path": "file.txt", "content": "x"})


@pytest.mark.asyncio
async def test_run_terminal_executes_in_active_workspace(workspace_tree: Workspace) -> None:
    host_policy = resolve_security_config(
        explicit={"sandbox_required": False},
        environ={},
        user_root=workspace_tree.root / ".avo-test-config",
    )
    with bind_workspace(workspace_tree):
        result = await run_terminal_tool(security_config=host_policy).invoke(
            {"command": "printf terminal-ok"}
        )

    assert isinstance(result, dict)
    assert result["exit_code"] == 0
    assert result["stdout"] == "terminal-ok"
    assert result["cwd"] == str(workspace_tree.root)


def test_bind_workspace_restores_state() -> None:
    from avo.app_tools import file_tools as module

    ws1 = Workspace(Path("/tmp"))
    ws2 = Workspace(Path("/var"))
    assert module._workspace_stack == []
    with bind_workspace(ws1):
        with bind_workspace(ws2):
            assert module._workspace_stack[-1] is ws2
        assert module._workspace_stack[-1] is ws1
    assert module._workspace_stack == []


def test_tools_have_metadata() -> None:
    read_metadata = read_file_tool().metadata
    write_metadata = write_file_tool().metadata
    assert read_metadata.name == "read_file"
    assert write_metadata.name == "write_file"
    props = read_metadata.input_schema["properties"]
    assert isinstance(props, dict)
    assert "path" in props
    write_props = write_metadata.input_schema["properties"]
    assert isinstance(write_props, dict)
    assert "content" in write_props


def test_arguments_validation_rejects_empty_path() -> None:
    """Pydantic enforces min_length=1 on path; content stays free-form."""

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReadFileArguments(path="")
    # Empty content is allowed — some workflows intentionally blank a file.
    args = WriteFileArguments(path="ok", content="")
    assert args.content == ""


@pytest.mark.asyncio
async def test_via_tool_registry_uses_bound_workspace(
    workspace_tree: Workspace, tmp_path: Path
) -> None:
    """Exercise the same path through ToolRegistry.invoke to confirm integration."""

    from avo.tools import ToolRegistry

    registry = ToolRegistry([read_file_tool(), write_file_tool()])
    with bind_workspace(workspace_tree):
        write_call = ToolCall(
            tool_call_id="w1",
            name="write_file",
            arguments={"path": "via.txt", "content": "hi"},
        )
        result = await registry.invoke(write_call, completed_tool_call_ids=set())
    assert result.success is True
    assert (tmp_path / "via.txt").read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_write_file_rejects_git_directory(workspace_tree: Workspace, tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    tool = write_file_tool()
    with bind_workspace(workspace_tree), pytest.raises(Exception, match=r"\.git"):
        await tool.invoke({"path": ".git/hooks/pre-commit", "content": "malicious"})
