"""Persistent selection for the opt-in token-saver preset."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

from avo.auth import default_auth_dir


def _setting_path(config_dir: Path | str | None = None) -> Path:
    return Path(config_dir) if config_dir is not None else default_auth_dir()


def read_saver_setting(config_dir: Path | str | None = None) -> str | None:
    """Read the persisted preset name; malformed state is treated as unset."""

    path = _setting_path(config_dir) / "saver.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    preset = raw.get("preset")
    if not isinstance(preset, str) or not preset.strip():
        return None
    return preset.strip()


def resolve_saver_setting(
    environ: Mapping[str, str] | None = None,
    config_dir: Path | str | None = None,
) -> str | None:
    """Resolve ``AVO_SAVER`` over the persisted setting."""

    env = environ or {}
    selected = env.get("AVO_SAVER", "").strip()
    return selected or read_saver_setting(config_dir)


def write_saver_setting(
    preset: str | None,
    config_dir: Path | str | None = None,
) -> Path | None:
    """Persist ``preset`` or remove the setting when ``preset`` is ``None``."""

    directory = _setting_path(config_dir)
    path = directory / "saver.json"
    if preset is None:
        with suppress(FileNotFoundError):
            path.unlink()
        return None
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps({"preset": preset}, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


__all__ = ["read_saver_setting", "resolve_saver_setting", "write_saver_setting"]
