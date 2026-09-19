"""Pydantic data models for Combo routing profiles and tiers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from avo.models import utc_now


class ComboTier(BaseModel):
    """One prioritized tier in a combo route."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0.0)
    cooldown_seconds: float = Field(default=60.0, ge=0.0)


class ComboProfile(BaseModel):
    """A named combo configuration consisting of ordered tiers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    strategy: str = Field(default="priority")
    tiers: list[ComboTier] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ComboCatalog(BaseModel):
    """File schema for ~/.config/avo/combos.json."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    combos: dict[str, ComboProfile] = Field(default_factory=dict)
