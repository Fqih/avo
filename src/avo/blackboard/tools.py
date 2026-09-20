"""Model-facing FunctionTools for shared multi-agent blackboard memory."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, JsonValue

from avo import FunctionTool as PublicFunctionTool

from .store import BlackboardStore


class BlackboardSetArguments(BaseModel):
    """Arguments to store a value on the shared blackboard."""

    key: str = Field(min_length=1, description="Key identifier for the item")
    value: JsonValue = Field(description="JSON value or structured payload to store")
    namespace: str = Field(default="default", description="Logical partition/namespace")


class BlackboardGetArguments(BaseModel):
    """Arguments to retrieve a value from the shared blackboard."""

    key: str = Field(min_length=1, description="Key identifier to look up")
    namespace: str = Field(default="default", description="Logical partition/namespace")


class BlackboardListArguments(BaseModel):
    """Arguments to list keys and entries on the shared blackboard."""

    namespace: str = Field(default="default", description="Logical partition/namespace")
    prefix: str | None = Field(default=None, description="Optional key prefix filter")


def blackboard_set_tool(
    store: BlackboardStore,
    author: str = "agent",
) -> PublicFunctionTool[BlackboardSetArguments]:
    """Return FunctionTool for writing to blackboard."""

    async def _fn(args: BlackboardSetArguments) -> dict[str, Any]:
        entry = await store.set(
            key=args.key,
            value=args.value,
            namespace=args.namespace,
            author=author,
        )
        return {
            "key": entry.key,
            "namespace": entry.namespace,
            "version": entry.version,
            "author": entry.author,
            "status": "stored",
        }

    return PublicFunctionTool(
        name="blackboard_set",
        description="Store or update a structured key-value item on the shared blackboard.",
        arguments_model=BlackboardSetArguments,
        function=_fn,
    )


def blackboard_get_tool(store: BlackboardStore) -> PublicFunctionTool[BlackboardGetArguments]:
    """Return FunctionTool for reading from blackboard."""

    async def _fn(args: BlackboardGetArguments) -> dict[str, Any]:
        entry = await store.get(key=args.key, namespace=args.namespace)
        if not entry:
            return {
                "key": args.key,
                "namespace": args.namespace,
                "found": False,
                "value": None,
            }
        return {
            "key": entry.key,
            "namespace": entry.namespace,
            "found": True,
            "version": entry.version,
            "author": entry.author,
            "value": entry.value,
        }

    return PublicFunctionTool(
        name="blackboard_get",
        description="Retrieve a structured item by key from the shared multi-agent blackboard.",
        arguments_model=BlackboardGetArguments,
        function=_fn,
    )


def blackboard_list_tool(store: BlackboardStore) -> PublicFunctionTool[BlackboardListArguments]:
    """Return FunctionTool for listing items on blackboard."""

    async def _fn(args: BlackboardListArguments) -> dict[str, Any]:
        entries = await store.list_entries(namespace=args.namespace, prefix=args.prefix)
        return {
            "namespace": args.namespace,
            "count": len(entries),
            "entries": [
                {
                    "key": e.key,
                    "version": e.version,
                    "author": e.author,
                    "value": e.value,
                }
                for e in entries
            ],
        }

    return PublicFunctionTool(
        name="blackboard_list",
        description="List stored keys and values on the shared multi-agent blackboard.",
        arguments_model=BlackboardListArguments,
        function=_fn,
    )
