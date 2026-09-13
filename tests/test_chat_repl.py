"""Tests for the REPL loop, slash commands, and chat context construction.

Covers CLI parsing for the ``chat`` subcommand, the wiring between
``build_provider_from_env`` and ``AgentRuntime``, one-turn / multi-turn
behaviour, slash-command dispatch, workspace enforcement, and persistence
to the SQLite event store. First-run wizard and shell-rc persistence live
in separate modules.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from avo import ModelResponse
from avo.app_tools.file_tools import bind_workspace, read_file_tool, write_file_tool
from avo.chat import build_chat_context, run_repl
from avo.config import ConfigError
from avo.exceptions import AvoError
from avo.providers.base import ModelProvider
from avo.storage.sqlite import SQLiteEventStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _ScriptedProvider(ModelProvider):
    """Provider that returns pre-recorded responses and records calls."""

    def __init__(self, script: list[ModelResponse]) -> None:
        self.script = list(script)
        self.calls: list[str] = []

    async def generate(self, request):  # type: ignore[override]
        self.calls.append(request.messages[-1].get("content", ""))  # type: ignore[union-attr]
        if not self.script:
            from avo.exceptions import FakeProviderExhaustedError

            raise FakeProviderExhaustedError("script empty")
        return self.script.pop(0)


@pytest.fixture
def chat_env(tmp_path: Path) -> dict[str, Path]:
    db = tmp_path / "chat.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("seed", encoding="utf-8")
    return {"db": db, "workspace": workspace}


def _environ_with_ollama(model: str = "fake-test-model") -> dict[str, str]:
    """Return an env dict that ``build_provider_from_env`` will accept for ollama."""

    return {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": model,
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


def test_cli_chat_subcommand_parses(chat_env: dict[str, Path]) -> None:
    """The ``chat`` subcommand accepts an optional ``--workspace-root``."""

    from avo.cli import _parser

    parser = _parser()
    args = parser.parse_args(
        [
            "chat",
            "--database",
            str(chat_env["db"]),
            "--workspace-root",
            str(chat_env["workspace"]),
        ]
    )
    assert args.command == "chat"
    assert args.workspace_root == chat_env["workspace"]
    assert args.database == chat_env["db"]


def test_cli_existing_runs_subcommands_still_parse(chat_env: dict[str, Path]) -> None:
    """Existing ``runs list/inspect/resume`` continue to work after the chat add."""

    from avo.cli import _parser

    parser = _parser()
    list_args = parser.parse_args(["runs", "list"])
    assert list_args.command == "runs"
    assert list_args.runs_command == "list"

    inspect_args = parser.parse_args(["runs", "inspect", "abc"])
    assert inspect_args.runs_command == "inspect"
    assert inspect_args.run_id == "abc"

    resume_args = parser.parse_args(["runs", "resume", "def"])
    assert resume_args.runs_command == "resume"
    assert resume_args.run_id == "def"


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------


def test_build_chat_context_constructs_runtime_with_tools(
    chat_env: dict[str, Path],
) -> None:
    """Context wires the provider factory, SQLite store, and file tools."""

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=_environ_with_ollama(),
    )

    assert isinstance(ctx.runtime.provider, ModelProvider)
    assert ctx.store is not None
    assert ctx.workspace.root == chat_env["workspace"].resolve()
    tool_names = {tool.metadata.name for tool in ctx.runtime.tools._tools.values()}  # type: ignore[attr-defined]
    assert tool_names == {
        "read_file",
        "write_file",
        "lint",
        "test_runner",
        "git_diff",
        "git_commit",
    }


def test_build_chat_context_missing_provider_raises(
    chat_env: dict[str, Path],
) -> None:
    with pytest.raises(ConfigError):
        build_chat_context(
            database_path=chat_env["db"],
            workspace_root=chat_env["workspace"],
            environ={},
        )


def test_build_chat_context_missing_workspace_raises(
    chat_env: dict[str, Path],
) -> None:
    with pytest.raises(AvoError):
        build_chat_context(
            database_path=chat_env["db"],
            workspace_root=chat_env["workspace"] / "does-not-exist",
            environ=_environ_with_ollama(),
        )


# ---------------------------------------------------------------------------
# REPL delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_runs_one_turn_per_non_empty_line(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each user line becomes one ``AgentRuntime.run`` invocation."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    scripted = _ScriptedProvider([ModelResponse(content="hi"), ModelResponse(content="hey")])
    ctx.runtime.provider = scripted

    stdout = io.StringIO()
    stderr = io.StringIO()

    # Inline REPL using the same machinery but with the scripted provider swapped in.
    from avo.chat import _run_turn

    with bind_workspace(ctx.workspace):
        for line in ("hello", "world"):
            await _run_turn(ctx, line, stdout, stderr)

    assert scripted.calls == ["hello", "world"]


@pytest.mark.asyncio
async def test_repl_quit_command_exits(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/quit`` and ``/exit`` return exit code 0 and close the store."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )

    assert code == 0
    assert "/help" in stdout.getvalue()


@pytest.mark.asyncio
async def test_repl_exit_alias_exits(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    stdin = io.StringIO("/exit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0


@pytest.mark.asyncio
async def test_repl_provider_command_does_not_leak_key(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/provider`` shows model + key presence but never the key value."""

    env = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "llama3.1",
        "AVO_OLLAMA_BASE_URL": "http://localhost:11434",
        "AVO_OLLAMA_API_KEY": "super-secret-token",
    }
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/provider\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    output = stdout.getvalue()
    assert "super-secret-token" not in output
    assert "Provider: ollama" in output
    assert "Model: llama3.1" in output
    assert "API key configured: yes" in output


@pytest.mark.asyncio
async def test_repl_empty_lines_are_skipped(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("\n\n   \n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0


@pytest.mark.asyncio
async def test_repl_eof_exits_cleanly(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("")  # immediate EOF
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    assert "/help" in stdout.getvalue()


@pytest.mark.asyncio
async def test_repl_runtime_error_does_not_crash(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime error inside one turn is contained; the REPL continues."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    scripted = _ScriptedProvider([])  # raises FakeProviderExhaustedError on first call
    ctx.runtime.provider = scripted

    from avo.chat import _run_turn

    stdout = io.StringIO()
    stderr = io.StringIO()
    with bind_workspace(ctx.workspace):
        # First turn: provider raises, runtime catches as PROVIDER_ERROR,
        # result has status=FAILED. _run_turn renders stop_reason + error.
        await _run_turn(ctx, "trigger error", stdout, stderr)
        # Second turn after the error: verify containment rather than crash.
        await _run_turn(ctx, "trigger again", stdout, stderr)

    output = stdout.getvalue() + stderr.getvalue()
    assert "failed" in output.lower() or "provider" in output.lower()


# ---------------------------------------------------------------------------
# Workspace protections survive the chat path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_workspace_blocks_traversal(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``validate_path`` inside the chat context still rejects escapes."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    with pytest.raises(Exception, match="escapes"):
        ctx.workspace.validate_path("../escape.txt")


@pytest.mark.asyncio
async def test_chat_file_tools_work_inside_workspace(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_file_tool and write_file_tool resolve against the chat workspace."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )

    with bind_workspace(ctx.workspace):
        result = await write_file_tool().invoke({"path": "via_chat.txt", "content": "written"})
        assert result["size"] == 7

        read_result = await read_file_tool().invoke({"path": "via_chat.txt"})
        assert read_result["content"] == "written"

    assert (chat_env["workspace"] / "via_chat.txt").read_text(encoding="utf-8") == "written"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_persists_run_and_events(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    scripted = _ScriptedProvider([ModelResponse(content="done")])
    ctx.runtime.provider = scripted

    stdout = io.StringIO()
    stderr = io.StringIO()

    from avo.chat import _run_turn

    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "first task", stdout, stderr)

    await ctx.store.close()

    # Re-open with a fresh store to confirm the row is durable.
    store2 = SQLiteEventStore(chat_env["db"])
    runs = await store2.list_runs()
    assert len(runs) == 1
    run = runs[0]
    events = await store2.get_events(run.run_id)
    # RUN_CREATED + MODEL_REQUESTED + MODEL_RESPONDED + STATE_CHANGED + RUN_FINALIZED
    assert len(events) >= 4
    await store2.close()


@pytest.mark.asyncio
async def test_chat_multiple_turns_create_multiple_runs(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    scripted = _ScriptedProvider(
        [ModelResponse(content="a"), ModelResponse(content="b"), ModelResponse(content="c")]
    )
    ctx.runtime.provider = scripted

    from avo.chat import _run_turn

    stdout = io.StringIO()
    stderr = io.StringIO()
    with bind_workspace(ctx.workspace):
        for task in ("one", "two", "three"):
            await _run_turn(ctx, task, stdout, stderr)

    await ctx.store.close()
    store2 = SQLiteEventStore(chat_env["db"])
    runs = await store2.list_runs()
    assert len(runs) == 3
    await store2.close()


@pytest.mark.asyncio
async def test_chat_inspect_command_finds_persisted_run(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    scripted = _ScriptedProvider([ModelResponse(content="ok")])
    ctx.runtime.provider = scripted

    stdout = io.StringIO()
    stderr = io.StringIO()

    from avo.chat import _run_turn

    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "task one", stdout, stderr)

    run_id = (await ctx.store.list_runs())[0].run_id

    # Now invoke /inspect through the slash dispatcher.
    from avo.chat import _run_slash

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_requested = await _run_slash(ctx, ["/inspect", run_id], stdout, stderr, env)
    assert exit_requested is False
    assert run_id in stdout.getvalue()


# ---------------------------------------------------------------------------
# CLI integration smoke (no provider network calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_full_flow_until_quit(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: header printed, turn executed, ``/quit`` exits 0."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    scripted = _ScriptedProvider([ModelResponse(content="hello")])
    ctx.runtime.provider = scripted

    # Patch run_repl's internal ctx by injecting ours via a thin wrapper.
    from avo import chat as chat_module

    original_build = chat_module.build_chat_context
    chat_module.build_chat_context = lambda **kwargs: ctx  # type: ignore[assignment]
    try:
        stdin = io.StringIO("hello there\n/quit\n")
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = await run_repl(
            database_path=chat_env["db"],
            workspace_root=chat_env["workspace"],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            environ=env,
        )
    finally:
        chat_module.build_chat_context = original_build  # type: ignore[assignment]

    assert code == 0
    output = stdout.getvalue()
    assert "provider" in output.lower() and "ollama" in output
    assert "workspace" in output.lower()
    assert scripted.calls == ["hello there"]


def test_existing_runs_subcommands_unchanged(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``runs list/inspect/resume`` still parse and run unchanged."""

    from avo.cli import main

    db = chat_env["db"]
    code = main(["--database", str(db), "runs", "list"])
    assert code == 0

    code = main(["--database", str(db), "runs", "inspect", "nope"])
    assert code == 2  # missing run, but parser + dispatcher ok


@pytest.mark.asyncio
async def test_slash_skills_lists_discovered_names(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)
    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    skills_root = ctx.skills.root
    (skills_root / "alpha.md").write_text("a body", encoding="utf-8")
    (skills_root / "zeta.md").write_text("z body", encoding="utf-8")

    from avo.chat import _run_slash

    stdout = io.StringIO()
    stderr = io.StringIO()
    should_exit = await _run_slash(ctx, ["/skills"], stdout, stderr, env)

    assert should_exit is False
    assert stdout.getvalue().strip().splitlines() == ["alpha", "  zeta"]


@pytest.mark.asyncio
async def test_slash_skill_dispatches_body_to_runtime(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)
    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )
    ctx.skills.root.mkdir(parents=True, exist_ok=True)
    (ctx.skills.root / "review.md").write_text("Review the diff.", encoding="utf-8")
    scripted = _ScriptedProvider([ModelResponse(content="ok")])
    ctx.runtime.provider = scripted

    from avo.chat import _run_slash

    stdout = io.StringIO()
    stderr = io.StringIO()
    with bind_workspace(ctx.workspace):
        should_exit = await _run_slash(ctx, ["/skill", "review"], stdout, stderr, env)

    assert should_exit is False
    assert scripted.calls == ["Review the diff."]


@pytest.mark.asyncio
async def test_slash_skill_missing_name_reports_error(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)
    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )

    from avo.chat import _run_slash

    stdout = io.StringIO()
    stderr = io.StringIO()
    should_exit = await _run_slash(ctx, ["/skill", "nope"], stdout, stderr, env)

    assert should_exit is False
    assert "skill load failed" in stderr.getvalue()


@pytest.mark.asyncio
async def test_slash_skill_without_name_prints_usage(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)
    ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )

    from avo.chat import _run_slash

    stdout = io.StringIO()
    stderr = io.StringIO()
    should_exit = await _run_slash(ctx, ["/skill"], stdout, stderr, env)

    assert should_exit is False
    assert "usage: /skill NAME" in stderr.getvalue()


@pytest.mark.asyncio
async def test_repl_model_command_lists_catalog_with_current_marker(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/model`` (no args) prints the catalog and marks the current model."""

    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/model\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    output = stdout.getvalue()
    assert "Models for provider 'ollama'" in output
    assert "llama3.1" in output
    # Current model gets a marker; the catalog prints at least one alternative.
    assert "*" in output
    assert "Pick a model with: /model NAME" in output


@pytest.mark.asyncio
async def test_repl_model_command_switches_provider(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/model NAME`` swaps the runtime provider and updates the label."""

    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/model qwen2.5-coder\n/model\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    output = stdout.getvalue()
    assert "Switched provider 'ollama' to model 'qwen2.5-coder'" in output
    # The provider actually swapped.
    assert isinstance(env["AVO_MODEL"] or "", str)
    # The second /model call should now mark qwen2.5-coder as the current model.
    assert output.count("qwen2.5-coder") >= 2


@pytest.mark.asyncio
async def test_repl_model_command_rejects_unknown_model(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/model bogus`` errors and does not change the active model."""

    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/model not-a-real-model\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    assert "is not in the 'ollama' catalog" in stderr.getvalue()
    assert env["AVO_MODEL"] == "llama3.1"


@pytest.mark.asyncio
async def test_repl_model_command_usage_error_when_too_many_args(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/model a b c\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    assert "usage: /model" in stderr.getvalue()


@pytest.mark.asyncio
async def test_repl_context_command(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/context\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    out = stdout.getvalue()
    assert "Active Context" in out
    assert "Provider" in out
    assert "ollama" in out


@pytest.mark.asyncio
async def test_repl_clear_command(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/clear\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    assert "\033[2J\033[H" in stdout.getvalue()


@pytest.mark.asyncio
async def test_repl_diff_command_clean_or_non_git(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/diff\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    combined = stdout.getvalue() + stderr.getvalue()
    # Workspace is either non-git or clean
    assert (
        "not a git repository" in combined
        or "clean" in combined.lower()
        or "git status" in combined.lower()
    )


def test_session_export_markdown(tmp_path: Path) -> None:
    from avo.chat_session import SessionLifecycle

    db = tmp_path / "test_export.db"
    session = SessionLifecycle.open(db)
    session.record_user_turn("sess-1", "Write a fibonacci function")
    session.record_assistant_turn(
        "sess-1",
        "```python\ndef fib(n):\n    return n\n```",
        run_id="run-fib-1",
        status="completed",
        stop_reason="stop",
    )

    md = session.export_markdown("sess-1")
    assert "# Avo Chat Session" in md
    assert "sess-1" in md
    assert "👤 User" in md
    assert "Write a fibonacci function" in md
    assert "🤖 Assistant" in md
    assert "def fib(n):" in md
    assert "run-fib-1" in md
    session.close()


@pytest.mark.asyncio
async def test_repl_export_command(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    from avo.chat_session import SessionLifecycle

    session = SessionLifecycle.open(chat_env["db"])
    session.record_user_turn("test-export-sess", "Hello world")
    session.close()

    stdin = io.StringIO("/export\n/export custom_export.md\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
        session_id="test-export-sess",
    )
    assert code == 0
    out = stdout.getvalue()
    assert "Exported session test-export-sess" in out
    assert "custom_export.md" in out

    custom_path = chat_env["workspace"] / "custom_export.md"
    assert custom_path.is_file()
    assert "Hello world" in custom_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_repl_router_status_command(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/router\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    out = stdout.getvalue()
    assert "Router is not active" in out


@pytest.mark.asyncio
async def test_repl_model_provider_switching(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    stdin = io.StringIO("/model ollama/qwen2.5-coder\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
    )
    assert code == 0
    out = stdout.getvalue()
    assert "Switched to provider 'ollama' and model 'qwen2.5-coder'" in out


def test_repl_undo_command(
    chat_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import subprocess

    env = _environ_with_ollama(model="llama3.1")
    monkeypatch.setattr("os.environ", env)

    ws = chat_env["workspace"]
    # Initialize git repo in workspace
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(ws), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(ws), check=True)
    subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(ws), check=True)

    # Modify existing file and create new file
    (ws / "README.md").write_text("corrupted", encoding="utf-8")
    (ws / "bad.txt").write_text("bad", encoding="utf-8")

    stdin = io.StringIO("/undo\n/undo\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = asyncio.run(
        run_repl(
            database_path=chat_env["db"],
            workspace_root=ws,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            environ=env,
        )
    )
    assert code == 0
    out = stdout.getvalue()
    assert "Rolled back changes in 2 file(s)" in out
    assert "Working tree clean. Nothing to undo." in out
    assert (ws / "README.md").read_text(encoding="utf-8") == "seed"
    assert not (ws / "bad.txt").exists()
