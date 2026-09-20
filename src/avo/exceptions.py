"""Exception hierarchy for Avo."""

from __future__ import annotations

from enum import StrEnum


class AvoError(Exception):
    """Base class for expected Avo failures."""


class InvalidStateTransitionError(AvoError):
    """Raised when a state-machine transition is not allowed."""


class RunNotFoundError(AvoError):
    """Raised when a requested run does not exist."""


class RunAlreadyTerminalError(AvoError):
    """Raised when execution is requested for a terminal run."""


class CheckpointNotFoundError(AvoError):
    """Raised when a run has no checkpoint from which it can resume."""


class UnsafeResumeError(AvoError):
    """Raised when a started tool has no durable result and cannot be retried safely."""


class DuplicateToolError(AvoError):
    """Raised when two tools use the same registry name."""


class ToolNotFoundError(AvoError):
    """Raised when a model requests a tool that is not registered."""


class ToolValidationError(AvoError):
    """Raised when tool arguments or output fail validation."""


class ToolExecutionError(AvoError):
    """Raised when a tool callable fails during execution."""


class ToolAlreadyCompletedError(AvoError):
    """Raised when an already-completed tool-call identifier is invoked."""


class StorageError(AvoError):
    """Raised when an event-store operation fails."""


class EventInvariantError(StorageError):
    """Raised when an append would violate an event-log invariant."""


class ProviderFailureKind(StrEnum):
    """Stable category used by fallback, telemetry, and diagnostics."""

    AUTHENTICATION = "authentication"
    CLIENT = "client"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TRANSPORT = "transport"
    PROTOCOL = "protocol"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class ProviderError(AvoError):
    """Raised for deterministic or adapter-specific provider failures."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        kind: ProviderFailureKind = ProviderFailureKind.UNKNOWN,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind
        self.status_code = status_code

    @classmethod
    def from_status(cls, status_code: int, message: str) -> ProviderError:
        """Build a consistently classified HTTP provider failure."""

        if status_code in {401, 403}:
            kind = ProviderFailureKind.AUTHENTICATION
            retryable = False
        elif status_code == 429:
            kind = ProviderFailureKind.RATE_LIMIT
            retryable = True
        elif 500 <= status_code < 600:
            kind = ProviderFailureKind.SERVER
            retryable = True
        elif status_code == 408:
            kind = ProviderFailureKind.TRANSPORT
            retryable = True
        elif 400 <= status_code < 500:
            kind = ProviderFailureKind.CLIENT
            retryable = False
        else:
            kind = ProviderFailureKind.UNKNOWN
            retryable = False
        return cls(
            message,
            retryable=retryable,
            kind=kind,
            status_code=status_code,
        )


class FakeProviderExhaustedError(ProviderError):
    """Raised when a fake provider has no scripted response remaining."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)
