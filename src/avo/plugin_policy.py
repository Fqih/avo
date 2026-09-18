"""Explicit trust policy and metadata preview for Avo plugins."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from avo.exceptions import AvoError


class PluginPolicyError(AvoError):
    """Raised when plugin metadata or operator confirmation is unsafe."""


@dataclass(frozen=True)
class PluginManifest:
    """Metadata that can be displayed before any plugin mutation."""

    name: str
    version: str
    description: str
    groups: tuple[str, ...]
    source: str
    editable: bool = False


@dataclass(frozen=True)
class PluginPolicy:
    """Operator policy for install and activation mutations."""

    editable_allowed: bool = False
    activation_allowed: bool = False


def _name_from_source(source: str, name: str | None) -> str:
    value = name or source.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise PluginPolicyError(f"invalid plugin name {value!r}")
    return value


def inspect_plugin_source(source: str, *, name: str | None = None) -> PluginManifest:
    """Read plugin metadata without cloning, installing, or importing it."""

    if not source.strip():
        raise PluginPolicyError("plugin source must not be empty")
    plugin_name = _name_from_source(source, name)
    path = Path(source).expanduser()
    if not path.is_dir():
        return PluginManifest(
            name=plugin_name,
            version="unknown",
            description="remote source; metadata available after fetch",
            groups=(),
            source=source,
        )
    pyproject = path.resolve() / "pyproject.toml"
    if not pyproject.is_file():
        raise PluginPolicyError(f"plugin source has no pyproject.toml: {path.resolve()}")
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginPolicyError(f"could not inspect plugin metadata: {exc}") from exc
    project = data.get("project")
    if not isinstance(project, dict):
        raise PluginPolicyError("plugin pyproject.toml has no [project] table")
    project_name = project.get("name")
    version = project.get("version")
    description = project.get("description", "")
    entry_points = project.get("entry-points", {})
    groups = tuple(sorted(entry_points)) if isinstance(entry_points, dict) else ()
    manifest_name = str(project_name) if isinstance(project_name, str) else source
    return PluginManifest(
        name=_name_from_source(manifest_name, name),
        version=str(version) if isinstance(version, str) else "unknown",
        description=str(description) if isinstance(description, str) else "",
        groups=groups,
        source=source,
    )


def confirm_plugin_action(
    action: str,
    *,
    confirmation: str | None,
    interactive: bool,
) -> bool:
    """Accept only an exact operator confirmation token."""

    del interactive
    return confirmation == f"CONFIRM {action}"


__all__ = [
    "PluginManifest",
    "PluginPolicy",
    "PluginPolicyError",
    "confirm_plugin_action",
    "inspect_plugin_source",
]
