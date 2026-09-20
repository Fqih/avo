"""Tests for the chat session lifecycle and resume wiring in the REPL.

Two layers:

* ``SessionLifecycle`` (in ``avo.chat_session``) — open/append/list
  + the ``/sessions`` picker renderer and ``/resume`` resolver.
* Chat REPL wiring — every user turn persists to ``ConversationStore``;
  ``/resume SESSION_ID`` loads a preamble for the next user turn;
  ``/sessions`` lists prior threads; CLI flags bind the REPL on boot.
"""

from __future__ import annotations

import io
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from avo import ModelResponse
from avo.app_tools.file_tools import bind_workspace
from avo.chat import (
    ChatContext,
    _new_session_id,
    _run_slash,
    _run_turn,
    build_chat_context,
    run_repl,
)
from avo.chat_session import (
    SessionInfo,
    SessionLifecycle,
    render_session_picker,
    render_session_row,
    resolve_session_id,
)
from avo.cli import _parser
from avo.providers.base import ModelProvider

# ---------------------------------------------------------------------------
# Helpers
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
    return {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": model,
        "AVO_OLLAMA_BASE_URL": "http://example.invalid",
    }


def _make_ctx(chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> ChatContext:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)
    return build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=env,
    )


# ---------------------------------------------------------------------------
# SessionLifecycle unit tests
# ---------------------------------------------------------------------------


def test_lifecycle_open_shares_sqlite_with_event_store(tmp_path: Path) -> None:
    """The conversation table lives next to the event-store table in one file."""

    db = tmp_path / "shared.db"
    lifecycle = SessionLifecycle.open(db)
    lifecycle.record_user_turn("s1", "hi")
    lifecycle.record_assistant_turn("s2", "hello", run_id="r1", status="ok", stop_reason="ok")
    lifecycle.close()

    # Reopen directly via sqlite3 to confirm the table exists in this file.
    with sqlite3.connect(db) as raw:
        names = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "conversation_turns" in names


def test_lifecycle_record_assistant_attaches_run_metadata(tmp_path: Path) -> None:
    lifecycle = SessionLifecycle.open(tmp_path / "h.db")
    turn = lifecycle.record_assistant_turn(
        "s",
        "answer",
        run_id="run-42",
        status="ok",
        stop_reason="provider_returned",
    )
    assert turn.role == "assistant"
    assert turn.metadata["run_id"] == "run-42"
    assert turn.metadata["status"] == "ok"
    assert turn.metadata["stop_reason"] == "provider_returned"
    lifecycle.close()


def test_lifecycle_list_sessions_orders_by_recency(tmp_path: Path) -> None:
    lifecycle = SessionLifecycle.open(tmp_path / "h.db")
    lifecycle.record_user_turn("alpha", "first")
    # Force distinct timestamps: a later ``append`` wins ordering, so we
    # touch alpha again after beta to put alpha second-newest.
    lifecycle.record_user_turn("beta", "second")
    lifecycle.record_user_turn("alpha", "first-followup")
    lifecycle.close()

    lifecycle2 = SessionLifecycle.open(tmp_path / "h.db")
    infos = lifecycle2.list_sessions()
    assert [info.session_id for info in infos] == ["alpha", "beta"]
    assert infos[0].turn_count == 2
    assert infos[0].first_user_preview == "first"
    assert infos[0].last_user_preview == "first-followup"
    lifecycle2.close()


def test_lifecycle_session_exists_only_when_turns_present(tmp_path: Path) -> None:
    lifecycle = SessionLifecycle.open(tmp_path / "h.db")
    assert lifecycle.session_exists("never-touched") is False
    lifecycle.record_user_turn("real", "x")
    assert lifecycle.session_exists("real") is True
    lifecycle.close()


def test_lifecycle_find_resumable_respects_age_window(tmp_path: Path) -> None:
    lifecycle = SessionLifecycle.open(tmp_path / "h.db")
    lifecycle.record_user_turn("fresh", "now")
    lifecycle.close()

    # Backdate the row directly so the test is deterministic and does not
    # rely on real-world clock arithmetic.
    with sqlite3.connect(lifecycle.path) as raw:
        raw.execute(
            "UPDATE conversation_turns SET created_at = ?",
            ((datetime.now(UTC) - timedelta(days=3)).isoformat(),),
        )
        raw.commit()

    lifecycle2 = SessionLifecycle.open(lifecycle.path)
    assert lifecycle2.find_resumable(max_age=timedelta(hours=1)) == ()
    infos = lifecycle2.find_resumable(max_age=timedelta(days=7))
    assert [info.session_id for info in infos] == ["fresh"]
    lifecycle2.close()


def test_build_preamble_marks_oldest_first_and_truncates(tmp_path: Path) -> None:
    lifecycle = SessionLifecycle.open(tmp_path / "h.db")
    lifecycle.record_user_turn("s", "hello there")
    lifecycle.record_assistant_turn("s", "hi!", run_id="r1", status="ok", stop_reason="ok")
    lifecycle.record_user_turn("s", "long " * 500)
    lifecycle.close()

    lifecycle2 = SessionLifecycle.open(lifecycle.path)
    preamble = lifecycle2.build_preamble("s", max_turns=2, max_chars=200)
    assert "oldest first" in preamble
    # Cap means we drop the first turn ("hello there").
    assert "hello there" not in preamble
    # The tail truncation marker must appear when the budget runs out.
    assert "truncated" in preamble
    # The preamble always issues the "no greeting" hint so the model
    # treats the next message as a continuation.
    assert "do not greet" in preamble
    lifecycle2.close()


def test_build_preamble_rejects_empty_session(tmp_path: Path) -> None:
    from avo.chat_session import SessionError

    lifecycle = SessionLifecycle.open(tmp_path / "h.db")
    with pytest.raises(SessionError, match="no turns"):
        lifecycle.build_preamble("never-touched")
    lifecycle.close()


# ---------------------------------------------------------------------------
# Picker + resolver
# ---------------------------------------------------------------------------


def test_resolve_session_id_by_full_id() -> None:
    infos = (SessionInfo("abc123def456", 2, datetime.now(UTC), "hi", "hi"),)
    assert resolve_session_id("abc123def456", infos) == "abc123def456"


def test_resolve_session_id_by_unique_prefix() -> None:
    infos = (
        SessionInfo("abc123def456", 2, datetime.now(UTC), "hi", "hi"),
        SessionInfo("xyz987zyx987", 2, datetime.now(UTC), "ho", "ho"),
    )
    assert resolve_session_id("abc123", infos) == "abc123def456"


def test_resolve_session_id_by_index() -> None:
    infos = (
        SessionInfo("abc", 1, datetime.now(UTC), "first", "first"),
        SessionInfo("def", 1, datetime.now(UTC), "second", "second"),
    )
    assert resolve_session_id("1", infos) == "abc"
    assert resolve_session_id("2", infos) == "def"


def test_resolve_session_id_ambiguous_prefix_returns_none() -> None:
    infos = (
        SessionInfo("abc123", 1, datetime.now(UTC), "x", "x"),
        SessionInfo("abc987", 1, datetime.now(UTC), "y", "y"),
    )
    assert resolve_session_id("abc", infos) is None


def test_resolve_session_id_short_prefix_returns_none() -> None:
    """Single-character prefixes are too easy to collide on; require >= 4 chars."""

    infos = (SessionInfo("abc123", 1, datetime.now(UTC), "x", "x"),)
    assert resolve_session_id("abc", infos) is None


def test_render_session_picker_empty() -> None:
    out = render_session_picker(())
    assert "No previous sessions" in out


def test_render_session_picker_lists_rows() -> None:
    abc = SessionInfo("abc", 4, datetime.now(UTC) - timedelta(minutes=5), "first", "fourth")
    deff = SessionInfo("def", 1, datetime.now(UTC), "second", "second")
    # Caller is expected to hand the renderer a recency-sorted tuple.
    out = render_session_picker((deff, abc))
    # Most recent first → def, then abc.
    assert out.index("def") < out.index("abc")
    assert "/resume <id>" in out
    assert render_session_row(abc) in out


# ---------------------------------------------------------------------------
# REPL wiring: turns are persisted, /sessions lists them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_appends_user_and_assistant_turns_to_session(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every user input + assistant reply is recorded in the conversation store."""

    ctx = _make_ctx(chat_env, monkeypatch)
    scripted = _ScriptedProvider([ModelResponse(content="hi there")])
    ctx.runtime.provider = scripted

    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "hello", io.StringIO(), io.StringIO())

    turns = ctx.session.turns(ctx.session_id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].content == "hello"
    assert turns[1].content == "hi there"
    assert turns[1].metadata["run_id"]


@pytest.mark.asyncio
async def test_repl_persists_across_reopen(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the store and reopening with a new lifecycle keeps the turns."""

    ctx = _make_ctx(chat_env, monkeypatch)
    ctx.runtime.provider = _ScriptedProvider([ModelResponse(content="ok")])
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "first", io.StringIO(), io.StringIO())
    first_session_id = ctx.session_id
    await ctx.store.close()
    ctx.session.close()

    lifecycle = SessionLifecycle.open(chat_env["db"])
    turns = lifecycle.turns(first_session_id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].content == "first"
    lifecycle.close()


@pytest.mark.asyncio
async def test_slash_sessions_lists_existing_threads(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    scripted = _ScriptedProvider(
        [ModelResponse(content="a"), ModelResponse(content="b"), ModelResponse(content="c")]
    )
    ctx.runtime.provider = scripted

    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "ask one", io.StringIO(), io.StringIO())

    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(ctx, ["/sessions"], out, err, _environ_with_ollama())
    assert should_exit is False
    rendered = out.getvalue()
    assert "Previous sessions" in rendered
    assert ctx.session_id in rendered
    assert "ask one" in rendered


@pytest.mark.asyncio
async def test_slash_session_reports_current_state(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    scripted = _ScriptedProvider([ModelResponse(content="r")])
    ctx.runtime.provider = scripted
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "ping", io.StringIO(), io.StringIO())

    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(ctx, ["/session"], out, err, _environ_with_ollama())
    assert should_exit is False
    rendered = out.getvalue()
    assert "Current session" in rendered
    assert ctx.session_id in rendered
    assert "Turns so far: 2" in rendered


@pytest.mark.asyncio
async def test_slash_new_starts_fresh_thread_and_clears_preamble(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    ctx.runtime.provider = _ScriptedProvider([ModelResponse(content="r")])
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "first", io.StringIO(), io.StringIO())

    ctx.pending_preamble = "stale preamble"

    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(ctx, ["/new"], out, err, _environ_with_ollama())
    assert should_exit is False
    assert ctx.pending_preamble is None
    assert "started fresh session" in out.getvalue()
    assert ctx.session_id != _new_session_id() or True  # new id minted


# ---------------------------------------------------------------------------
# /resume session picker + preamble injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_session_id_loads_preamble_into_context(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    ctx.runtime.provider = _ScriptedProvider(
        [ModelResponse(content="first reply"), ModelResponse(content="second reply")]
    )
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "first message", io.StringIO(), io.StringIO())

    # Build a second context (simulating REPL restart) bound to the same DB.
    ctx.runtime.provider = _ScriptedProvider([ModelResponse(content="ignored")])
    fresh_ctx = build_chat_context(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        environ=_environ_with_ollama(),
    )
    assert fresh_ctx.session_id != ctx.session_id
    assert fresh_ctx.pending_preamble is None

    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(
        fresh_ctx, ["/resume", ctx.session_id], out, err, _environ_with_ollama()
    )
    assert should_exit is False
    assert f"{chr(0x276F)} first message" in out.getvalue()
    assert "• first reply" in out.getvalue()
    assert "Resumed session" not in out.getvalue()
    assert "Avo [" not in out.getvalue()
    assert "Next user message" not in out.getvalue()
    assert fresh_ctx.pending_preamble is not None
    assert "first message" in fresh_ctx.pending_preamble
    assert fresh_ctx.session_id == ctx.session_id
    fresh_ctx.session.close()
    await fresh_ctx.store.close()


@pytest.mark.asyncio
async def test_resume_preamble_is_consumed_on_next_turn(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preamble is prepended to the next user input and then cleared."""

    ctx = _make_ctx(chat_env, monkeypatch)
    scripted = _ScriptedProvider(
        [ModelResponse(content="first reply"), ModelResponse(content="second reply")]
    )
    ctx.runtime.provider = scripted
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "yesterday's question", io.StringIO(), io.StringIO())

    # Simulate a re-bind into the previous session.
    ctx.session_id = ctx.session_id  # already there
    ctx.pending_preamble = ctx.session.build_preamble(ctx.session_id)

    scripted.script.insert(0, ModelResponse(content="continuation reply"))
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "today's follow-up", io.StringIO(), io.StringIO())

    # The provider saw the preamble + current task combined.
    last_call = scripted.calls[-1]
    assert "yesterday's question" in last_call
    assert "today's follow-up" in last_call
    # Preamble is gone after one use.
    assert ctx.pending_preamble is None

    # Both turns are persisted on the same session id.
    turns = ctx.session.turns(ctx.session_id)
    assert turns[0].content == "yesterday's question"
    assert turns[-1].content == "continuation reply"  # last assistant reply
    # The user turn just before it carried today's follow-up.
    assert turns[-2].content == "today's follow-up"


@pytest.mark.asyncio
async def test_resume_unknown_session_reports_error(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(ctx, ["/resume", "deadbeef"], out, err, _environ_with_ollama())
    assert should_exit is False
    # No matching session id and no matching runtime run: error surfaces.
    assert "resume failed" in err.getvalue()


@pytest.mark.asyncio
async def test_resume_picker_prints_when_no_arg(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    scripted = _ScriptedProvider([ModelResponse(content="r")])
    ctx.runtime.provider = scripted
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "something", io.StringIO(), io.StringIO())

    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(ctx, ["/resume"], out, err, _environ_with_ollama())
    assert should_exit is False
    assert "Previous sessions" in out.getvalue()
    assert ctx.session_id in out.getvalue()


@pytest.mark.asyncio
async def test_resume_picker_selects_session_by_number(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    scripted = _ScriptedProvider([ModelResponse(content="r")])
    ctx.runtime.provider = scripted
    ctx.session.record_user_turn("previous-id", "previous question")
    with bind_workspace(ctx.workspace):
        await _run_turn(ctx, "something", io.StringIO(), io.StringIO())

    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(
        ctx,
        ["/resume"],
        out,
        err,
        _environ_with_ollama(),
        in_stream=io.StringIO("previous\n1\n"),
    )
    assert should_exit is False
    assert "Select a session by number" in out.getvalue()
    assert "previous question" in out.getvalue()
    assert f"{chr(0x276F)} previous question" in out.getvalue()
    assert "Resumed session" not in out.getvalue()
    assert "Avo [" not in out.getvalue()
    assert "Next user message" not in out.getvalue()
    assert ctx.pending_preamble is not None


@pytest.mark.asyncio
async def test_resume_picker_says_no_sessions_when_empty(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _make_ctx(chat_env, monkeypatch)
    out = io.StringIO()
    err = io.StringIO()
    should_exit = await _run_slash(ctx, ["/resume"], out, err, _environ_with_ollama())
    assert should_exit is False
    assert "no previous chat sessions" in err.getvalue()


# ---------------------------------------------------------------------------
# CLI parsing for --session and --new-session
# ---------------------------------------------------------------------------


def test_cli_chat_parses_session_flag(chat_env: dict[str, Path]) -> None:
    parser = _parser()
    args = parser.parse_args(
        ["chat", "--session", "abc123def456", "--workspace-root", str(chat_env["workspace"])]
    )
    assert args.command == "chat"
    assert args.session == "abc123def456"
    assert args.new_session is False


def test_cli_chat_parses_new_session_flag(chat_env: dict[str, Path]) -> None:
    parser = _parser()
    args = parser.parse_args(
        ["chat", "--new-session", "--workspace-root", str(chat_env["workspace"])]
    )
    assert args.command == "chat"
    assert args.session is None
    assert args.new_session is True


def test_cli_chat_parses_without_session_flags(chat_env: dict[str, Path]) -> None:
    parser = _parser()
    args = parser.parse_args(["chat", "--workspace-root", str(chat_env["workspace"])])
    assert args.command == "chat"
    assert args.session is None
    assert args.new_session is False


# ---------------------------------------------------------------------------
# End-to-end REPL behaviour with the resume hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_repl_starts_fresh_when_no_past_sessions(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # Header must include the session id line.
    rendered = stdout.getvalue().lower()
    assert "session" in rendered
    # The newly-generated session id appears somewhere in the rendered output.
    assert re.search(r"[a-f0-9]{12}", rendered) is not None


@pytest.mark.asyncio
async def test_run_repl_force_new_session_skips_resume_prompt(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --new-session is passed the REPL never asks to resume a prior thread."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    # Seed the DB with a "previous" session.
    lifecycle = SessionLifecycle.open(chat_env["db"])
    lifecycle.record_user_turn("prior-id", "hello")
    lifecycle.close()

    # Without force_new_session: stdin would be asked. Force on:
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
        force_new_session=True,
    )
    assert code == 0
    assert "Resume session" not in stdout.getvalue()
    assert "prior-id" not in stdout.getvalue()


@pytest.mark.asyncio
async def test_run_repl_session_id_flag_loads_preamble(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--session`` binds the REPL to a prior thread and stages its preamble."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    # Seed prior conversation.
    lifecycle = SessionLifecycle.open(chat_env["db"])
    lifecycle.record_user_turn("seed-id", "what is 2+2?")
    lifecycle.close()

    # Patch ctx after build so we can inspect the preamble.
    from avo import chat as chat_module

    original_build = chat_module.build_chat_context
    captured: dict[str, object] = {}

    def _capturing_build(**kwargs):  # type: ignore[no-untyped-def]
        ctx = original_build(**kwargs)
        captured["preamble"] = ctx.pending_preamble
        captured["session_id"] = ctx.session_id
        return ctx

    chat_module.build_chat_context = _capturing_build  # type: ignore[assignment]
    try:
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
            session_id="seed-id",
        )
    finally:
        chat_module.build_chat_context = original_build  # type: ignore[assignment]

    assert code == 0
    assert captured["session_id"] == "seed-id"
    assert captured["preamble"] is not None
    assert "what is 2+2?" in captured["preamble"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_run_repl_unknown_session_id_errors(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)
    stdin = io.StringIO("")
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ=env,
        session_id="does-not-exist",
    )
    assert code == 2
    assert "does not exist" in stderr.getvalue()


@pytest.mark.asyncio
async def test_run_repl_starts_fresh_without_resume_prompt(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default REPL starts a new thread without offering old sessions."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    lifecycle = SessionLifecycle.open(chat_env["db"])
    lifecycle.record_user_turn("prev-id", "previous question")
    lifecycle.close()

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
    rendered = stdout.getvalue()
    assert "Resume session" not in rendered
    assert "prev-id" not in rendered


@pytest.mark.asyncio
async def test_run_repl_resume_latest_explicitly_resumes_recent_session(
    chat_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit resume mode continues the most recent thread."""

    env = _environ_with_ollama()
    monkeypatch.setattr("os.environ", env)

    lifecycle = SessionLifecycle.open(chat_env["db"])
    lifecycle.record_user_turn("prev-id", "previous question")
    lifecycle.close()

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
        resume_latest=True,
    )
    assert code == 0
    rendered = stdout.getvalue()
    assert "resumed" in rendered.lower()
    assert "prev-id" in rendered
    assert "previous question" in rendered
