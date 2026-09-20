"""Persistence and precedence tests for token-saver settings."""

from __future__ import annotations

import json
from pathlib import Path

from avo.savers.config_store import (
    read_saver_setting,
    resolve_saver_setting,
    write_saver_setting,
)


def test_missing_or_corrupt_saver_setting_is_unset(tmp_path: Path) -> None:
    assert read_saver_setting(tmp_path) is None

    target = tmp_path / "saver.json"
    target.write_text("not-json", encoding="utf-8")
    assert read_saver_setting(tmp_path) is None


def test_write_and_remove_saver_setting(tmp_path: Path) -> None:
    target = write_saver_setting("full", tmp_path)
    assert target == tmp_path / "saver.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {"preset": "full"}
    assert read_saver_setting(tmp_path) == "full"

    assert write_saver_setting(None, tmp_path) is None
    assert not target.exists()
    assert read_saver_setting(tmp_path) is None


def test_environment_saver_wins_over_persisted_setting(tmp_path: Path) -> None:
    write_saver_setting("compact", tmp_path)
    assert resolve_saver_setting({"AVO_SAVER": "terse"}, tmp_path) == "terse"
    assert resolve_saver_setting({}, tmp_path) == "compact"
    assert resolve_saver_setting({"AVO_SAVER": "  "}, tmp_path) == "compact"
