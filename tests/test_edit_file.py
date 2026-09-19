"""Tests for the ``edit_file`` tool.

The path traversal contract is owned by :class:`Workspace` and covered
in ``tests/test_workspace.py``; here we only assert the surgical-edit
behaviour on a real temporary file bound to the active workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.app_tools.edit_file import (
    EditFileArguments,
    EditFileError,
    edit_file_tool,
)
from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.workspace import Workspace, WorkspacePathError


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, create=True)


async def _invoke(arguments: EditFileArguments) -> dict[str, object]:
    tool = edit_file_tool()
    return await tool._function(arguments)  # type: ignore[no-any-return]


async def test_edit_file_replaces_single_match(tmp_path: Path, workspace: Workspace) -> None:
    target = tmp_path / "example.txt"
    target.write_text("hello world\nhello again\n", encoding="utf-8")

    with bind_workspace(workspace):
        result = await _invoke(
            EditFileArguments(path="example.txt", old_string="hello again", new_string="hi again")
        )

    assert result["matches_replaced"] == 1
    assert "diff" in result
    assert "-hello again" in str(result["diff"])
    assert "+hi again" in str(result["diff"])
    assert target.read_text(encoding="utf-8") == "hello world\nhi again\n"


async def test_edit_file_ambiguous_match_rejects_without_replace_all(
    tmp_path: Path, workspace: Workspace
) -> None:
    target = tmp_path / "dup.txt"
    target.write_text("foo\nfoo\n", encoding="utf-8")

    with bind_workspace(workspace), pytest.raises(EditFileError, match="2 locations"):
        await _invoke(EditFileArguments(path="dup.txt", old_string="foo", new_string="bar"))

    assert target.read_text(encoding="utf-8") == "foo\nfoo\n"


async def test_edit_file_replace_all_replaces_every_match(
    tmp_path: Path, workspace: Workspace
) -> None:
    target = tmp_path / "multi.txt"
    target.write_text("foo\nfoo\nfoo\n", encoding="utf-8")

    with bind_workspace(workspace):
        result = await _invoke(
            EditFileArguments(
                path="multi.txt", old_string="foo", new_string="baz", replace_all=True
            )
        )

    assert result["matches_replaced"] == 3
    assert target.read_text(encoding="utf-8") == "baz\nbaz\nbaz\n"


async def test_edit_file_missing_match_raises(tmp_path: Path, workspace: Workspace) -> None:
    target = tmp_path / "no_match.txt"
    target.write_text("alpha\n", encoding="utf-8")

    with bind_workspace(workspace), pytest.raises(EditFileError, match="not found"):
        await _invoke(EditFileArguments(path="no_match.txt", old_string="omega", new_string="beta"))


async def test_edit_file_rejects_identical_old_and_new(
    tmp_path: Path, workspace: Workspace
) -> None:
    target = tmp_path / "noop.txt"
    target.write_text("same\n", encoding="utf-8")

    with bind_workspace(workspace), pytest.raises(ValueError, match="must differ"):
        EditFileArguments(path="noop.txt", old_string="same", new_string="same")


async def test_edit_file_validates_path_against_workspace(
    tmp_path: Path, workspace: Workspace
) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("data", encoding="utf-8")

    with bind_workspace(workspace), pytest.raises(WorkspacePathError):
        await _invoke(EditFileArguments(path=str(outside), old_string="data", new_string="other"))

    assert outside.read_text(encoding="utf-8") == "data"


async def test_edit_file_raises_without_active_workspace(tmp_path: Path) -> None:
    target = tmp_path / "loose.txt"
    target.write_text("text\n", encoding="utf-8")

    with pytest.raises(EditFileError, match="without an active workspace"):
        await _invoke(EditFileArguments(path=str(target), old_string="text", new_string="next"))

    assert target.read_text(encoding="utf-8") == "text\n"


async def test_edit_file_tool_metadata_describes_contract() -> None:
    tool = edit_file_tool()
    metadata = tool.metadata
    assert metadata.name == "edit_file"
    assert "workspace" in metadata.description.lower()
    assert metadata.input_schema["properties"]["path"]
    assert metadata.input_schema["properties"]["old_string"]
    assert metadata.input_schema["properties"]["new_string"]
    assert metadata.input_schema["properties"]["replace_all"]
