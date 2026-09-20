from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_posix_installer_has_valid_shell_syntax() -> None:
    for script_name in ("install.sh", "scripts/install.sh"):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / script_name)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{script_name} syntax error: {result.stderr}"


def test_posix_installer_dry_run_standalone_binary_is_zero_python() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mode: dry-run (standalone binary, zero-python)" in result.stdout
    assert "standalone binary (no Python runtime required)" in result.stdout
    assert "user-global (no root / no python runtime required)" in result.stdout
    assert "github.com/Fqih/avo/releases" in result.stdout
    assert "curl" not in result.stdout


def test_posix_installer_dry_run_from_source_uses_uv() -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "install.sh"),
            "--dry-run",
            "--from-source",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mode: dry-run (source / uv tool)" in result.stdout
    assert "avo[all]" in result.stdout
    assert "uv tool install" in result.stdout
    assert "user-global" in result.stdout


def test_posix_installer_accepts_package_and_python_overrides() -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "install.sh"),
            "--dry-run",
            "--package",
            ".[providers]",
            "--python",
            "3.12",
            "--no-update-shell",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "package: .[providers]" in result.stdout
    assert "python: 3.12" in result.stdout
    assert "uv tool update-shell" not in result.stdout


def test_powershell_installer_exposes_standalone_and_source_contracts() -> None:
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "[switch]$DryRun" in script
    assert "[switch]$FromSource" in script
    assert "standalone (zero Python runtime required)" in script
    assert "avo-install-" in script
    assert "avo[all]" in script
    assert '"tool", "install"' in script
    assert '"tool", "update-shell"' in script
    assert "user-global" in script


def test_build_standalone_check_only_runs_without_error() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_standalone.py"), "--check-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Avo Standalone Binary Builder" in result.stdout
    assert "[dry-run] Would build standalone binary:" in result.stdout


def test_all_extra_contains_runtime_integrations() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()

    all_section = pyproject.split("all = [", 1)[1].split("]", 1)[0]
    for dependency in (
        "avo-native",
        "httpx",
        "docker",
        "mcp",
        "opentelemetry-api",
        "langchain-core",
    ):
        assert dependency in all_section
