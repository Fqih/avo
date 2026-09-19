"""Plugin discovery via Python entry points.

Third-party packages register tools, notifiers, or providers by adding
an entry-point group to their ``pyproject.toml``:

    [project.entry-points."avo.tools"]
    weather = "my_pkg:WeatherTool"

The runtime loads them at startup with :func:`discover` and the caller
filters / instantiates the discovered factories.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from avo.exceptions import AvoError

PluginError = AvoError

TOOL_GROUP = "avo.tools"
NOTIFIER_GROUP = "avo.notifiers"
PROVIDER_GROUP = "avo.providers"

ALL_GROUPS: tuple[str, ...] = (TOOL_GROUP, NOTIFIER_GROUP, PROVIDER_GROUP)


@dataclass(frozen=True)
class PluginEntry:
    """One discovered plugin."""

    group: str
    name: str
    factory: Any
    package: str | None = None

    def __call__(self) -> Any:
        """Invoke the factory — useful for callable plugins."""

        return self.factory()


def discover(group: str, *, package: str | None = None) -> tuple[PluginEntry, ...]:
    """Return all entries for ``group``, optionally filtered by package."""

    eps = metadata.entry_points()
    try:
        selected = eps.select(group=group)
    except AttributeError:  # pragma: no cover  — Python < 3.10 fallback
        selected = eps.get(group, ())  # type: ignore[arg-type]
    return _load_entries(selected, group=group, package=package)


def _load_entries(
    selected: Iterable[Any], *, group: str, package: str | None = None
) -> tuple[PluginEntry, ...]:
    entries: list[PluginEntry] = []
    for ep in selected:
        if package is not None and ep.dist and ep.dist.name != package:
            continue
        try:
            factory = ep.load()
        except Exception as exc:
            raise PluginError(
                f"failed to load plugin {ep.name!r} from group {group!r}: {exc}"
            ) from exc
        entries.append(
            PluginEntry(
                group=group,
                name=ep.name,
                factory=factory,
                package=ep.dist.name if ep.dist else None,
            )
        )
    return tuple(entries)


@contextmanager
def _private_import_path(paths: Iterable[Path]) -> Iterator[None]:
    """Temporarily expose private plugin environments to entry-point imports."""

    original = sys.path[:]
    private = [str(path.expanduser().resolve()) for path in paths]
    sys.path[:0] = [path for path in private if path not in sys.path]
    try:
        yield
    finally:
        sys.path[:] = original


def discover_from_paths(
    group: str,
    paths: Iterable[Path],
    *,
    package: str | None = None,
) -> tuple[PluginEntry, ...]:
    """Discover entry points from explicitly activated private environments.

    This deliberately avoids the host interpreter's global distributions. A
    plugin must be installed into one of ``paths`` and activated in Avo's
    plugin index before its code can be imported.
    """

    normalized = tuple(path.expanduser().resolve() for path in paths)
    if not normalized:
        return ()
    selected: list[Any] = []
    for distribution in metadata.distributions(path=[str(path) for path in normalized]):
        distribution_name = getattr(distribution, "name", None)
        if package is not None and distribution_name != package:
            continue
        for entry_point in distribution.entry_points:
            if entry_point.group == group:
                selected.append(entry_point)
    with _private_import_path(normalized):
        return _load_entries(selected, group=group, package=package)


def discover_all(*, groups: Iterable[str] = ALL_GROUPS) -> tuple[PluginEntry, ...]:
    """Discover across multiple groups."""

    collected: list[PluginEntry] = []
    for group in groups:
        collected.extend(discover(group))
    return tuple(collected)


def names(group: str, *, package: str | None = None) -> tuple[str, ...]:
    """Return just the registered names — cheap probe without imports."""

    eps = metadata.entry_points()
    try:
        selected = eps.select(group=group)
    except AttributeError:  # pragma: no cover  — Python < 3.10 fallback
        selected = eps.get(group, ())  # type: ignore[arg-type]
    out: list[str] = []
    for ep in selected:
        if package is not None and ep.dist and ep.dist.name != package:
            continue
        out.append(ep.name)
    return tuple(out)


__all__ = [
    "ALL_GROUPS",
    "NOTIFIER_GROUP",
    "PROVIDER_GROUP",
    "TOOL_GROUP",
    "PluginEntry",
    "PluginError",
    "discover",
    "discover_all",
    "discover_from_paths",
    "names",
]
