"""Tests for the test_runner tool and /test REPL command."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.test_runner import (
    TestRunnerArguments,
    run_tests,
)
from avo.app_tools.test_runner import (
    test_runner_tool as make_test_runner_tool,
)
from avo.app_tools.workspace import Workspace
from avo.chat import run_repl
from avo.exceptions import ToolExecutionError


def test_run_tests_passing(tmp_path: Path) -> None:
    test_file = tmp_path / "test_ok.py"
    test_file.write_text(
        "def test_simple():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    res = run_tests(tmp_path, target="test_ok.py")
    assert res["ok"] is True
    assert res["returncode"] == 0
    assert "passed" in res["summary"].lower()


def test_run_tests_failing(tmp_path: Path) -> None:
    test_file = tmp_path / "test_bad.py"
    test_file.write_text(
        "def test_broken():\n    assert 1 == 2\n",
        encoding="utf-8",
    )
    res = run_tests(tmp_path, target="test_bad.py")
    assert res["ok"] is False
    assert res["returncode"] != 0
    assert len(res["failures"]) >= 1


def test_run_tests_timeout(tmp_path: Path) -> None:
    expired = subprocess.TimeoutExpired(cmd=["pytest"], timeout=1.0)
    with patch("subprocess.run", side_effect=expired):
        res = run_tests(tmp_path, timeout_seconds=1.0)
        assert res["ok"] is False
        assert "timed out" in res["summary"].lower()


def test_run_tests_generic_exception(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=OSError("binary not found")):
        res = run_tests(tmp_path)
        assert res["ok"] is False
        assert "binary not found" in res["summary"]


@pytest.mark.asyncio
async def test_test_runner_tool_invocation(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        "def test_add():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )

    tool = make_test_runner_tool()
    with bind_workspace(ws):
        res = await tool.invoke(TestRunnerArguments(target="test_sample.py"))

    assert isinstance(res, dict)
    assert res["ok"] is True
    assert res["target"] == "test_sample.py"


@pytest.mark.asyncio
async def test_test_runner_tool_without_workspace_raises() -> None:
    tool = make_test_runner_tool()
    with pytest.raises(ToolExecutionError, match="without an active workspace"):
        await tool.invoke(TestRunnerArguments())


def test_repl_test_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    ws = tmp_path / "ws"
    ws.mkdir()
    test_file = ws / "test_mini.py"
    test_file.write_text("def test_ok():\n    pass\n", encoding="utf-8")
    db = tmp_path / "test.db"

    env = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "llama3.1",
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/test test_mini.py\n/quit\n")
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
    assert "Running test suite" in out
    assert "passed cleanly" in out
