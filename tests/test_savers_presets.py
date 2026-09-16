"""Preset registry, built-in skill files, and SkillRegistry interop."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from avo.savers.pipeline import PipelineConfig, PreTrimConfig
from avo.savers.presets import (
    BUILTIN_PRESETS,
    builtin_skill_body,
    builtin_skills_root,
    resolve_saver,
)
from avo.skills import SkillRegistry

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def test_builtin_names_resolve() -> None:
    for name in ("terse", "yagni", "compact", "full"):
        preset = resolve_saver(name)
        assert preset is not None
        assert preset.name == name


def test_unknown_name_resolves_to_none() -> None:
    assert resolve_saver("bogus") is None
    assert resolve_saver("") is None


def test_preset_shapes_match_spec_table() -> None:
    terse = resolve_saver("terse")
    assert terse is not None and terse.skill_name == "caveman-terse"
    assert terse.pipeline is None

    yagni = resolve_saver("yagni")
    assert yagni is not None and yagni.skill_name == "ponytail-yagni"
    assert yagni.pipeline is None

    compact = resolve_saver("compact")
    assert compact is not None and compact.skill_name is None
    assert isinstance(compact.pipeline, PipelineConfig)
    assert compact.pipeline.pre_trim is None

    full = resolve_saver("full")
    assert full is not None and full.skill_name == "caveman-terse"
    assert full.pipeline is not None and full.pipeline.pre_trim is not None


def test_every_preset_has_description() -> None:
    for preset in BUILTIN_PRESETS.values():
        assert preset.description.strip()


def test_presets_are_frozen() -> None:
    preset = resolve_saver("terse")
    assert preset is not None
    with pytest.raises(ValidationError):
        preset.skill_name = None  # type: ignore[misc]


def test_skill_names_are_registry_slugs() -> None:
    for preset in BUILTIN_PRESETS.values():
        if preset.skill_name is not None:
            assert _NAME_PATTERN.match(preset.skill_name)


def test_skill_registry_walks_builtin_dir() -> None:
    registry = SkillRegistry(builtin_skills_root())
    assert registry.names() == ["caveman-terse", "ponytail-yagni"]
    assert registry.exists("caveman-terse")


def test_builtin_skill_body_loads() -> None:
    body = builtin_skill_body("caveman-terse")
    assert "Terse output" in body
    assert len(body) > 100
    other = builtin_skill_body("ponytail-yagni")
    assert "ladder" in other


def test_builtin_skill_body_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown built-in skill"):
        builtin_skill_body("nope")


def test_full_pipeline_pretrim_is_valid() -> None:
    full = BUILTIN_PRESETS["full"]
    assert full.pipeline is not None
    assert isinstance(full.pipeline.pre_trim, PreTrimConfig)
