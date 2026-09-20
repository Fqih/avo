"""Tests for terminal tool call previews and diff rendering in approval prompts."""

from __future__ import annotations

import io

import pytest

from avo.models import ToolCall
from avo.permissions import (
    _default_console_prompter,
    render_tool_call_preview,
)


def test_render_tool_call_preview_for_edit_file() -> None:
    call = ToolCall(
        name="edit_file",
        arguments={
            "path": "src/app.py",
            "old_text": "def old_fn():\n    return 1",
            "new_text": "def new_fn():\n    return 2",
        },
    )

    preview = render_tool_call_preview(call, color=False)
    assert "edit_file: src/app.py" in preview
    assert "- def old_fn():" in preview
    assert "+ def new_fn():" in preview


def test_render_tool_call_preview_for_run_terminal() -> None:
    call = ToolCall(
        name="run_terminal",
        arguments={"command": "pytest tests/ -v"},
    )

    preview = render_tool_call_preview(call, color=False)
    assert "run_terminal" in preview
    assert "$ pytest tests/ -v" in preview


@pytest.mark.asyncio
async def test_default_console_prompter_displays_preview_and_reads_yes() -> None:
    call = ToolCall(
        name="edit_file",
        arguments={
            "path": "config.py",
            "old_text": "debug = False",
            "new_text": "debug = True",
        },
    )

    stdin = io.StringIO("y\n")
    stdout = io.StringIO()

    approved = await _default_console_prompter(call, stdin, stdout)
    assert approved is True
    out = stdout.getvalue()
    assert "edit_file: config.py" in out
    assert "- debug = False" in out
    assert "+ debug = True" in out
    assert "Approve" in out


@pytest.mark.asyncio
async def test_default_console_prompter_reads_no() -> None:
    call = ToolCall(
        name="run_terminal",
        arguments={"command": "rm -rf /"},
    )

    stdin = io.StringIO("n\n")
    stdout = io.StringIO()

    approved = await _default_console_prompter(call, stdin, stdout)
    assert approved is False
