"""Tests for the atomic batch_replace tool."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from avo.app_tools.batch_replace import (
    BatchReplaceArguments,
    FilePatch,
    batch_replace_tool,
)
from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.workspace import Workspace
from avo.exceptions import ToolExecutionError


@pytest.fixture
def workspace_env(tmp_path: Path):
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    ws = Workspace(ws_dir)
    with bind_workspace(ws):
        yield ws_dir


def test_file_patch_requires_difference() -> None:
    with pytest.raises(ValidationError, match="old_string and new_string must differ"):
        FilePatch(path="file.txt", old_string="same", new_string="same")


@pytest.mark.asyncio
async def test_batch_replace_single_file(workspace_env: Path) -> None:
    file_path = workspace_env / "greeting.py"
    file_path.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    tool = batch_replace_tool()
    args = BatchReplaceArguments(
        patches=[
            FilePatch(path="greeting.py", old_string="'world'", new_string="'universe'"),
        ],
        dry_run=False,
    )

    result = await tool.invoke(args.model_dump())
    assert result["status"] == "applied"
    assert result["files_changed"] == 1
    assert result["total_replacements"] == 1
    assert "universe" in file_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_batch_replace_multi_files(workspace_env: Path) -> None:
    f1 = workspace_env / "config.py"
    f2 = workspace_env / "service.py"
    f1.write_text("HOST = 'localhost'\nPORT = 8080\n", encoding="utf-8")
    f2.write_text("url = f'http://{HOST}:{PORT}/api'\n", encoding="utf-8")

    tool = batch_replace_tool()
    args = BatchReplaceArguments(
        patches=[
            FilePatch(path="config.py", old_string="8080", new_string="9000"),
            FilePatch(path="service.py", old_string="/api", new_string="/v1/api"),
        ],
        dry_run=False,
    )

    result = await tool.invoke(args.model_dump())
    assert result["status"] == "applied"
    assert result["files_changed"] == 2
    assert result["total_replacements"] == 2

    assert "PORT = 9000" in f1.read_text(encoding="utf-8")
    assert "/v1/api" in f2.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_batch_replace_multiple_patches_same_file(workspace_env: Path) -> None:
    f = workspace_env / "math_ops.py"
    f.write_text("a = 10\nb = 20\n", encoding="utf-8")

    tool = batch_replace_tool()
    args = BatchReplaceArguments(
        patches=[
            FilePatch(path="math_ops.py", old_string="10", new_string="100"),
            FilePatch(path="math_ops.py", old_string="20", new_string="200"),
        ],
        dry_run=False,
    )

    result = await tool.invoke(args.model_dump())
    assert result["status"] == "applied"
    assert result["files_changed"] == 1
    assert result["total_replacements"] == 2
    assert f.read_text(encoding="utf-8") == "a = 100\nb = 200\n"


@pytest.mark.asyncio
async def test_batch_replace_replace_all_behavior(workspace_env: Path) -> None:
    f = workspace_env / "repeated.txt"
    f.write_text("foo bar foo baz foo\n", encoding="utf-8")

    tool = batch_replace_tool()

    # Without replace_all=True, multiple matches fail
    args_fail = BatchReplaceArguments(
        patches=[
            FilePatch(path="repeated.txt", old_string="foo", new_string="qux", replace_all=False)
        ],
    )
    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.invoke(args_fail.model_dump())
    assert "matches 3 locations" in str(exc_info.value)

    # File remains unchanged
    assert f.read_text(encoding="utf-8") == "foo bar foo baz foo\n"

    # With replace_all=True, all matches are replaced
    args_ok = BatchReplaceArguments(
        patches=[
            FilePatch(path="repeated.txt", old_string="foo", new_string="qux", replace_all=True)
        ],
    )
    result = await tool.invoke(args_ok.model_dump())
    assert result["status"] == "applied"
    assert result["total_replacements"] == 3
    assert f.read_text(encoding="utf-8") == "qux bar qux baz qux\n"


@pytest.mark.asyncio
async def test_batch_replace_dry_run(workspace_env: Path) -> None:
    f = workspace_env / "dry_file.txt"
    orig_content = "original state line 1\nline 2\n"
    f.write_text(orig_content, encoding="utf-8")

    tool = batch_replace_tool()
    args = BatchReplaceArguments(
        patches=[
            FilePatch(
                path="dry_file.txt",
                old_string="original state",
                new_string="previewed state",
            )
        ],
        dry_run=True,
    )

    result = await tool.invoke(args.model_dump())
    assert result["status"] == "dry_run"
    assert result["files_changed"] == 1
    assert result["total_replacements"] == 1
    assert "+previewed state" in result["diff"]
    assert "-original state" in result["diff"]

    # Assert file on disk was NOT modified
    assert f.read_text(encoding="utf-8") == orig_content


@pytest.mark.asyncio
async def test_batch_replace_atomic_all_or_nothing(workspace_env: Path) -> None:
    f1 = workspace_env / "valid.txt"
    f2 = workspace_env / "invalid.txt"
    f1.write_text("will not change\n", encoding="utf-8")
    f2.write_text("another text\n", encoding="utf-8")

    tool = batch_replace_tool()
    args = BatchReplaceArguments(
        patches=[
            FilePatch(path="valid.txt", old_string="will not change", new_string="corrupted"),
            # f2 does not contain 'nonexistent string', so this will fail
            FilePatch(path="invalid.txt", old_string="nonexistent string", new_string="replaced"),
        ],
        dry_run=False,
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.invoke(args.model_dump())

    assert "not found in file" in str(exc_info.value)
    # Crucial assertion: valid.txt must NOT have been changed on disk!
    assert f1.read_text(encoding="utf-8") == "will not change\n"
    assert f2.read_text(encoding="utf-8") == "another text\n"
