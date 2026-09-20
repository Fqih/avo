"""Model-facing FunctionTools for long-term epistemic memory."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from .store import FactStore


class RememberArguments(BaseModel):
    """Arguments for saving a long-term fact."""

    fact: str = Field(min_length=1, description="Fact content, guideline, or user preference")
    category: str = Field(
        default="project",
        description="Category: user | project | feedback | reference",
    )
    tags: list[str] = Field(default_factory=list, description="Optional keyword tags")


class RecallMemoryArguments(BaseModel):
    """Arguments for querying long-term facts."""

    query: str = Field(min_length=1, description="Search query or keyword")
    limit: int = Field(default=5, ge=1, le=20, description="Max facts to retrieve")


def remember_tool(store: FactStore) -> PublicFunctionTool[RememberArguments]:
    """Return tool for recording a persistent fact or user preference."""

    async def _fn(args: RememberArguments) -> dict[str, Any]:
        fact = store.remember(
            content=args.fact,
            category=args.category,
            tags=args.tags,
        )
        return {
            "status": "saved",
            "id": fact.id,
            "category": fact.category,
            "content": fact.content,
        }

    return PublicFunctionTool(
        name="remember",
        description=(
            "Persist a critical fact, user preference, or project decision across sessions."
        ),
        arguments_model=RememberArguments,
        function=_fn,
    )


def recall_memory_tool(store: FactStore) -> PublicFunctionTool[RecallMemoryArguments]:
    """Return tool for querying previously remembered facts."""

    async def _fn(args: RecallMemoryArguments) -> dict[str, Any]:
        results = store.recall(args.query, k=args.limit)
        return {
            "query": args.query,
            "count": len(results),
            "results": [
                {
                    "id": f.id,
                    "content": f.content,
                    "category": f.category,
                    "tags": f.tags,
                }
                for f in results
            ],
        }

    return PublicFunctionTool(
        name="recall_memory",
        description="Search persistent epistemic memory for user preferences or project decisions.",
        arguments_model=RecallMemoryArguments,
        function=_fn,
    )
