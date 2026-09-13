"""Tests for the conversation history store."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.storage.conversations import (
    ConversationStore,
    HistoryError,
)


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "history.db")


def test_append_assigns_monotonic_sequence(store: ConversationStore) -> None:
    a = store.append("s1", role="user", content="hi")
    b = store.append("s1", role="assistant", content="hello")
    assert a.sequence == 1
    assert b.sequence == 2


def test_turns_returns_ordered_history(store: ConversationStore) -> None:
    store.append("s1", role="user", content="u1")
    store.append("s1", role="assistant", content="a1")
    turns = store.turns("s1")
    assert [t.role for t in turns] == ["user", "assistant"]
    assert [t.content for t in turns] == ["u1", "a1"]


def test_sessions_lists_distinct_keys(store: ConversationStore) -> None:
    store.append("a", role="user", content="x")
    store.append("b", role="user", content="y")
    store.append("a", role="assistant", content="z")
    assert sorted(store.sessions()) == ["a", "b"]


def test_last_turn_returns_most_recent(store: ConversationStore) -> None:
    store.append("s", role="user", content="first")
    store.append("s", role="assistant", content="second")
    assert store.last_turn("s") is not None
    assert store.last_turn("s").content == "second"


def test_last_turn_returns_none_when_empty(store: ConversationStore) -> None:
    assert store.last_turn("missing") is None


def test_truncate_removes_all_turns(store: ConversationStore) -> None:
    store.append("s", role="user", content="x")
    store.append("s", role="user", content="y")
    removed = store.truncate("s")
    assert removed == 2
    assert store.turns("s") == ()


def test_append_rejects_invalid_role(store: ConversationStore) -> None:
    with pytest.raises(HistoryError, match="invalid role"):
        store.append("s", role="bot", content="x")


def test_close_idempotent(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "h.db")
    store.close()
    store.close()  # no raise


def test_operations_on_closed_store_raise(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "h.db")
    store.close()
    with pytest.raises(HistoryError, match="closed"):
        store.append("s", role="user", content="x")


def test_metadata_round_trips(store: ConversationStore) -> None:
    turn = store.append(
        "s",
        role="assistant",
        content="hi",
        metadata={"model": "llama", "tokens": 12},
    )
    fetched = store.turns("s")[0]
    assert fetched.metadata == {"model": "llama", "tokens": 12}
    assert fetched.sequence == turn.sequence


def test_metadata_defaults_to_empty(store: ConversationStore) -> None:
    store.append("s", role="user", content="x")
    assert store.turns("s")[0].metadata == {}


def test_search_turns_across_and_within_sessions(store: ConversationStore) -> None:
    store.append("s1", role="user", content="Deploying to kubernetes cluster")
    store.append("s1", role="assistant", content="Kubernetes deployment succeeded")
    store.append("s2", role="user", content="Fix database migration issue")
    store.append("s2", role="assistant", content="Running kubernetes pod for migration")

    # Search all sessions for 'kubernetes'
    results = store.search_turns("kubernetes")
    assert len(results) == 3
    assert all("kubernetes" in r.content.lower() for r in results)

    # Scoped to session 's1'
    s1_results = store.search_turns("kubernetes", session_id="s1")
    assert len(s1_results) == 2
    assert all(r.session_id == "s1" for r in s1_results)

    # Search non-matching query
    empty = store.search_turns("nonexistent keyword")
    assert empty == ()
