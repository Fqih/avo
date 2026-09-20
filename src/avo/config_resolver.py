"""Canonical, typed resolution of Avo security configuration.

The resolver is deliberately independent from provider construction.  It gives
CLI, chat, delegated agents, plugins, and the local web UI one precedence and
one validation path without moving secrets into configuration files.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Generic, TypeVar

from avo.permissions import PermissionMode
from avo.permissions import parse_permission_mode as _parse_permission_mode

T = TypeVar("T")


class ConfigResolutionError(ValueError):
    """Raised when a security setting is invalid or unsafe to load."""


class ConfigSource(StrEnum):
    """Origin of a resolved setting, ordered from strongest to weakest."""

    CLI = "cli"
    ENVIRONMENT = "environment"
    PROJECT = "project"
    USER = "user"
    DEFAULT = "default"


@dataclass(frozen=True)
class ResolvedValue(Generic[T]):
    """A typed value together with the source and public config key."""

    value: T
    source: ConfigSource
    key: str


@dataclass(frozen=True)
class AvoSecurityConfig:
    """Resolved security posture consumed by execution boundaries."""

    permission_mode: ResolvedValue[PermissionMode]
    require_approval: ResolvedValue[tuple[str, ...]]
    sandbox_required: ResolvedValue[bool]
    sandbox_network: ResolvedValue[bool]
    sandbox_timeout_seconds: ResolvedValue[float]
    plugin_editable: ResolvedValue[bool]
    plugin_activation: ResolvedValue[bool]
    web_allowed_origin: ResolvedValue[str | None]
    web_cors_enabled: ResolvedValue[bool]

    def values(self) -> dict[str, object]:
        """Return plain values for consumers that do not need provenance."""

        return {
            name: resolved.value
            for name, resolved in self.__dict__.items()
            if isinstance(resolved, ResolvedValue)
        }

    def sources(self) -> dict[str, ConfigSource]:
        """Return the provenance of each resolved setting."""

        return {
            name: resolved.source
            for name, resolved in self.__dict__.items()
            if isinstance(resolved, ResolvedValue)
        }


_SETTING_KEYS: dict[str, str] = {
    "permission_mode": "AVO_PERMISSION_MODE",
    "require_approval": "AVO_TOOLS_REQUIRE_APPROVAL",
    "sandbox_required": "AVO_SANDBOX_REQUIRED",
    "sandbox_network": "AVO_SANDBOX_NETWORK",
    "sandbox_timeout_seconds": "AVO_SANDBOX_TIMEOUT_SECONDS",
    "plugin_editable": "AVO_PLUGIN_EDITABLE",
    "plugin_activation": "AVO_PLUGIN_ACTIVATION",
    "web_allowed_origin": "AVO_WEB_ALLOWED_ORIGIN",
    "web_cors_enabled": "AVO_WEB_CORS_ENABLED",
}

_DEFAULTS: dict[str, object] = {
    "permission_mode": PermissionMode.DEFAULT,
    "require_approval": (),
    "sandbox_required": True,
    "sandbox_network": False,
    "sandbox_timeout_seconds": 120.0,
    "plugin_editable": False,
    "plugin_activation": False,
    "web_allowed_origin": None,
    "web_cors_enabled": False,
}


def parse_permission_mode(value: object, *, source: str) -> PermissionMode:
    """Parse the canonical permission mode into a resolver error on failure."""

    try:
        return _parse_permission_mode(value, source=source)
    except ValueError as exc:
        raise ConfigResolutionError(str(exc)) from exc


def _config_candidates(root: Path, *, legacy: bool = False) -> tuple[Path, ...]:
    names = ("config.toml", "config.json")
    if legacy:
        return tuple(root / name for name in names)
    return tuple(root / name for name in names)


def _read_mapping(path: Path, *, source: ConfigSource) -> dict[str, object]:
    try:
        if path.suffix == ".toml":
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ConfigResolutionError(f"cannot read {source.value} config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigResolutionError(f"{source.value} config {path} must contain an object")
    security = data.get("security")
    if isinstance(security, dict):
        merged = dict(data)
        merged.update(security)
        data = merged
    return {str(key): value for key, value in data.items()}


def _safe_project_config(workspace_root: Path) -> Path | None:
    root = workspace_root.expanduser().resolve()
    config_dir = root / ".avo"
    if config_dir.is_symlink():
        resolved = config_dir.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ConfigResolutionError(
                f"project config directory must remain inside workspace {root}"
            ) from exc
    for path in _config_candidates(config_dir):
        if path.exists() or path.is_symlink():
            resolved = path.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ConfigResolutionError(
                    f"project config must remain inside workspace {root}"
                ) from exc
            return path
    return None


def _user_config_path(environ: Mapping[str, str], user_root: Path | None) -> Path | None:
    roots: tuple[Path, ...]
    if user_root is not None:
        roots = (user_root.expanduser(),)
    else:
        configured = environ.get("AVO_CONFIG_DIR", "").strip()
        if configured:
            roots = (Path(configured).expanduser(),)
        else:
            xdg = environ.get("XDG_CONFIG_HOME", "").strip()
            modern = Path(xdg).expanduser() / "avo" if xdg else Path.home() / ".config" / "avo"
            legacy = Path.home() / ".avo"
            roots = (modern, legacy)
    for root in roots:
        for path in _config_candidates(root):
            if path.exists() or path.is_symlink():
                return path
    return None


def _value_for(
    name: str,
    *,
    explicit: Mapping[str, object],
    environ: Mapping[str, str],
    project: Mapping[str, object],
    user: Mapping[str, object],
) -> tuple[object, ConfigSource, str]:
    env_key = _SETTING_KEYS[name]
    for source, mapping, keys in (
        (ConfigSource.CLI, explicit, (name, env_key)),
        (ConfigSource.ENVIRONMENT, environ, (env_key,)),
        (ConfigSource.PROJECT, project, (name, env_key)),
        (ConfigSource.USER, user, (name, env_key)),
    ):
        for key in keys:
            if key in mapping:
                val = mapping[key]
                if source is ConfigSource.PROJECT:
                    if name == "sandbox_required":
                        try:
                            if not _parse_bool(val, key=key, source=source):
                                continue
                        except Exception:
                            continue
                    elif name == "permission_mode":
                        if str(val).lower() in {"bypass", "bypass_permissions"}:
                            continue
                    elif name == "plugin_editable":
                        try:
                            if _parse_bool(val, key=key, source=source):
                                continue
                        except Exception:
                            continue
                return val, source, env_key
    return _DEFAULTS[name], ConfigSource.DEFAULT, env_key


def _parse_bool(value: object, *, key: str, source: ConfigSource) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigResolutionError(f"{key} from {source.value} must be a boolean; got {value!r}")


def _parse_timeout(value: object, *, key: str, source: ConfigSource) -> float:
    try:
        timeout = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigResolutionError(
            f"{key} from {source.value} must be a positive number; got {value!r}"
        ) from exc
    if not 0 < timeout <= 3600:
        raise ConfigResolutionError(
            f"{key} from {source.value} must be between 0 and 3600 seconds; got {value!r}"
        )
    return timeout


def _parse_approval(value: object, *, key: str, source: ConfigSource) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    raise ConfigResolutionError(
        f"{key} from {source.value} must be a comma-separated string or list of tool names"
    )


def _parse_origin(value: object, *, key: str, source: ConfigSource) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigResolutionError(f"{key} from {source.value} must be a URL or null")
    origin = value.strip()
    if not origin:
        return None
    if origin == "*":
        raise ConfigResolutionError(f"{key} from {source.value} cannot be wildcard '*' ")
    return origin


def resolve_security_config(
    *,
    explicit: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    workspace_root: Path | None = None,
    user_root: Path | None = None,
) -> AvoSecurityConfig:
    """Resolve security settings with CLI > env > project > user > default."""

    env = dict(os.environ if environ is None else environ)
    explicit_values = explicit or {}
    project: dict[str, object] = {}
    user: dict[str, object] = {}

    if workspace_root is not None:
        project_path = _safe_project_config(workspace_root)
        if project_path is not None:
            project = _read_mapping(project_path, source=ConfigSource.PROJECT)

    user_path = _user_config_path(env, user_root)
    if user_path is not None:
        user = _read_mapping(user_path, source=ConfigSource.USER)

    resolved: dict[str, ResolvedValue[object]] = {}
    for name in _SETTING_KEYS:
        raw, source, key = _value_for(
            name,
            explicit=explicit_values,
            environ=env,
            project=project,
            user=user,
        )
        if name == "permission_mode":
            value: object = parse_permission_mode(raw, source=source.value)
        elif name in {
            "sandbox_required",
            "sandbox_network",
            "plugin_editable",
            "plugin_activation",
            "web_cors_enabled",
        }:
            value = _parse_bool(raw, key=key, source=source)
        elif name == "sandbox_timeout_seconds":
            value = _parse_timeout(raw, key=key, source=source)
        elif name == "require_approval":
            value = _parse_approval(raw, key=key, source=source)
        else:
            value = _parse_origin(raw, key=key, source=source)
        resolved[name] = ResolvedValue(value=value, source=source, key=key)

    return AvoSecurityConfig(**resolved)  # type: ignore[arg-type]


def render_security_diagnostics(config: AvoSecurityConfig) -> str:
    """Render non-secret settings and provenance for ``avo doctor``."""

    values = config.values()
    lines: list[str] = []
    for name, source in config.sources().items():
        value = values[name]
        if name == "require_approval":
            safe_value = ",".join(value) if isinstance(value, tuple) else ""
        elif name == "web_allowed_origin":
            safe_value = str(value) if value else "(unset)"
        else:
            safe_value = str(value).lower() if isinstance(value, bool) else str(value)
        lines.append(f"{name}={safe_value} ({source.value})")
    return "\n".join(lines)


__all__ = [
    "AvoSecurityConfig",
    "ConfigResolutionError",
    "ConfigSource",
    "ResolvedValue",
    "parse_permission_mode",
    "render_security_diagnostics",
    "resolve_security_config",
]
