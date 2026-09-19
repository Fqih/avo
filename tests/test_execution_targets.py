"""Workspace and environment boundaries for executable helper tools."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from avo.app_tools.linter import run_linter
from avo.app_tools.test_runner import run_tests


def test_linter_rejects_target_outside_workspace_without_running_process(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    with patch("avo.app_tools.linter.subprocess.run") as run:
        result = run_linter(tmp_path, "../outside.py")

    assert result["ok"] is False
    assert "escapes workspace" in result["issues"][0]
    run.assert_not_called()


def test_linter_rejects_symlink_target_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    link.symlink_to(outside)

    with patch("avo.app_tools.linter.subprocess.run") as run:
        result = run_linter(tmp_path, "linked.py")

    assert result["ok"] is False
    assert "escapes workspace" in result["issues"][0]
    run.assert_not_called()


def test_test_runner_preserves_valid_pytest_node_selector(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "test_ok.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_one():\n    assert True\n", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=["pytest"], returncode=0, stdout="1 passed\n", stderr=""
    )

    with patch("avo.app_tools.test_runner.subprocess.run", return_value=completed) as run:
        result = run_tests(tmp_path, "tests/test_ok.py::test_one")

    assert result["ok"] is True
    command = run.call_args.args[0]
    assert command[-1] == "tests/test_ok.py::test_one"


def test_test_runner_rejects_absolute_outside_node_selector(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("def test_one():\n    pass\n", encoding="utf-8")

    with patch("avo.app_tools.test_runner.subprocess.run") as run:
        result = run_tests(tmp_path, f"{outside}::test_one")

    assert result["ok"] is False
    assert "escapes workspace" in result["failures"][0]
    run.assert_not_called()


def test_test_runner_does_not_forward_secret_environment_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AVO_API_KEY", "secret-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("SAFE_TEST_FLAG", "safe")
    completed = subprocess.CompletedProcess(
        args=["pytest"], returncode=0, stdout="1 passed\n", stderr=""
    )

    with patch("avo.app_tools.test_runner.subprocess.run", return_value=completed) as run:
        result = run_tests(tmp_path)

    assert result["ok"] is True
    environment = run.call_args.kwargs["env"]
    assert "AVO_API_KEY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "SAFE_TEST_FLAG" not in environment
    assert environment["PYTHONUNBUFFERED"] == "1"
