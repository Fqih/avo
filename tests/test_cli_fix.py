"""Tests for autonomous test repair engine (`avo fix` / `src/avo/cli_fix.py`)."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from avo import ModelResponse
from avo.cli_fix import run_cli_fix
from avo.providers.fake import FakeProvider


@pytest.fixture
def sample_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.mark.asyncio
async def test_run_cli_fix_when_already_passing(sample_git_repo: Path) -> None:
    out = io.StringIO()
    err = io.StringIO()

    with patch("avo.cli_fix.run_tests") as mock_run_tests:
        mock_run_tests.return_value = {
            "ok": True,
            "runner": "pytest",
            "target": "tests/test_ok.py",
            "summary": "1 passed",
            "failures": [],
            "output": "1 passed in 0.01s",
        }

        success = await run_cli_fix(
            workspace_root=sample_git_repo,
            database_path=sample_git_repo / "avo.db",
            target="tests/test_ok.py",
            stdout=out,
            stderr=err,
        )

        assert success is True
        assert "All tests are already passing" in out.getvalue()
        mock_run_tests.assert_called_once()


@pytest.mark.asyncio
async def test_run_cli_fix_repairs_failing_tests(sample_git_repo: Path) -> None:
    out = io.StringIO()
    err = io.StringIO()

    fake_provider = FakeProvider([ModelResponse(content="Fix applied to source code.")])

    with patch("avo.cli_fix.run_tests") as mock_run_tests:
        # First call fails, second call (after agent repair) passes
        mock_run_tests.side_effect = [
            {
                "ok": False,
                "runner": "pytest",
                "target": "tests/test_broken.py",
                "summary": "1 failed",
                "failures": ["AssertionError: expected 1 got 2"],
                "output": "FAILED tests/test_broken.py::test_calc",
            },
            {
                "ok": True,
                "runner": "pytest",
                "target": "tests/test_broken.py",
                "summary": "1 passed",
                "failures": [],
                "output": "1 passed in 0.02s",
            },
        ]

        success = await run_cli_fix(
            workspace_root=sample_git_repo,
            database_path=sample_git_repo / "avo.db",
            target="tests/test_broken.py",
            provider=fake_provider,
            auto_merge=True,
            stdout=out,
            stderr=err,
        )

        assert success is True
        assert "Tests failed" in out.getvalue()
        assert "Successfully resolved test failure" in out.getvalue()


def test_main_maps_fix_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from avo.cli import _parser, main

    parser = _parser()
    args, _ = parser.parse_known_args(["fix", "tests/test_foo.py"])
    assert args.command == "fix"
    assert args.target == "tests/test_foo.py"

    called_targets: list[str | None] = []

    async def mock_run_cli_fix(target: str | None = None, **_kwargs: object) -> bool:
        del _kwargs
        called_targets.append(target)
        return True

    monkeypatch.setattr("avo.cli_fix.run_cli_fix", mock_run_cli_fix)

    code = main(["fix", "tests/test_foo.py", "-d", str(tmp_path / "avo.db")])
    assert code == 0
    assert called_targets == ["tests/test_foo.py"]


@pytest.mark.asyncio
async def test_chat_slash_fix_dispatches_cleanly(
    sample_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from avo.chat import build_chat_context
    from avo.chat_commands import _run_slash

    ctx = build_chat_context(
        database_path=sample_git_repo / "avo.db",
        workspace_root=sample_git_repo,
        environ={
            "AVO_PROVIDER": "ollama",
            "AVO_MODEL": "llama3.1",
            "AVO_OLLAMA_BASE_URL": "http://example.invalid",
        },
    )

    out = io.StringIO()
    err = io.StringIO()

    called_fix: list[str | None] = []

    async def mock_run_cli_fix(
        *_args: object, target: str | None = None, **_kwargs: object
    ) -> bool:
        del _args, _kwargs
        called_fix.append(target)
        return True

    monkeypatch.setattr("avo.cli_fix.run_cli_fix", mock_run_cli_fix)

    exit_flag = await _run_slash(ctx, ["/fix", "tests/test_calc.py"], out, err, {})
    assert exit_flag is False
    assert called_fix == ["tests/test_calc.py"]
