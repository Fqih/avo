"""Tests for the AGY-style ANSI mascot banner and live terminal spinner."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from avo import __version__ as AVO_VERSION
from avo.chat_render import (
    _format_workspace_path,
    _print_header,
    _render_mascot,
    _resolve_user_identity,
)
from avo.chat_stream import LiveAnswerPrinter, TerminalSpinner
from avo.oauth.store import Credential, store_credential


def test_format_workspace_path_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = fake_home / "Project" / "Loopward"
    project.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    assert _format_workspace_path(project) == "~/Project/Loopward"


def test_format_workspace_path_outside_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    other = tmp_path / "var" / "data"
    other.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    assert _format_workspace_path(other) == str(other)


def test_resolve_user_identity_from_stored_subscription(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AVO_CONFIG_DIR", str(tmp_path))
    store_credential(
        Credential(
            provider="claude",
            kind="oauth",
            access_token="tok",
            account="fqih@example.com",
            subscription=True,
        )
    )

    ident = _resolve_user_identity("claude")
    assert ident == "fqih@example.com (Subscription)"


def test_render_mascot_lines_and_colors() -> None:
    colored = _render_mascot(color=True)
    plain = _render_mascot(color=False)

    assert len(colored) == 7
    assert len(plain) == 7
    assert any("\033[" in line for line in colored)
    assert not any("\033[" in line for line in plain)
    assert all("█" in line for line in plain)


def test_print_header_renders_mascot_and_metadata(tmp_path: Path) -> None:
    fake_ctx = MagicMock()
    fake_ctx.provider_name = "gemini_cli"
    fake_ctx.model_name = "gemini-2.5-pro"
    fake_ctx.session_id = "test-session-123"

    out = io.StringIO()
    _print_header(out, fake_ctx, tmp_path / "my-workspace")
    text = out.getvalue()

    assert f"Avo CLI {AVO_VERSION}" in text
    assert "provider: gemini_cli · model: gemini-2.5-pro" in text
    assert "workspace:" in text
    assert "session: test-session-123" in text
    assert "─" * 54 in text


@pytest.mark.asyncio
async def test_terminal_spinner_noop_on_non_tty() -> None:
    out = io.StringIO()
    spinner = TerminalSpinner(out, message="Thinking...")
    assert spinner.enabled is False

    async with spinner:
        await asyncio.sleep(0.01)

    assert out.getvalue() == ""


@pytest.mark.asyncio
async def test_live_answer_printer_calls_on_first_content() -> None:
    out = io.StringIO()
    called = 0

    def on_first() -> None:
        nonlocal called
        called += 1

    printer = LiveAnswerPrinter(out, on_first_content=on_first)
    printer.feed("hello ")
    assert called == 1
    assert printer.answered is True
    assert "Avo> hello " in out.getvalue()

    printer.feed("world")
    assert called == 1  # Only called once
    printer.finish()
    assert out.getvalue().endswith("\n")
