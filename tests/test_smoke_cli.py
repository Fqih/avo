"""Smoke tests for Avo CLI and module entrypoints."""

from __future__ import annotations

import subprocess
import sys


def test_avo_module_help() -> None:
    res = subprocess.run(
        [sys.executable, "-m", "avo", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "Avo" in res.stdout or "usage:" in res.stdout.lower()


def test_avo_doctor_help() -> None:
    res = subprocess.run(
        [sys.executable, "-m", "avo", "doctor", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "doctor" in res.stdout.lower() or "usage:" in res.stdout.lower()


def test_avo_version_import() -> None:
    res = subprocess.run(
        [sys.executable, "-c", "import avo; assert avo.__version__"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
