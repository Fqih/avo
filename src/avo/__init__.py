"""Public API for Avo."""

from avo.deprecation import DeprecatedSymbol, deprecated, deprecation_index
from avo.events import AgentEvent, EventType
from avo.integrations import LetheMemoryAdapter, MemoryProvider
from avo.models import (
    Checkpoint,
    ModelRequest,
    ModelResponse,
    RunRecord,
    RunResult,
    TokenUsage,
    ToolCall,
    ToolMetadata,
    ToolResult,
)
from avo.observability import (
    OtelDisabledError,
    configure_tracer,
    is_enabled,
    span_for_turn,
)
from avo.policies import LoopPolicy
from avo.progress import ProgressDetector
from avo.providers.fake import FakeProvider, ScriptItem
from avo.runtime import AgentRuntime
from avo.state import RunState, StopReason
from avo.tools import (
    FunctionTool,
    Tool,
    ToolRegistry,
    to_anthropic_tool,
    to_json_schema,
    to_openai_function,
)
from avo.tracing import RunTrace, TraceEntry, TraceInspector

__version__ = "0.1.6"

# Public API surface freeze — see ``docs/api-stability.md``. Any change
# to the list below requires a SemVer bump per ``docs/semver.md``.
_STABLE_ABI = "0.2.0"

__all__ = [
    "AgentEvent",
    "AgentRuntime",
    "Checkpoint",
    "DeprecatedSymbol",
    "EventType",
    "FakeProvider",
    "FunctionTool",
    "LetheMemoryAdapter",
    "LoopPolicy",
    "MemoryProvider",
    "ModelRequest",
    "ModelResponse",
    "OtelDisabledError",
    "ProgressDetector",
    "RunRecord",
    "RunResult",
    "RunState",
    "RunTrace",
    "ScriptItem",
    "StopReason",
    "TokenUsage",
    "Tool",
    "ToolCall",
    "ToolMetadata",
    "ToolRegistry",
    "ToolResult",
    "TraceEntry",
    "TraceInspector",
    "configure_tracer",
    "deprecated",
    "deprecation_index",
    "is_enabled",
    "span_for_turn",
    "to_anthropic_tool",
    "to_json_schema",
    "to_openai_function",
]
