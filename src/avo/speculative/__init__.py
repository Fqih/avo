"""Speculative execution, test-driven self-correction, and workspace checkpoints."""

from __future__ import annotations

from .runner import SpeculativeResult, SpeculativeRunner, WorkspaceSnapshot
from .tools import (
    CreateCheckpointArguments,
    RollbackCheckpointArguments,
    create_checkpoint_tool,
    rollback_checkpoint_tool,
)

__all__ = [
    "CreateCheckpointArguments",
    "RollbackCheckpointArguments",
    "SpeculativeResult",
    "SpeculativeRunner",
    "WorkspaceSnapshot",
    "create_checkpoint_tool",
    "rollback_checkpoint_tool",
]
