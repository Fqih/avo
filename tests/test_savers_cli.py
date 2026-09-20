"""Tests for the ``avo saver`` command."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.savers.cli import main


def test_saver_list_and_show_are_human_readable(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    assert main(["--config-dir", str(tmp_path), "list"]) == 0
    listed = capsys.readouterr().out
    assert "terse" in listed
    assert "compact" in listed
    assert "caveman-terse" in main_show(tmp_path, "terse", capsys)


def main_show(tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]) -> str:
    assert main(["--config-dir", str(tmp_path), "show", name]) == 0
    return capsys.readouterr().out


def test_saver_use_and_off_persist_operator_choice(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    assert main(["--config-dir", str(tmp_path), "use", "full"]) == 0
    assert (tmp_path / "saver.json").is_file()
    assert "restart" in capsys.readouterr().out.lower()

    assert main(["--config-dir", str(tmp_path), "off"]) == 0
    assert not (tmp_path / "saver.json").exists()
    assert "disabled" in capsys.readouterr().out.lower()


def test_saver_unknown_name_lists_valid_presets(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    assert main(["--config-dir", str(tmp_path), "use", "nope"]) == 2
    error = capsys.readouterr().err
    assert "terse" in error
    assert "compact" in error
