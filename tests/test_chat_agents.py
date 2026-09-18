"""Chat-level tests for named-agent routing and profile commands."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from avo.agent_profiles import AgentProfileRegistry
from avo.chat_commands import _run_slash
from avo.chat_turn import _run_agent_request
from avo.models import ModelResponse
from avo.providers.fake import FakeProvider
from avo.runtime import AgentRuntime
from avo.storage.memory import InMemoryEventStore


class _Session:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, str]] = []

    def record_user_turn(self, session_id: str, text: str) -> None:
        self.recorded.append((session_id, text))

    def record_assistant_turn(self, *_args: object, **_kwargs: object) -> None:
        return None


def _chat_context(tmp_path: Path, provider: FakeProvider) -> Any:
    runtime = AgentRuntime(provider=FakeProvider([]), event_store=InMemoryEventStore())
    registry = AgentProfileRegistry(tmp_path)
    response = provider._script[0] if provider._script else ModelResponse(content="child output")
    return SimpleNamespace(
        runtime=runtime,
        workspace=SimpleNamespace(root=tmp_path),
        store=SimpleNamespace(path=tmp_path / "events.db"),
        session=_Session(),
        session_id="chat-session",
        agent_profiles=registry,
        provider_factory=lambda: FakeProvider([response]),
    )


@pytest.mark.asyncio
async def test_mention_routes_to_child_without_using_parent_provider(tmp_path: Path) -> None:
    child_provider = FakeProvider([ModelResponse(content="workspace summary")])
    ctx = _chat_context(tmp_path, child_provider)
    request = ctx.agent_profiles.parse_prompt("@explore inspect the workspace")
    assert request is not None
    out = io.StringIO()
    err = io.StringIO()

    handled = await _run_agent_request(
        cast(Any, ctx), request, out, err, original_task="@explore inspect the workspace"
    )

    assert handled is True
    assert "@explore" in out.getvalue()
    assert "workspace summary" in out.getvalue()
    assert not err.getvalue()
    assert ctx.runtime.provider.requests == []  # type: ignore[attr-defined]
    assert ctx.session.recorded == [("chat-session", "@explore inspect the workspace")]


@pytest.mark.asyncio
async def test_parallel_mentions_render_in_input_order(tmp_path: Path) -> None:
    child_provider = FakeProvider([ModelResponse(content="child output")])
    ctx = _chat_context(tmp_path, child_provider)
    request = ctx.agent_profiles.parse_prompt("@explore first | @reviewer second")
    assert request is not None
    out = io.StringIO()
    err = io.StringIO()

    await _run_agent_request(
        cast(Any, ctx), request, out, err, original_task="@explore first | @reviewer second"
    )

    rendered = out.getvalue()
    assert rendered.index("@explore") < rendered.index("@reviewer")
    assert rendered.count("child output") == 2


@pytest.mark.asyncio
async def test_agents_list_and_add_are_workspace_local(tmp_path: Path) -> None:
    ctx = _chat_context(tmp_path, FakeProvider([]))
    out = io.StringIO()
    err = io.StringIO()

    should_exit = await _run_slash(
        cast(Any, ctx), ["/agents", "list"], out, err, {}, in_stream=io.StringIO()
    )

    assert should_exit is False
    assert "@explore" in out.getvalue()
    assert "@reviewer" in out.getvalue()

    out = io.StringIO()
    await _run_slash(
        cast(Any, ctx), ["/agent", "add", "docs", "Document the workspace"], out, err, {}
    )

    assert ctx.agent_profiles.get("docs") is not None
    assert (tmp_path / ".avo" / "agents" / "docs.md").is_file()
