from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_posix_installer_has_valid_shell_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "install.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_posix_installer_dry_run_is_network_free_and_global() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mode: dry-run" in result.stdout
    assert "avo[all]" in result.stdout
    assert "uv tool install" in result.stdout
    assert "user-global" in result.stdout
    assert "curl" not in result.stdout


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


def test_powershell_installer_exposes_dry_run_and_global_install_contract() -> None:
    script = (ROOT / "install.ps1").read_text()

    assert "[switch]$DryRun" in script
    assert "avo[all]" in script
    assert '"tool", "install"' in script
    assert '"tool", "update-shell"' in script
    assert "user-global" in script


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
