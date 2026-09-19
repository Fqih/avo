"""Tests for Multi-Agent SQLite Blackboard Store."""

from __future__ import annotations

from pathlib import Path

from avo.blackboard.store import BlackboardStore


async def test_blackboard_set_and_get(tmp_path: Path) -> None:
    db_path = tmp_path / "blackboard.sqlite3"
    store = BlackboardStore(db_path=db_path)

    entry = await store.set(
        "summary",
        {"status": "ok", "items": 3},
        namespace="team1",
        author="agent_a",
    )
    assert entry.key == "summary"
    assert entry.namespace == "team1"
    assert entry.author == "agent_a"
    assert entry.version == 1
    assert entry.value == {"status": "ok", "items": 3}

    retrieved = await store.get("summary", namespace="team1")
    assert retrieved is not None
    assert retrieved.key == "summary"
    assert retrieved.value == {"status": "ok", "items": 3}
    assert retrieved.version == 1

    # Update increases version
    updated = await store.set(
        "summary",
        {"status": "done", "items": 5},
        namespace="team1",
        author="agent_b",
    )
    assert updated.version == 2
    assert updated.author == "agent_b"
    assert updated.value["status"] == "done"


async def test_blackboard_list_and_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "blackboard.sqlite3"
    store = BlackboardStore(db_path=db_path)

    await store.set("plan:step1", "init", namespace="tasks")
    await store.set("plan:step2", "compile", namespace="tasks")
    await store.set("metric:coverage", 92, namespace="tasks")
    await store.set("other", "val", namespace="other_ns")

    entries = await store.list_entries(namespace="tasks", prefix="plan:")
    assert len(entries) == 2
    keys = {e.key for e in entries}
    assert keys == {"plan:step1", "plan:step2"}

    deleted = await store.delete("plan:step1", namespace="tasks")
    assert deleted is True

    remaining = await store.list_entries(namespace="tasks", prefix="plan:")
    assert len(remaining) == 1
    assert remaining[0].key == "plan:step2"
