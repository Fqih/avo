"""Named saver presets: prompt addendum skill + pipeline bundle (spec §6).

A preset is data — ``{skill_name?, pipeline?}`` — resolved by
name from :data:`BUILTIN_PRESETS`. Skill bodies load from the
:mod:`avo.skills_builtin` package data, so presets double as
installable skills ("presets as skills").
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from pydantic import ConfigDict

from avo.models import AvoModel
from avo.savers.pipeline import ElideConfig, PipelineConfig, PreTrimConfig


class SaverPreset(AvoModel):
    """One named token-saver configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    skill_name: str | None = None
    pipeline: PipelineConfig | None = None


_PIPELINE_DEFAULT = PipelineConfig()
_PIPELINE_FULL = PipelineConfig(
    elide=ElideConfig(),
    pre_trim=PreTrimConfig(),
)

BUILTIN_PRESETS: dict[str, SaverPreset] = {
    preset.name: preset
    for preset in (
        SaverPreset(
            name="terse",
            description="Terse-output prompt addendum; no message compression.",
            skill_name="caveman-terse",
        ),
        SaverPreset(
            name="yagni",
            description="YAGNI build-ladder prompt addendum; no message compression.",
            skill_name="ponytail-yagni",
        ),
        SaverPreset(
            name="compact",
            description="Deterministic message compression; prompt unchanged.",
            pipeline=_PIPELINE_DEFAULT,
        ),
        SaverPreset(
            name="full",
            description="Terse-output addendum plus compression with pre-trim enabled.",
            skill_name="caveman-terse",
            pipeline=_PIPELINE_FULL,
        ),
    )
}


def resolve_saver(name: str) -> SaverPreset | None:
    """Return the built-in preset ``name`` or ``None`` (case-sensitive)."""
    return BUILTIN_PRESETS.get(name)


def builtin_skills_root() -> Path:
    """Return the directory holding the built-in skill files."""
    return Path(str(resources.files("avo.skills_builtin")))


def builtin_skill_body(skill_name: str) -> str:
    """Return the body of a built-in skill; raises ``ValueError`` if unknown."""
    candidate = builtin_skills_root() / f"{skill_name}.md"
    if not candidate.is_file():
        msg = f"unknown built-in skill: {skill_name!r}"
        raise ValueError(msg)
    return candidate.read_text(encoding="utf-8")


__all__ = [
    "BUILTIN_PRESETS",
    "SaverPreset",
    "builtin_skill_body",
    "builtin_skills_root",
    "resolve_saver",
]
