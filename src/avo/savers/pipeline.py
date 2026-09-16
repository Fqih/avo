"""Ordered stage pipeline + frozen config models (spec §3).

Order is fixed and cheapest-first:
``json_minify → dedupe_tool_results → elide_verbose_output → pre_trimmer``.
``pre_trimmer`` runs last so its token estimate sees post-compression
sizes; it is ``None`` unless explicitly configured.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ConfigDict, Field, model_validator

from avo.models import AvoModel
from avo.savers.stages import (
    DedupeToolResultsStage,
    ElideVerboseOutputStage,
    JsonMinifyStage,
    Messages,
    PreTrimmerStage,
    SaverStage,
    estimate_messages,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ElideConfig(AvoModel):
    """Settings for :class:`ElideVerboseOutputStage`."""

    model_config = _FROZEN

    max_lines: int = Field(default=80, ge=1)
    keep_first: int = Field(default=20, ge=1)
    keep_last: int = Field(default=20, ge=1)


class PreTrimConfig(AvoModel):
    """Settings for :class:`PreTrimmerStage`; fails closed on bad bounds."""

    model_config = _FROZEN

    trigger_tokens: int = Field(default=6000, ge=1)
    target_tokens: int = Field(default=4500, ge=1)
    keep_recent: int = Field(default=8, ge=1)
    tool_content_max_chars: int = Field(default=1200, ge=1)

    @model_validator(mode="after")
    def target_not_above_trigger(self) -> PreTrimConfig:
        if self.target_tokens > self.trigger_tokens:
            msg = (
                f"target_tokens {self.target_tokens} must not exceed "
                f"trigger_tokens {self.trigger_tokens}"
            )
            raise ValueError(msg)
        return self


class PipelineConfig(AvoModel):
    """Which stages run and with what settings."""

    model_config = _FROZEN

    json_minify: bool = True
    dedupe: bool = True
    elide: ElideConfig | None = Field(default_factory=ElideConfig)
    pre_trim: PreTrimConfig | None = None


@dataclass(frozen=True)
class PipelineResult:
    """Output of one pipeline pass with applied-stage accounting."""

    messages: Messages
    stages_applied: tuple[str, ...]
    tokens_before: int
    tokens_after: int


def _stages_for(config: PipelineConfig) -> list[SaverStage]:
    stages: list[SaverStage] = []
    if config.json_minify:
        stages.append(JsonMinifyStage())
    if config.dedupe:
        stages.append(DedupeToolResultsStage())
    if config.elide is not None:
        stages.append(
            ElideVerboseOutputStage(
                max_lines=config.elide.max_lines,
                keep_first=config.elide.keep_first,
                keep_last=config.elide.keep_last,
            )
        )
    if config.pre_trim is not None:
        stages.append(
            PreTrimmerStage(
                trigger_tokens=config.pre_trim.trigger_tokens,
                target_tokens=config.pre_trim.target_tokens,
                keep_recent=config.pre_trim.keep_recent,
                tool_content_max_chars=config.pre_trim.tool_content_max_chars,
            )
        )
    return stages


def run_pipeline(messages: Messages, config: PipelineConfig) -> PipelineResult:
    """Apply the configured stages in fixed order; never mutate the input."""
    tokens_before = estimate_messages(messages)
    current = list(messages)
    applied: list[str] = []
    for stage in _stages_for(config):
        result = stage.apply(current)
        if result != current:
            applied.append(stage.name)
            current = result
    return PipelineResult(
        messages=current,
        stages_applied=tuple(applied),
        tokens_before=tokens_before,
        tokens_after=estimate_messages(current),
    )


__all__ = [
    "ElideConfig",
    "PipelineConfig",
    "PipelineResult",
    "PreTrimConfig",
    "run_pipeline",
]
