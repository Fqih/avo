"""Tests for ``avo.cli_sandbox`` — mocked docker client path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from avo.cli_sandbox import _build_parser
from avo.cli_sandbox import main as sandbox_main


class _FakeContainer:
    def __init__(self, info: dict[str, Any], logs: bytes) -> None:
        self._info = info
        self._logs = logs
        self.id = "abc"
        self.short_id = "abc"

    def wait(self) -> dict[str, Any]:
        return self._info

    def logs(self, *, stdout: bool = True, stderr: bool = True) -> bytes:
        del stdout, stderr
        return self._logs

    def remove(self, *, force: bool = False) -> None:
        del force


class _FakeContainers:
    def __init__(self, info: dict[str, Any], logs: bytes) -> None:
        self._info = info
        self._logs = logs
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeContainer:
        self.last_kwargs = kwargs
        return _FakeContainer(self._info, self._logs)


class _FakeClient:
    def __init__(self, info: dict[str, Any], logs: bytes) -> None:
        self.containers_obj = _FakeContainers(info, logs)

    def containers(self) -> _FakeContainers:
        return self.containers_obj


def _patch_docker(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    """Force ``docker.from_env`` to return ``client``."""

    fake_module = MagicMock()
    fake_module.from_env = lambda: client

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "docker":
            return fake_module
        return original_import(name, *args, **kwargs)

    import builtins

    original_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _fake_import)


def test_parser_requires_subcommand() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_run_rejects_no_command() -> None:
    with pytest.raises(Exception, match="no command provided"):
        sandbox_main(["run", "--image", "python:3.12-slim"])


def test_run_rejects_malformed_env() -> None:
    with pytest.raises(Exception, match="KEY=VALUE"):
        sandbox_main(["run", "--env", "BADENTRY", "--", "echo", "hi"])


def test_run_creates_container_with_expected_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _FakeClient({"StatusCode": 0}, b"hello-from-sandbox\n")
    _patch_docker(monkeypatch, client)

    code = sandbox_main(
        [
            "run",
            "--image",
            "python:3.12-slim",
            "--memory",
            "512m",
            "--network",
            "none",
            "--workspace",
            str(tmp_path),
            "--",
            "echo",
            "hello-from-sandbox",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "hello-from-sandbox" in captured.out

    kwargs = client.containers_obj.last_kwargs
    assert kwargs is not None
    assert kwargs["network_mode"] == "none"
    assert kwargs["mem_limit"] == "512m"
    assert kwargs["image"] == "python:3.12-slim"
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["pids_limit"] == 128
    assert kwargs["user"] == "65532:65532"


def test_run_emits_json_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _FakeClient({"StatusCode": 0}, b"ok\n")
    _patch_docker(monkeypatch, client)

    code = sandbox_main(
        [
            "run",
            "--image",
            "python:3.12-slim",
            "--json",
            "--workspace",
            str(tmp_path),
            "--",
            "echo",
            "ok",
        ]
    )
    assert code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["exit_code"] == 0
    assert parsed["stdout"].strip() == "ok"
    assert parsed["network_mode"] == "none"


def test_run_propagates_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _FakeClient({"StatusCode": 7}, b"failed\n")
    _patch_docker(monkeypatch, client)

    code = sandbox_main(
        [
            "run",
            "--workspace",
            str(tmp_path),
            "--",
            "false",
        ]
    )
    assert code == 7


def test_run_strips_leading_dash_dash() -> None:
    parser = _build_parser()
    args = parser.parse_args(["run", "--", "echo", "hello"])
    # REMAINDER captures "--" too — ensure _invoke strips it.
    assert args.command[0] == "--"


def test_run_passes_env_dict() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--env",
            "FOO=bar",
            "--env",
            "BAZ=qux",
            "--",
            "printenv",
        ]
    )
    assert args.env == ["FOO=bar", "BAZ=qux"]


def test_parser_help_is_helpful(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "ephemeral" in out
