"""Public, serializable domain models for Avo."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from avo.capabilities import ToolCapability
from avo.state import RunState, StopReason, is_terminal, validate_terminal_outcome


def new_id() -> str:
    """Return a UUID4 encoded as a canonical string.

    Avo consistently represents UUID identifiers as strings at public and
    persistence boundaries.
    """

    return str(uuid4())


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(UTC)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must be timezone-aware")
    return value.astimezone(UTC)


class AvoModel(BaseModel):
    """Strict base model shared by public Avo data models."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TokenUsage(AvoModel):
    """Input and output token counts reported by a provider."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        """Return combined input and output tokens."""

        return self.input_tokens + self.output_tokens

    def plus(self, other: TokenUsage) -> TokenUsage:
        """Return the element-wise sum of two usage records."""

        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class ToolMetadata(AvoModel):
    """Model-facing metadata and JSON schema for a registered tool."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]
    capability: ToolCapability = ToolCapability.READ


class ToolCall(AvoModel):
    """A provider decision requesting one tool invocation."""

    tool_call_id: str = Field(default_factory=new_id, min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(AvoModel):
    """The normalized result of a tool invocation."""

    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    success: bool
    output: JsonValue | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    duration_ms: float = Field(default=0, ge=0)

    _started_at_aware = field_validator("started_at", mode="after")(_require_aware)
    _finished_at_aware = field_validator("finished_at", mode="after")(_require_aware)

    @model_validator(mode="after")
    def validate_success_payload(self) -> ToolResult:
        """Require errors only for failed tool results."""

        if self.success and self.error is not None:
            raise ValueError("a successful tool result cannot contain an error")
        if not self.success and not self.error:
            raise ValueError("a failed tool result must contain an error")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self


class ModelRequest(AvoModel):
    """Provider-neutral input for one model generation."""

    request_id: str = Field(default_factory=new_id, min_length=1)
    run_id: str = Field(min_length=1)
    step: int = Field(ge=1)
    messages: list[dict[str, JsonValue]]
    tools: list[ToolMetadata] = Field(default_factory=list)
    cache: bool = False
    cache_prefix_messages: int | None = Field(default=None, ge=0)


class ModelResponse(AvoModel):
    """Provider-neutral final answer or single tool-call decision."""

    response_id: str = Field(default_factory=new_id, min_length=1)
    content: str | None = None
    tool_call: ToolCall | None = None
    usage: TokenUsage | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> ModelResponse:
        """Require exactly one of final content and a tool call."""

        has_content = self.content is not None
        has_call = self.tool_call is not None
        if has_content == has_call:
            raise ValueError("model response must contain exactly one of content or tool_call")
        return self

    @property
    def is_final(self) -> bool:
        """Return whether this response is a final answer."""

        return self.content is not None


class RunRecord(AvoModel):
    """Mutable run metadata; its event history remains append-only."""

    run_id: str = Field(default_factory=new_id, min_length=1)
    task: str = Field(min_length=1)
    state: RunState = RunState.CREATED
    stop_reason: StopReason | None = None
    output: str | None = None
    error: str | None = None
    steps: int = Field(default=0, ge=0)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    token_accounting_available: bool = True
    user_state: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    duration_seconds: float | None = Field(default=None, ge=0)

    _created_at_aware = field_validator("created_at", mode="after")(_require_aware)
    _updated_at_aware = field_validator("updated_at", mode="after")(_require_aware)

    @model_validator(mode="after")
    def validate_outcome(self) -> RunRecord:
        """Require terminal states and stop reasons to agree."""

        if is_terminal(self.state):
            if self.stop_reason is None:
                raise ValueError("a terminal run must have a stop reason")
            validate_terminal_outcome(self.state, self.stop_reason)
        elif self.stop_reason is not None:
            raise ValueError("a non-terminal run cannot have a stop reason")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class Checkpoint(AvoModel):
    """A durable runtime snapshot sufficient to continue a non-terminal run."""

    checkpoint_id: str = Field(default_factory=new_id, min_length=1)
    run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    state: RunState
    messages: list[dict[str, JsonValue]]
    next_step: int = Field(ge=1)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    token_accounting_available: bool = True
    consecutive_errors: int = Field(default=0, ge=0)
    repeated_action_history: list[str] = Field(default_factory=list)
    observation_fingerprints: list[str] = Field(default_factory=list)
    model_response_fingerprints: list[str] = Field(default_factory=list)
    progress_markers: list[str] = Field(default_factory=list)
    completed_tool_call_ids: set[str] = Field(default_factory=set)
    user_state: dict[str, JsonValue] = Field(default_factory=dict)
    policy: dict[str, JsonValue]
    provider_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    pending_response: ModelResponse | None = None
    last_event_sequence: int = Field(default=0, ge=0)

    _created_at_aware = field_validator("created_at", mode="after")(_require_aware)


class RunResult(AvoModel):
    """The explicit terminal outcome returned by the runtime."""

    run_id: str
    status: RunState
    stop_reason: StopReason
    output: str | None = None
    error: str | None = None
    steps: int = Field(ge=0)
    token_usage: TokenUsage
    token_accounting_available: bool

    @model_validator(mode="after")
    def validate_terminal_result(self) -> RunResult:
        """Ensure a result cannot describe a non-terminal outcome."""

        validate_terminal_outcome(self.status, self.stop_reason)
        return self
