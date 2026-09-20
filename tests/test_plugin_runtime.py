"""Tests for isolated plugin installation metadata."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from avo.cli_plugins import active_plugin_site_packages, install, set_active


def _plugin_source(root: Path) -> Path:
    source = root / "demo-plugin"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        """[project]
name = 'demo-plugin'
version = '1.2.3'
description = 'demo'
[project.entry-points.'avo.tools']
demo = 'demo:register'
""",
        encoding="utf-8",
    )
    (source / "demo.py").write_text("def register(): return ()\n", encoding="utf-8")
    return source


def test_install_records_digest_and_does_not_use_active_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import avo.cli_plugins as plugins

    source = _plugin_source(tmp_path)
    plugin_root = tmp_path / "plugins"
    monkeypatch.setattr(plugins, "PLUGIN_ROOT", plugin_root)
    monkeypatch.setattr(plugins, "PLUGIN_INDEX", plugin_root / "index.json")
    calls: list[tuple[Path, Path, bool]] = []

    def fake_install(target: Path, *, environment: Path, editable: bool) -> None:
        calls.append((target, environment, editable))
        environment.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(plugins, "_pip_install_isolated", fake_install)

    installed = install(str(source), confirm=True)

    index = json.loads((plugin_root / "index.json").read_text(encoding="utf-8"))
    record = index[installed.name]
    assert calls == [(plugin_root / "demo-plugin", plugin_root / ".venvs" / "demo-plugin", False)]
    assert record["environment"] == str(plugin_root / ".venvs" / "demo-plugin")
    assert record["source_digest"].startswith("sha256:")
    assert record["active"] is False


def test_activation_is_explicit_and_returns_private_site_packages(
    tmp_path: Path, monkeypatch
) -> None:
    import avo.cli_plugins as plugins

    plugin_root = tmp_path / "plugins"
    environment = plugin_root / ".venvs" / "demo"
    target = plugin_root / "demo"
    environment.mkdir(parents=True)
    target.mkdir()
    (plugin_root / "index.json").parent.mkdir(parents=True, exist_ok=True)
    (plugin_root / "index.json").write_text(
        json.dumps(
            {
                "demo": {
                    "source": "local",
                    "path": str(target),
                    "environment": str(environment),
                    "editable": False,
                    "source_digest": "sha256:test",
                    "active": False,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(plugins, "PLUGIN_ROOT", plugin_root)
    monkeypatch.setattr(plugins, "PLUGIN_INDEX", plugin_root / "index.json")

    assert active_plugin_site_packages() == ()
    plugin = set_active("demo", active=True)

    assert plugin.active is True
    expected = (
        environment
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    assert active_plugin_site_packages() == (expected,)


def test_remove_deletes_isolated_environment(tmp_path: Path, monkeypatch) -> None:
    import avo.cli_plugins as plugins

    plugin_root = tmp_path / "plugins"
    environment = plugin_root / ".venvs" / "demo"
    target = plugin_root / "demo"
    environment.mkdir(parents=True)
    target.mkdir(parents=True)
    plugin_root.mkdir(exist_ok=True)
    (plugin_root / "index.json").write_text(
        json.dumps(
            {
                "demo": {
                    "source": "local",
                    "path": str(target),
                    "environment": str(environment),
                    "editable": False,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(plugins, "PLUGIN_ROOT", plugin_root)
    monkeypatch.setattr(plugins, "PLUGIN_INDEX", plugin_root / "index.json")

    plugins.remove("demo", uninstall=False)

    assert not target.exists()
    assert not environment.exists()
