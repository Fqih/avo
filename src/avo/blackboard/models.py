"""Models for multi-agent blackboard memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class BlackboardEntry:
    """A typed entry stored on the shared blackboard."""

    key: str
    value: Any
    namespace: str = "default"
    author: str = "anonymous"
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
