"""Tests for the interactive first-run setup wizard and secret redaction.

Covers the numbered-menu wizard (``interactive_first_run_setup``), the
end-to-end onboarding flow that turns a fresh shell into a ready REPL,
and the canonical-path guarantee that ``build_chat_context`` actually
receives the env produced by the wizard. Shell-rc persistence has its
own module.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest


def _chat_env(tmp_path: Path) -> dict[str, Path]:
    db = tmp_path / "chat.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("seed", encoding="utf-8")
    return {"db": db, "workspace": workspace}


# ---------------------------------------------------------------------------
# Interactive first-run setup unit tests
# ---------------------------------------------------------------------------


def test_setup_ollama_completes() -> None:
    from avo.chat import interactive_first_run_setup

    # 1 = ollama, empty base_url (default), empty model (default)
    stdin = io.StringIO("1\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "x")
    assert env == {
        "AVO_PROVIDER": "ollama",
        "AVO_OLLAMA_BASE_URL": "http://localhost:11434",
        "AVO_MODEL": "llama3.1",
    }
    assert "Avo First-Time Setup" in stdout.getvalue()
    assert "Starting Avo" in stdout.getvalue()


def test_setup_openai_with_key() -> None:
    from avo.chat import interactive_first_run_setup

    # 2 = openai, key (secret_reader), base_url=default, model=default
    stdin = io.StringIO("2\n\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "sk-test123")
    assert env == {
        "AVO_PROVIDER": "openai",
        "AVO_OPENAI_API_KEY": "sk-test123",
        "AVO_OPENAI_BASE_URL": "https://api.openai.com/v1",
        "AVO_MODEL": "gpt-5.6",
    }


def test_setup_anthropic_uses_custom_model() -> None:
    from avo.chat import interactive_first_run_setup

    # 3 = anthropic, key (secret_reader), model override
    stdin = io.StringIO("3\nclaude-opus\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "sk-ant-test")
    assert env == {
        "AVO_PROVIDER": "anthropic",
        "AVO_ANTHROPIC_API_KEY": "sk-ant-test",
        "AVO_MODEL": "claude-opus",
    }


def test_setup_minimax_with_api_style() -> None:
    from avo.chat import interactive_first_run_setup

    # 4 = minimax, key (secret_reader), style=2 (openai), base_url=default, model=default
    stdin = io.StringIO("4\n2\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "key-xyz")
    assert env == {
        "AVO_PROVIDER": "minimax",
        "AVO_MINIMAX_API_KEY": "key-xyz",
        "AVO_MINIMAX_API_STYLE": "openai",
        "AVO_MINIMAX_BASE_URL": "https://api.minimax.io",
        "AVO_MODEL": "MiniMax-M3",
    }


def test_setup_minimax_rejects_url_in_style_prompt() -> None:
    """A URL pasted into the style prompt must NOT be accepted as a style."""

    from avo.chat import interactive_first_run_setup

    # 4 = minimax, key (secret_reader), URL pasted as style (rejected),
    # then 2 (openai), base_url=default, model=default.
    stdin = io.StringIO("4\nhttps://api.minimax.io/anthropic\n2\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "key-xyz")
    assert env is not None
    assert env["AVO_MINIMAX_API_STYLE"] == "openai"
    out = stdout.getvalue()
    assert "please choose one of: 1, 2" in out


def test_setup_openrouter_manual_key() -> None:
    from avo.chat import interactive_first_run_setup

    # 5 = openrouter, key (secret_reader), base_url=default, model=default
    stdin = io.StringIO("5\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "sk-or-v1-manual")
    assert env == {
        "AVO_PROVIDER": "openrouter",
        "AVO_OPENROUTER_API_KEY": "sk-or-v1-manual",
        "AVO_OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "AVO_MODEL": "meta-llama/llama-3.3-70b-instruct:free",
    }
    assert "OpenRouter" in stdout.getvalue()


def test_setup_openrouter_stored_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.auth import store_token
    from avo.chat import interactive_first_run_setup

    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    store_token("openrouter", "sk-or-stored-token")

    # 5 = openrouter, use stored token (Y/default), base_url=default, model=default
    stdin = io.StringIO("5\n\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(
        stdin,
        stdout,
        secret_reader=lambda _: pytest.fail("secret_reader should not be called with stored token"),
    )
    assert env is not None
    assert env["AVO_PROVIDER"] == "openrouter"
    assert env["AVO_OPENROUTER_API_KEY"] == "sk-or-stored-token"


def test_setup_router_fallback_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.auth import store_token
    from avo.chat import interactive_first_run_setup

    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    store_token("openrouter", "sk-or-router-token")

    # 6 = router, default providers (ollama,openrouter), default models
    stdin = io.StringIO("6\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout)
    assert env == {
        "AVO_PROVIDER": "router",
        "AVO_ROUTER_PROVIDERS": "ollama,openrouter",
        "AVO_ROUTER_MODELS": "llama3.1,meta-llama/llama-3.3-70b-instruct:free",
        "AVO_OPENROUTER_API_KEY": "sk-or-router-token",
        "AVO_MODEL": "auto",
    }
    assert "Router" in stdout.getvalue()


def test_setup_invalid_choice_re_prompts() -> None:
    from avo.chat import interactive_first_run_setup

    # 9 invalid, 0 invalid, then 1 valid; empty base_url, empty model
    stdin = io.StringIO("9\n0\n1\n\n\n")
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "x")
    assert env is not None
    assert env["AVO_PROVIDER"] == "ollama"
    out = stdout.getvalue()
    assert "please choose one of" in out


def test_setup_eof_returns_none() -> None:
    from avo.chat import interactive_first_run_setup

    stdin = io.StringIO("")  # immediate EOF
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=lambda _: "x")
    assert env is None
    assert "aborted" in stdout.getvalue().lower()


def test_setup_uses_secret_reader_not_stdin() -> None:
    """API key must come from secret_reader, not from the REPL stdin."""

    from avo.chat import interactive_first_run_setup

    secret = "shhh-do-not-leak"

    def fake_secret_reader(prompt: str) -> str:
        return secret

    stdin = io.StringIO("2\n\n\n\n")  # newlines after each prompt
    stdout = io.StringIO()
    env = interactive_first_run_setup(stdin, stdout, secret_reader=fake_secret_reader)
    assert env is not None
    assert env["AVO_OPENAI_API_KEY"] == secret
    # The secret must NOT appear in stdout (it was typed via secret_reader).
    assert secret not in stdout.getvalue()


# ---------------------------------------------------------------------------
# Onboarding integration regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_setup_succeeds_then_quits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After interactive setup completes, the REPL starts and accepts /quit."""

    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    # 1 (ollama, no API key), empty base_url, empty model, /quit
    stdin = io.StringIO("1\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "irrelevant",  # Ollama doesn't read a key
    )

    assert code == 0
    out = stdout.getvalue()
    assert "Avo First-Time Setup" in out
    assert "Provider configured: Ollama" in out
    assert "/help" in out  # REPL header after setup


@pytest.mark.asyncio
async def test_fresh_minimax_setup_reaches_repl_without_configerror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the original bug: interactive MiniMax setup -> REPL ready."""

    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    stdin = io.StringIO("4\n2\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "fake-minimax-key",
    )

    assert code == 0
    combined = stdout.getvalue() + stderr.getvalue()
    # Original bug: a ConfigError appeared after the success message.
    assert "AVO_PROVIDER must be one of" not in combined
    assert "got ''" not in combined
    # The header that proves the REPL actually entered the loop.
    assert "/help" in combined
    assert "provider" in combined.lower() and "minimax" in combined


@pytest.mark.asyncio
async def test_fresh_ollama_setup_reaches_repl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    stdin = io.StringIO("1\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "x",
    )

    assert code == 0
    combined = stdout.getvalue() + stderr.getvalue()
    assert "AVO_PROVIDER must be one of" not in combined
    assert "provider" in combined.lower() and "ollama" in combined


@pytest.mark.asyncio
async def test_fresh_openai_setup_reaches_repl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    stdin = io.StringIO("2\n\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "sk-openai-test",
    )

    assert code == 0
    combined = stdout.getvalue() + stderr.getvalue()
    assert "AVO_PROVIDER must be one of" not in combined
    assert "provider" in combined.lower() and "openai" in combined


@pytest.mark.asyncio
async def test_fresh_anthropic_setup_reaches_repl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    stdin = io.StringIO("3\nclaude-opus\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "sk-ant-test",
    )

    assert code == 0
    combined = stdout.getvalue() + stderr.getvalue()
    assert "AVO_PROVIDER must be one of" not in combined
    assert "provider" in combined.lower() and "anthropic" in combined


@pytest.mark.asyncio
async def test_fresh_openrouter_setup_reaches_repl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    # 5 (openrouter), key via secret_reader, default base_url, default model, /quit
    stdin = io.StringIO("5\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "sk-or-repl-test",
    )

    assert code == 0
    combined = stdout.getvalue() + stderr.getvalue()
    assert "AVO_PROVIDER must be one of" not in combined
    assert "openrouter" in combined.lower()


@pytest.mark.asyncio
async def test_fresh_router_setup_reaches_repl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from avo.auth import store_token
    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    store_token("openrouter", "sk-or-mock-token")
    chat_env = _chat_env(tmp_path)

    # 6 (router), default providers, default models, /quit
    stdin = io.StringIO("6\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
    )

    assert code == 0
    combined = stdout.getvalue() + stderr.getvalue()
    assert "AVO_PROVIDER must be one of" not in combined
    assert "router" in combined.lower()


def test_build_chat_context_refuses_none_database_path() -> None:
    """database_path=None must raise AvoError, not TypeError."""

    from avo.chat import build_chat_context
    from avo.exceptions import AvoError

    with pytest.raises(AvoError):
        build_chat_context(
            database_path=None,  # type: ignore[arg-type]
            workspace_root=Path("/tmp"),
            environ={"AVO_PROVIDER": "ollama", "AVO_MODEL": "x"},
        )


@pytest.mark.asyncio
async def test_database_path_never_none_during_chat_normal_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_repl with a Path database_path never crashes with TypeError on None."""

    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    stdin = io.StringIO("1\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    # Sanity: a real Path is passed; if anything tries to hand None downstream,
    # we want the run to crash with a AvoError, never a bare TypeError.
    assert chat_env["db"] is not None
    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "x",
    )
    assert code == 0


@pytest.mark.asyncio
async def test_provider_config_errors_do_not_produce_secondary_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If interactive setup's output is still invalid, no double traceback."""

    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    # Pick OpenAI, type API key, then abort at base_url with EOF.
    stdin = io.StringIO("2\nsk-test123\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: "sk-test123",
    )

    assert code == 2
    combined = stdout.getvalue() + stderr.getvalue()
    # No raw Python traceback.
    assert "Traceback" not in combined
    assert "TypeError" not in combined


@pytest.mark.asyncio
async def test_api_key_never_appears_in_stdout_or_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secret captured via secret_reader must not leak to either stream."""

    from avo.chat import run_repl

    monkeypatch.setattr("os.environ", {})
    chat_env = _chat_env(tmp_path)

    secret = "sk-test-secret-should-not-leak-anywhere"
    stdin = io.StringIO("2\n\n\n\n/quit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    await run_repl(
        database_path=chat_env["db"],
        workspace_root=chat_env["workspace"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        environ={},
        secret_reader=lambda _: secret,
    )

    assert secret not in stdout.getvalue()
    assert secret not in stderr.getvalue()


def test_existing_env_config_still_works() -> None:
    """AVO_ env config still builds a provider without wizard."""

    from avo.config import build_provider_from_env
    from avo.providers.ollama import OllamaProvider

    env = {
        "AVO_PROVIDER": "ollama",
        "AVO_MODEL": "llama3.1",
        "AVO_OLLAMA_BASE_URL": "http://localhost:11434",
    }
    provider = build_provider_from_env(env)
    assert isinstance(provider, OllamaProvider)


def test_build_chat_context_passes_env_to_build_provider_from_env() -> None:
    """The env passed to build_chat_context reaches build_provider_from_env.

    This is the canonical-path guarantee: onboarding output and env-var
    input share the same provider-construction code path.
    """

    from unittest.mock import patch

    from avo.chat import build_chat_context

    env = {
        "AVO_PROVIDER": "openai",
        "AVO_OPENAI_API_KEY": "sk-mock",
        "AVO_OPENAI_BASE_URL": "https://example.invalid/v1",
        "AVO_MODEL": "gpt-5.6",
    }

    # Patch only the factory. The real Workspace and SQLiteEventStore
    # still get constructed so the test exercises the actual integration.
    with patch("avo.chat.build_provider_from_env") as factory:
        factory.return_value = "fake-provider"

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir()
            db = Path(td) / "db"
            try:
                ctx = build_chat_context(
                    database_path=db,
                    workspace_root=ws,
                    environ=env,
                )
                factory.assert_called_once()
                called_env = factory.call_args.args[0]
                assert called_env.get("AVO_PROVIDER") == "openai"
                assert called_env.get("AVO_OPENAI_API_KEY") == "sk-mock"
                assert ctx.runtime.provider == "fake-provider"
            finally:
                import asyncio

                if "ctx" in locals():
                    asyncio.run(ctx.store.close())


def test_merged_env_after_setup_carries_provider_key() -> None:
    """After setup, the merged env passed to build_chat_context has the key."""

    from avo.chat import interactive_first_run_setup

    # Simulate the run_repl merge: user-facing env empty + setup output.
    merged: dict[str, str] = {}
    setup_out = interactive_first_run_setup(
        io.StringIO("4\n2\n\n\n"),  # minimax + style=openai + empty model/base
        io.StringIO(),
        secret_reader=lambda _: "key-xyz",
    )
    assert setup_out is not None
    merged.update(setup_out)
    assert merged["AVO_PROVIDER"] == "minimax"
    assert merged["AVO_MINIMAX_API_STYLE"] == "openai"
