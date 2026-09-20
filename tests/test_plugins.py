"""Tests for the plugin loader."""

from __future__ import annotations

import types
from importlib import metadata
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from avo.plugins import (
    ALL_GROUPS,
    NOTIFIER_GROUP,
    PROVIDER_GROUP,
    TOOL_GROUP,
    PluginError,
    discover,
    discover_all,
    discover_from_paths,
    names,
)


@pytest.fixture
def fake_entry_points(monkeypatch: MonkeyPatch) -> list[tuple[str, str, object, str | None]]:
    captured: list[tuple[str, str, object, str | None]] = []

    class FakeEntryPoint:
        def __init__(self, name: str, group: str, factory: object, pkg: str | None) -> None:
            self.name = name
            self.group = group
            self.factory = factory
            self.dist_name = pkg
            self.dist = types.SimpleNamespace(name=pkg) if pkg else None

        def load(self) -> object:
            for grp, nm, factory, _pkg in captured:
                if self.name == nm and self.group == grp:
                    return factory
            raise KeyError("entry not found")

    def fake_entry_points_select(group: str) -> list[FakeEntryPoint]:
        return [FakeEntryPoint(nm, grp, fac, pkg) for grp, nm, fac, pkg in captured if grp == group]

    def fake_entry_points_get(group: str, default: object) -> list[FakeEntryPoint]:
        result = [
            FakeEntryPoint(nm, grp, fac, pkg) for grp, nm, fac, pkg in captured if grp == group
        ]
        return result if result else default  # type: ignore[return-value]

    class FakeEntryPoints:
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return fake_entry_points_select(group)

        def get(self, group: str, default: object = None) -> list[FakeEntryPoint]:
            return fake_entry_points_get(group, default)

    monkeypatch.setattr(metadata, "entry_points", lambda: FakeEntryPoints())
    return captured


def test_discover_returns_registered_plugins(
    fake_entry_points: list[tuple[str, str, object, str | None]],
) -> None:
    fake_entry_points.append((TOOL_GROUP, "weather", lambda: "weather factory", "my_pkg"))
    result = discover(TOOL_GROUP)
    assert len(result) == 1
    assert result[0].name == "weather"
    assert result[0].group == TOOL_GROUP
    assert result[0].package == "my_pkg"
    assert result[0].factory() == "weather factory"


def test_discover_filters_by_package(
    fake_entry_points: list[tuple[str, str, object, str | None]],
) -> None:
    fake_entry_points.extend(
        [
            (TOOL_GROUP, "a", lambda: 1, "pkg_a"),
            (TOOL_GROUP, "b", lambda: 2, "pkg_b"),
        ]
    )
    result = discover(TOOL_GROUP, package="pkg_a")
    assert len(result) == 1
    assert result[0].name == "a"


def test_discover_all_collects_across_groups(
    fake_entry_points: list[tuple[str, str, object, str | None]],
) -> None:
    fake_entry_points.extend(
        [
            (TOOL_GROUP, "tool1", lambda: "t", "pkg_a"),
            (NOTIFIER_GROUP, "notif1", lambda: "n", "pkg_b"),
            (PROVIDER_GROUP, "prov1", lambda: "p", "pkg_c"),
        ]
    )
    result = discover_all()
    assert len(result) == 3
    groups = {e.group for e in result}
    assert groups == {TOOL_GROUP, NOTIFIER_GROUP, PROVIDER_GROUP}


def test_discover_wraps_load_failure_as_plugin_error(
    fake_entry_points: list[tuple[str, str, object, str | None]],
) -> None:
    def broken() -> None:
        raise RuntimeError("boom")

    fake_entry_points.append((TOOL_GROUP, "broken", broken, "pkg"))

    class BadEntryPoint:
        name = "broken"
        group = TOOL_GROUP
        dist = types.SimpleNamespace(name="pkg")

        def load(self) -> None:
            raise ImportError("missing dep")

        def select(self, *, group: str) -> list[BadEntryPoint]:
            return [self] if group == self.group else []

    import avo.plugins as mod

    mp = MonkeyPatch()
    mp.setattr(mod.metadata, "entry_points", lambda: BadEntryPoint())
    try:
        with pytest.raises(PluginError, match="failed to load"):
            discover(TOOL_GROUP)
    finally:
        mp.undo()


def test_names_returns_names_without_loading(
    fake_entry_points: list[tuple[str, str, object, str | None]],
) -> None:
    fake_entry_points.extend(
        [
            (TOOL_GROUP, "alpha", lambda: 1, "pkg"),
            (TOOL_GROUP, "beta", lambda: 2, "pkg"),
        ]
    )
    result = names(TOOL_GROUP)
    assert result == ("alpha", "beta")


def test_names_filters_by_package(
    fake_entry_points: list[tuple[str, str, object, str | None]],
) -> None:
    fake_entry_points.extend(
        [
            (TOOL_GROUP, "a", lambda: 1, "pkg_a"),
            (TOOL_GROUP, "b", lambda: 2, "pkg_b"),
        ]
    )
    result = names(TOOL_GROUP, package="pkg_b")
    assert result == ("b",)


def test_plugin_entry_call_invokes_factory() -> None:
    def factory() -> int:
        return 42

    class E:
        def __init__(self) -> None:
            self.factory = factory

        def __call__(self) -> int:
            return self.factory()

    assert E()() == 42


def test_all_groups_constant() -> None:
    assert ALL_GROUPS == (TOOL_GROUP, NOTIFIER_GROUP, PROVIDER_GROUP)


def test_discover_empty_group_returns_empty(
    fake_entry_points: list[tuple[str, str, object, str | None]],
) -> None:
    result = discover(TOOL_GROUP)
    assert result == ()


def test_discover_from_private_paths_loads_only_selected_distributions(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    imported = tmp_path / "private_site"
    imported.mkdir()
    observed: list[list[str]] = []

    class EntryPoint:
        name = "private_tool"
        group = TOOL_GROUP
        dist = types.SimpleNamespace(name="private-pkg")

        def load(self) -> object:
            observed.append(list(__import__("sys").path))
            return lambda: "private"

    class Distribution:
        name = "private-pkg"
        entry_points = (EntryPoint(),)

    monkeypatch.setattr(metadata, "distributions", lambda *, path: (Distribution(),))

    result = discover_from_paths(TOOL_GROUP, (imported,))

    assert result[0].package == "private-pkg"
    assert str(imported) in observed[0]
