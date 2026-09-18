"""Tests for named agent profiles and safe chat mention parsing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from avo.agent_profiles import (
    AgentCapability,
    AgentProfileError,
    AgentProfileRegistry,
)


def test_registry_exposes_stable_builtin_profiles(tmp_path: Path) -> None:
    registry = AgentProfileRegistry(tmp_path)

    profiles = registry.list()

    assert [profile.name for profile in profiles] == ["coder", "explore", "reviewer"]
    assert registry.get("explore").capability is AgentCapability.READ_ONLY
    assert registry.get("coder").capability is AgentCapability.INHERITED


def test_workspace_profile_overrides_builtin_and_is_bounded(tmp_path: Path) -> None:
    agents = tmp_path / ".avo" / "agents"
    agents.mkdir(parents=True)
    (agents / "explore.md").write_text(
        "Repository scout\n\nUse the workspace map before reading files.\n",
        encoding="utf-8",
    )
    (agents / "release.md").write_text(
        "Release assistant\n\nPrepare a release checklist.\n",
        encoding="utf-8",
    )

    registry = AgentProfileRegistry(tmp_path)

    explore = registry.get("explore")
    assert explore.description == "Repository scout"
    assert explore.system_prompt == "Use the workspace map before reading files."
    assert registry.get("release").builtin is False
    assert [profile.name for profile in registry.list()] == [
        "coder",
        "explore",
        "release",
        "reviewer",
    ]


def test_registry_ignores_invalid_profile_names_and_oversized_files(tmp_path: Path) -> None:
    agents = tmp_path / ".avo" / "agents"
    agents.mkdir(parents=True)
    (agents / "Bad Name.md").write_text("Bad\n\nPrompt", encoding="utf-8")
    (agents / "too-large.md").write_text("x" * 20, encoding="utf-8")

    registry = AgentProfileRegistry(tmp_path, max_profile_bytes=16)

    assert registry.get("bad-name") is None
    assert registry.get("too-large") is None
    assert any("too-large" in warning for warning in registry.warnings)


def test_registry_ignores_symlink_that_escapes_workspace(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable on this platform")
    agents = tmp_path / ".avo" / "agents"
    agents.mkdir(parents=True)
    outside = tmp_path.parent / "avo-agent-outside.md"
    outside.write_text("Outside\n\nDo not load me.\n", encoding="utf-8")
    link = agents / "outside.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    registry = AgentProfileRegistry(tmp_path)

    assert registry.get("outside") is None
    assert any("outside" in warning for warning in registry.warnings)


def test_parse_single_mention_returns_registered_agent_and_prompt(tmp_path: Path) -> None:
    registry = AgentProfileRegistry(tmp_path)

    request = registry.parse_prompt("@explore inspect the provider adapters")

    assert request is not None
    assert request.is_parallel is False
    assert request.parts[0].agent.name == "explore"
    assert request.parts[0].prompt == "inspect the provider adapters"


def test_parse_parallel_mentions_respects_quoted_pipes(tmp_path: Path) -> None:
    registry = AgentProfileRegistry(tmp_path)

    request = registry.parse_prompt('@explore say "one | two" | @reviewer inspect the latest diff')

    assert request is not None
    assert request.is_parallel is True
    assert [part.agent.name for part in request.parts] == ["explore", "reviewer"]
    assert request.parts[0].prompt == 'say "one | two"'
    assert request.parts[1].prompt == "inspect the latest diff"


@pytest.mark.parametrize(
    "text",
    [
        "please email @explore@example.com",
        "read @src/app.py",
        "@unknown inspect this",
        "ordinary prompt with no mention",
    ],
)
def test_unknown_mentions_and_attachment_paths_remain_normal_prompt(
    tmp_path: Path, text: str
) -> None:
    registry = AgentProfileRegistry(tmp_path)

    assert registry.parse_prompt(text) is None


def test_parse_rejects_empty_parallel_segment(tmp_path: Path) -> None:
    registry = AgentProfileRegistry(tmp_path)

    with pytest.raises(AgentProfileError, match="empty delegation segment"):
        registry.parse_prompt("@explore inspect |   ")


def test_parse_rejects_empty_agent_prompt(tmp_path: Path) -> None:
    registry = AgentProfileRegistry(tmp_path)

    with pytest.raises(AgentProfileError, match="prompt"):
        registry.parse_prompt("@explore")
