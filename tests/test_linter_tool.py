"""Tests for the lint tool and /lint REPL command."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.linter import LintArguments, lint_tool, run_linter
from avo.app_tools.workspace import Workspace
from avo.chat import run_repl


def test_run_linter_clean_workspace(tmp_path: Path) -> None:
    (tmp_path / "valid.py").write_text("print('valid')\n", encoding="utf-8")
    res = run_linter(tmp_path)
    assert res["ok"] is True
    assert res["issue_count"] == 0


def test_run_linter_syntax_error(tmp_path: Path) -> None:
    # Deliberate syntax error
    (tmp_path / "broken.py").write_text("def broken(\n", encoding="utf-8")
    res = run_linter(tmp_path, "broken.py")
    assert res["ok"] is False
    assert res["issue_count"] > 0
    assert len(res["issues"]) > 0


def test_run_linter_missing_file(tmp_path: Path) -> None:
    res = run_linter(tmp_path, "nonexistent.py")
    assert res["ok"] is False
    assert "Path not found" in res["issues"][0]


@pytest.mark.asyncio
async def test_lint_tool_invocation(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    (tmp_path / "clean.py").write_text("a = 42\n", encoding="utf-8")

    tool = lint_tool()
    with bind_workspace(ws):
        res = await tool.invoke(LintArguments(path="clean.py"))

    assert isinstance(res, dict)
    assert res["ok"] is True


def test_repl_lint_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    db = tmp_path / "test.db"

    env = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "llama3.1",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/lint\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = asyncio.run(
        run_repl(
            database_path=db,
            workspace_root=ws,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            environ=env,
        )
    )
    assert code == 0
    out = stdout.getvalue()
    assert "Linter" in out
    assert "passed cleanly" in out
