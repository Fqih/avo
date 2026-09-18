"""Typed tool definitions, validation, invocation, and fingerprints."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Generic, Protocol, TypeVar, cast, runtime_checkable

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from avo.capabilities import ToolCapability, classify_tool
from avo.exceptions import (
    DuplicateToolError,
    ToolAlreadyCompletedError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from avo.models import ToolCall, ToolMetadata, ToolResult, utc_now

ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)
ToolCallable = Callable[[ArgumentsT], JsonValue | Awaitable[JsonValue]]
_JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


@runtime_checkable
class Tool(Protocol):
    """A validated async-compatible tool."""

    @property
    def metadata(self) -> ToolMetadata:
        """Return model-facing tool metadata."""

    async def invoke(self, arguments: dict[str, JsonValue]) -> JsonValue:
        """Validate and invoke this tool."""


class FunctionTool(Generic[ArgumentsT]):
    """Adapt a typed Pydantic input model and callable into a Tool."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        arguments_model: type[ArgumentsT],
        function: ToolCallable[ArgumentsT],
        capability: ToolCapability | None = None,
    ) -> None:
        self._arguments_model = arguments_model
        self._function = function
        self._metadata = ToolMetadata(
            name=name,
            description=description,
            input_schema=cast(dict[str, JsonValue], arguments_model.model_json_schema()),
            capability=capability or classify_tool(name),
        )

    @property
    def metadata(self) -> ToolMetadata:
        """Return the immutable name, description, and argument schema."""

        return self._metadata.model_copy(deep=True)

    async def invoke(self, arguments: dict[str, JsonValue]) -> JsonValue:
        """Validate arguments, invoke the function, and require JSON-safe output."""

        try:
            parsed = self._arguments_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolValidationError(
                f"Invalid arguments for tool {self._metadata.name!r}: {exc}"
            ) from exc

        try:
            value = self._function(parsed)
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            raise ToolExecutionError(
                f"Tool {self._metadata.name!r} raised {type(exc).__name__}: {exc}"
            ) from exc

        try:
            return _JSON_ADAPTER.validate_python(value)
        except ValidationError as exc:
            raise ToolValidationError(
                f"Tool {self._metadata.name!r} returned a non-JSON value: {exc}"
            ) from exc


def _enforce_strict_object(node: dict[str, Any]) -> dict[str, Any]:
    """Recursively set ``additionalProperties: false`` on every object schema.

    OpenAI's strict function-calling mode rejects tool payloads whose
    nested object schemas allow extra properties. Pydantic sets the
    flag on the root, but nested ``$defs`` and ``items`` nodes are left
    permissive unless we walk them.
    """

    if node.get("type") == "object" or "properties" in node:
        node["additionalProperties"] = False
        properties = node.get("properties")
        if isinstance(properties, dict):
            for child in properties.values():
                if isinstance(child, dict):
                    _enforce_strict_object(child)
    for key in ("$defs", "definitions"):
        defs = node.get(key)
        if isinstance(defs, dict):
            for child in defs.values():
                if isinstance(child, dict):
                    _enforce_strict_object(child)
    for key in ("items", "additionalProperties"):
        child = node.get(key)
        if isinstance(child, dict):
            _enforce_strict_object(child)
    for key in ("anyOf", "oneOf", "allOf"):
        variants = node.get(key)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    _enforce_strict_object(variant)
    return node


def to_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Export a Pydantic model to a JSON Schema 2020-12 document.

    The returned dictionary is safe to feed into OpenAI's
    ``function.parameters``, Anthropic's ``tool.input_schema``, or any
    MCP server that accepts JSON Schema over JSON-RPC. Nested object
    schemas have ``additionalProperties: false`` set so the document
    satisfies OpenAI's strict function-calling mode without further
    post-processing.
    """

    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    _enforce_strict_object(schema)
    return schema


def to_openai_function(tool_name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    """Render a strict OpenAI function-calling entry.

    The returned dictionary has the shape expected by the
    ``ChatCompletions`` API::

        {
            "type": "function",
            "function": {
                "name": ...,
                "description": ...,
                "parameters": ...,
                "strict": True,
            },
        }

    The ``parameters`` field is a JSON Schema document produced by
    :func:`to_json_schema`, with ``additionalProperties: false`` set
    on every nested object so OpenAI's strict validator accepts the
    payload.
    """

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": to_json_schema(model),
            "strict": True,
        },
    }


def to_anthropic_tool(tool_name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    """Render an Anthropic ``tools`` entry with ``input_schema``.

    The returned dictionary has the shape expected by the Anthropic
    Messages API::

        {
            "name": ...,
            "description": ...,
            "input_schema": ...,
        }
    """

    return {
        "name": tool_name,
        "description": description,
        "input_schema": to_json_schema(model),
    }


def canonical_fingerprint(value: JsonValue) -> str:
    """Return a stable SHA-256 fingerprint for a JSON value."""

    try:
        normalized = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ToolValidationError(f"Value cannot be normalized as JSON: {exc}") from exc
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def tool_call_fingerprint(call: ToolCall) -> str:
    """Fingerprint normalized tool name and arguments, excluding call ID."""

    return canonical_fingerprint({"name": call.name, "arguments": call.arguments})


def tool_result_fingerprint(result: ToolResult) -> str:
    """Fingerprint stable result content while excluding timestamps and call ID."""

    return canonical_fingerprint(
        {
            "tool_name": result.tool_name,
            "success": result.success,
            "output": result.output,
            "error": result.error,
        }
    )


class ToolRegistry:
    """Name-indexed tool collection with idempotent invocation checks."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools:
            self.register(item)

    def register(self, item: Tool) -> None:
        """Register one tool and reject duplicate names."""

        name = item.metadata.name
        if name in self._tools:
            raise DuplicateToolError(
                f"Tool name {name!r} is already registered; tool names must be unique."
            )
        self._tools[name] = item

    @property
    def metadata(self) -> list[ToolMetadata]:
        """Return model-facing metadata in registration order."""

        return [item.metadata for item in self._tools.values()]

    def get(self, name: str) -> Tool:
        """Return a registered tool or raise a specific error."""

        try:
            return self._tools[name]
        except KeyError as exc:
            available = ", ".join(self._tools) or "(none)"
            raise ToolNotFoundError(
                f"Tool {name!r} is not registered. Available tools: {available}."
            ) from exc

    async def invoke(
        self,
        call: ToolCall,
        *,
        completed_tool_call_ids: set[str],
    ) -> ToolResult:
        """Invoke a call once and normalize success or expected failure details."""

        if call.tool_call_id in completed_tool_call_ids:
            raise ToolAlreadyCompletedError(
                f"Tool call {call.tool_call_id!r} was already completed and cannot run again."
            )

        started_at = utc_now()
        started_clock = time.perf_counter()
        try:
            tool = self.get(call.name)
            output = await tool.invoke(call.arguments)
        except (ToolNotFoundError, ToolValidationError, ToolExecutionError) as exc:
            finished_at = utc_now()
            return ToolResult(
                tool_call_id=call.tool_call_id,
                tool_name=call.name,
                success=False,
                error=str(exc),
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0.0, (time.perf_counter() - started_clock) * 1000),
            )
        finished_at = utc_now()
        return ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=call.name,
            success=True,
            output=output,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0.0, (time.perf_counter() - started_clock) * 1000),
        )


__all__ = [
    "ArgumentsT",
    "FunctionTool",
    "Tool",
    "ToolCallable",
    "ToolRegistry",
    "canonical_fingerprint",
    "to_anthropic_tool",
    "to_json_schema",
    "to_openai_function",
    "tool_call_fingerprint",
    "tool_result_fingerprint",
]
