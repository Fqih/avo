"""Data models for epistemic memory and long-term facts."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class FactRecord(BaseModel):
    """An atomic learned fact or user preference."""

    id: str = Field(default_factory=lambda: secrets.token_hex(6))
    content: str = Field(min_length=1, description="Fact content")
    category: str = Field(
        default="project",
        description="Category: user | project | feedback | reference",
    )
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="user", description="Source: user | model | agent")
    session_id: str = Field(default="global")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
