"""Tests for the ReplHistoryManager and slash command completer."""

from __future__ import annotations

from pathlib import Path

from avo.repl_history import ReplHistoryManager, make_slash_completer


def test_make_slash_completer() -> None:
    commands = ["/help", "/compact", "/context", "/commit", "/cost"]
    completer = make_slash_completer(commands)

    # 1. Matching with leading slash
    assert completer("/co", 0) == "/compact"
    assert completer("/co", 1) == "/context"
    assert completer("/co", 2) == "/commit"
    assert completer("/co", 3) == "/cost"
    assert completer("/co", 4) is None

    # 2. Matching without leading slash (delims stripped slash)
    assert completer("co", 0) == "compact"
    assert completer("co", 1) == "context"
    assert completer("co", 2) == "commit"
    assert completer("co", 3) == "cost"
    assert completer("co", 4) is None

    # 3. No match
    assert completer("/xyz", 0) is None


def test_repl_history_manager_append_and_save(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    mgr = ReplHistoryManager(ws, max_entries=5)

    # Initially empty
    assert mgr.list_history() == []

    # Append entries
    mgr.append_history("first prompt")
    mgr.append_history("second prompt")
    # Duplicate consecutive should be ignored
    mgr.append_history("second prompt")
    # Empty entry should be ignored
    mgr.append_history("   ")
    mgr.append_history("third prompt")

    history = mgr.list_history()
    assert history == ["first prompt", "second prompt", "third prompt"]

    # Save to disk
    mgr.save_history()
    assert mgr.history_file.exists()

    # Re-instantiate from same workspace and setup
    mgr2 = ReplHistoryManager(ws, max_entries=5)
    mgr2.setup(commands=["/help", "/draft"])
    assert mgr2.list_history() == ["first prompt", "second prompt", "third prompt"]


def test_repl_history_manager_max_entries(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    # Min entries is 10
    mgr = ReplHistoryManager(ws, max_entries=10)

    for i in range(15):
        mgr.append_history(f"entry {i}")

    history = mgr.list_history()
    assert len(history) == 10
    assert history[0] == "entry 5"
    assert history[-1] == "entry 14"


def test_repl_history_manager_draft_lifecycle(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    mgr = ReplHistoryManager(ws)

    # 1. Initially no draft
    assert mgr.load_draft() is None
    assert mgr.clear_draft() is False

    # 2. Save draft
    mgr.save_draft("Refactor auth system to use JWT tokens\nAnd update tests")
    assert mgr.draft_file.exists()
    loaded = mgr.load_draft()
    assert loaded is not None
    assert "Refactor auth system" in loaded

    # 3. Clear draft
    assert mgr.clear_draft() is True
    assert mgr.load_draft() is None
    assert not mgr.draft_file.exists()
