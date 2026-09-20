"""Model-facing FunctionTools for workspace checkpoints and rollback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from .runner import WorkspaceSnapshot


class CreateCheckpointArguments(BaseModel):
    """Arguments for creating a workspace checkpoint."""

    name: str | None = Field(default=None, description="Optional label for the checkpoint")


class RollbackCheckpointArguments(BaseModel):
    """Arguments for rolling back to a workspace checkpoint."""

    tag: str = Field(min_length=1, description="Checkpoint tag or identifier to restore")


def create_checkpoint_tool(workspace_root: Path) -> PublicFunctionTool[CreateCheckpointArguments]:
    """Return tool for snapshotting workspace git state."""
    snapshot = WorkspaceSnapshot(workspace_root)

    async def _fn(args: CreateCheckpointArguments) -> dict[str, Any]:
        tag = snapshot.capture(name=args.name)
        return {
            "status": "created",
            "tag": tag,
            "message": f"Workspace checkpoint {tag!r} captured successfully.",
        }

    return PublicFunctionTool(
        name="create_checkpoint",
        description="Create a safety snapshot/checkpoint of workspace files before risky edits.",
        arguments_model=CreateCheckpointArguments,
        function=_fn,
    )


def rollback_checkpoint_tool(
    workspace_root: Path,
) -> PublicFunctionTool[RollbackCheckpointArguments]:
    """Return tool for reverting workspace to a previous checkpoint."""
    snapshot = WorkspaceSnapshot(workspace_root)

    async def _fn(args: RollbackCheckpointArguments) -> dict[str, Any]:
        success = snapshot.restore(args.tag)
        return {
            "status": "rolled_back" if success else "failed",
            "tag": args.tag,
            "success": success,
        }

    return PublicFunctionTool(
        name="rollback_checkpoint",
        description="Revert workspace files to a previously captured safety checkpoint.",
        arguments_model=RollbackCheckpointArguments,
        function=_fn,
    )
