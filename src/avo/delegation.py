"""Bounded, isolated delegation of named child agents."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import JsonValue

from avo.agent_profiles import AgentCapability, AgentMention
from avo.capabilities import filter_tools, inherit_runtime_security
from avo.exceptions import AvoError
from avo.models import TokenUsage
from avo.providers.base import ModelProvider
from avo.runtime import AgentRuntime
from avo.storage.base import EventStore
from avo.storage.memory import InMemoryEventStore
from avo.tools import Tool

_MAX_OUTPUT_CHARS = 8_192
_MAX_ERROR_CHARS = 2_048


class DelegationError(AvoError):
    """Raised when child runtimes cannot be safely created."""


@dataclass(frozen=True)
class DelegationResult:
    """Bounded summary of one child runtime."""

    agent_name: str
    child_run_id: str
    status: str
    output: str | None
    error: str | None
    steps: int
    token_usage: TokenUsage


def child_tools(profile: Any, parent_tools: Iterable[Tool]) -> list[Tool]:
    """Filter parent tools according to the profile's capability."""

    if profile is None:
        raise DelegationError("agent profile is required")
    tools = list(parent_tools)
    if profile.capability is not AgentCapability.READ_ONLY:
        return tools
    return filter_tools(tools, read_only=True)


def _bounded_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class DelegationCoordinator:
    """Run named child agents with isolated runtimes and bounded concurrency."""

    def __init__(
        self,
        parent_runtime: AgentRuntime,
        *,
        provider_factory: Callable[[], ModelProvider] | None = None,
        event_store_factory: Callable[[], EventStore] = InMemoryEventStore,
        max_concurrency: int = 4,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.parent_runtime = parent_runtime
        self.provider_factory = provider_factory
        self.event_store_factory = event_store_factory
        self.max_concurrency = max_concurrency

    async def run(
        self,
        parent_run_id: str,
        requests: Sequence[AgentMention],
        *,
        user_state: dict[str, JsonValue] | None = None,
    ) -> tuple[DelegationResult, ...]:
        """Run all mentions, preserving input order in the returned summaries."""

        if not parent_run_id:
            raise DelegationError("parent_run_id must be non-empty")
        if not requests:
            raise DelegationError("at least one agent request is required")
        if self.provider_factory is None and len(requests) > 1 and not self._can_clone_provider():
            raise DelegationError(
                "parallel delegation requires a provider factory or a stateful cloneable provider"
            )
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run_one(index: int, mention: AgentMention) -> DelegationResult:
            child_id = f"{parent_run_id}.{mention.agent.name}.{index + 1}"
            async with semaphore:
                try:
                    provider = self._provider_for_child()
                    store = self.event_store_factory()
                    tools = child_tools(mention.agent, self.parent_runtime.tools._tools.values())
                    security_config = inherit_runtime_security(
                        self.parent_runtime.security_config,
                        AgentCapability(mention.agent.capability),
                    )
                    child = AgentRuntime(
                        provider=provider,
                        tools=tools,
                        policy=self.parent_runtime.policy,
                        security_config=security_config,
                        event_store=store,
                        approval_callback=self.parent_runtime.approval_callback,
                    )
                    metadata: dict[str, JsonValue] = {
                        "parent_run_id": parent_run_id,
                        "agent_name": mention.agent.name,
                        "agent_capability": mention.agent.capability,
                    }
                    if user_state:
                        metadata.update(user_state)
                    result = await child.run(
                        mention.prompt,
                        system_prompt=mention.agent.system_prompt,
                        user_state=metadata,
                        run_id=child_id,
                    )
                    return DelegationResult(
                        agent_name=mention.agent.name,
                        child_run_id=child_id,
                        status=result.status.value,
                        output=_bounded_text(result.output, _MAX_OUTPUT_CHARS),
                        error=_bounded_text(
                            result.error
                            or (
                                None
                                if result.status.value == "completed"
                                else result.stop_reason.value
                            ),
                            _MAX_ERROR_CHARS,
                        ),
                        steps=result.steps,
                        token_usage=result.token_usage,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    return DelegationResult(
                        agent_name=mention.agent.name,
                        child_run_id=child_id,
                        status="failed",
                        output=None,
                        error=_bounded_text(f"{type(exc).__name__}: {exc}", _MAX_ERROR_CHARS),
                        steps=0,
                        token_usage=TokenUsage(),
                    )

        results = await asyncio.gather(
            *(run_one(index, mention) for index, mention in enumerate(requests))
        )
        return tuple(results)

    async def pipeline(
        self,
        parent_run_id: str,
        stages: Sequence[AgentMention],
        *,
        user_state: dict[str, JsonValue] | None = None,
        pass_previous_output: bool = True,
    ) -> tuple[DelegationResult, ...]:
        """Run named agents sequentially as a pipeline, passing output downstream."""
        if not parent_run_id:
            raise DelegationError("parent_run_id must be non-empty")
        if not stages:
            raise DelegationError("at least one agent request is required")

        results: list[DelegationResult] = []
        prev_output: str | None = None
        prev_name: str | None = None

        for index, mention in enumerate(stages):
            child_id = f"{parent_run_id}.{mention.agent.name}.{index + 1}"
            prompt = mention.prompt
            if pass_previous_output and prev_output and index > 0:
                prompt = (
                    f"{prompt}\n\n--- Output from prior stage (@{prev_name}) ---\n{prev_output}"
                )

            try:
                provider = self._provider_for_child()
                store = self.event_store_factory()
                tools = child_tools(mention.agent, self.parent_runtime.tools._tools.values())
                security_config = inherit_runtime_security(
                    self.parent_runtime.security_config,
                    AgentCapability(mention.agent.capability),
                )
                child = AgentRuntime(
                    provider=provider,
                    tools=tools,
                    policy=self.parent_runtime.policy,
                    security_config=security_config,
                    event_store=store,
                    approval_callback=self.parent_runtime.approval_callback,
                )
                metadata: dict[str, JsonValue] = {
                    "parent_run_id": parent_run_id,
                    "agent_name": mention.agent.name,
                    "agent_capability": mention.agent.capability,
                    "pipeline_stage": index + 1,
                    "pipeline_total": len(stages),
                }
                if user_state:
                    metadata.update(user_state)
                result = await child.run(
                    prompt,
                    system_prompt=mention.agent.system_prompt,
                    user_state=metadata,
                    run_id=child_id,
                )
                delegation_res = DelegationResult(
                    agent_name=mention.agent.name,
                    child_run_id=child_id,
                    status=result.status.value,
                    output=_bounded_text(result.output, _MAX_OUTPUT_CHARS),
                    error=_bounded_text(
                        result.error
                        or (
                            None if result.status.value == "completed" else result.stop_reason.value
                        ),
                        _MAX_ERROR_CHARS,
                    ),
                    steps=result.steps,
                    token_usage=result.token_usage,
                )
                results.append(delegation_res)
                if delegation_res.status != "completed":
                    break
                prev_output = delegation_res.output
                prev_name = mention.agent.name
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                results.append(
                    DelegationResult(
                        agent_name=mention.agent.name,
                        child_run_id=child_id,
                        status="failed",
                        output=None,
                        error=_bounded_text(f"{type(exc).__name__}: {exc}", _MAX_ERROR_CHARS),
                        steps=0,
                        token_usage=TokenUsage(),
                    )
                )
                break

        return tuple(results)

    def _can_clone_provider(self) -> bool:
        snapshot = getattr(self.parent_runtime.provider, "snapshot_state", None)
        restore = getattr(type(self.parent_runtime.provider), "from_snapshot", None)
        return callable(snapshot) and callable(restore)

    def _provider_for_child(self) -> ModelProvider:
        if self.provider_factory is not None:
            return self.provider_factory()
        if self._can_clone_provider():
            snapshot = self.parent_runtime.provider.snapshot_state()  # type: ignore[attr-defined]
            factory = type(self.parent_runtime.provider).from_snapshot  # type: ignore[attr-defined]
            return cast(ModelProvider, factory(snapshot))
        return self.parent_runtime.provider


__all__ = [
    "DelegationCoordinator",
    "DelegationError",
    "DelegationResult",
    "child_tools",
]
