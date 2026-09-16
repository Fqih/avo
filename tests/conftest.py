"""Root test configuration and global fixtures."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

import avo.auth


@pytest.fixture(autouse=True)
def isolate_avo_config(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Path, None, None]:
    """Isolate AVO_CONFIG_DIR for every test so local developer credentials are not touched."""
    temp_dir = tmp_path_factory.mktemp("avo_test_config")
    monkeypatch.setenv("AVO_CONFIG_DIR", str(temp_dir))
    monkeypatch.setattr(avo.auth, "default_auth_dir", lambda: temp_dir)
    yield temp_dir
