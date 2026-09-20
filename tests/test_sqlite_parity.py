"""Behavioral parity tests for memory and SQLite event stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from avo.events import AgentEvent, EventType
from avo.exceptions import RunNotFoundError, StorageError
from avo.models import Checkpoint, RunRecord
from avo.policies import LoopPolicy
from avo.state import RunState, StopReason
from avo.storage import EventStore, InMemoryEventStore, SQLiteEventStore
from tests.helpers import seed_run


@pytest.fixture(params=["memory", "sqlite"])
async def event_store(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[EventStore]:
    store: EventStore
    if request.param == "memory":
        store = InMemoryEventStore()
    else:
        store = SQLiteEventStore(tmp_path / "parity.db")
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_create_append_read_update_and_list_parity(event_store: EventStore) -> None:
    run = await seed_run(event_store)
    event = await event_store.append_event(
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.MODEL_REQUESTED,
            payload={"step": 1},
        )
    )
    updated = RunRecord.model_validate({**run.model_dump(), "steps": 1})
    await event_store.update_run(updated)

    loaded = await event_store.get_run(run.run_id)
    events = await event_store.get_events(run.run_id)
    listed = await event_store.list_runs()

    assert event.sequence == 2
    assert loaded.steps == 1
    assert [item.sequence for item in events] == [1, 2]
    assert [item.run_id for item in listed] == [run.run_id]
    assert await event_store.is_terminal_run(run.run_id) is False


@pytest.mark.asyncio
async def test_checkpoint_round_trip_parity(event_store: EventStore) -> None:
    run = await seed_run(event_store)
    checkpoint = Checkpoint(
        run_id=run.run_id,
        state=RunState.CREATED,
        messages=[{"role": "user", "content": "hello"}],
        next_step=2,
        repeated_action_history=["a"],
        completed_tool_call_ids={"call-1"},
        user_state={"revision": 2},
        policy=LoopPolicy().model_dump(mode="json"),
    )
    saved, event = await event_store.save_checkpoint(
        checkpoint,
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.CHECKPOINT_CREATED,
            payload={"checkpoint_id": checkpoint.checkpoint_id},
        ),
    )
    loaded = await event_store.get_latest_checkpoint(run.run_id)

    assert loaded is not None
    assert saved.last_event_sequence == event.sequence == 2
    assert loaded.model_dump() == saved.model_dump()
    assert loaded.completed_tool_call_ids == {"call-1"}


@pytest.mark.asyncio
async def test_unknown_runs_raise_specific_error_parity(event_store: EventStore) -> None:
    with pytest.raises(RunNotFoundError, match="missing"):
        await event_store.get_run("missing")
    with pytest.raises(RunNotFoundError):
        await event_store.get_events("missing")
    with pytest.raises(RunNotFoundError):
        await event_store.get_latest_checkpoint("missing")


@pytest.mark.asyncio
async def test_update_run_cannot_hide_state_change(event_store: EventStore) -> None:
    run = await seed_run(event_store)
    changed = run.model_copy(update={"state": RunState.MODEL_PENDING})

    with pytest.raises(StorageError, match="STATE_CHANGED"):
        await event_store.update_run(changed)


@pytest.mark.asyncio
async def test_atomic_terminal_finalize_parity(event_store: EventStore) -> None:
    run = await seed_run(event_store)
    failed = RunRecord.model_validate(
        {
            **run.model_dump(),
            "state": RunState.FAILED,
            "stop_reason": StopReason.INTERNAL_ERROR,
        }
    )
    state_event, terminal_event = await event_store.finalize_run(
        failed,
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.STATE_CHANGED,
            payload={"from_state": "created", "to_state": "failed"},
        ),
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.RUN_FAILED,
            payload={"state": "failed", "stop_reason": "internal_error"},
        ),
    )

    assert state_event.sequence == 2
    assert terminal_event.sequence == 3
    assert await event_store.is_terminal_run(run.run_id) is True
    assert (await event_store.get_run(run.run_id)).stop_reason is StopReason.INTERNAL_ERROR


@pytest.mark.asyncio
async def test_closed_store_rejects_operations(event_store: EventStore) -> None:
    await event_store.close()

    with pytest.raises(StorageError, match="closed"):
        await event_store.list_runs()


@pytest.mark.asyncio
async def test_sqlite_close_and_reopen_preserves_all_data(tmp_path: Path) -> None:
    path = tmp_path / "durable.db"
    first = SQLiteEventStore(path)
    run = await seed_run(first, run_id="durable-run")
    checkpoint = Checkpoint(
        run_id=run.run_id,
        state=run.state,
        messages=[{"role": "user", "content": "persist"}],
        next_step=1,
        policy=LoopPolicy().model_dump(mode="json"),
    )
    await first.save_checkpoint(
        checkpoint,
        AgentEvent(
            run_id=run.run_id,
            event_type=EventType.CHECKPOINT_CREATED,
            payload={"checkpoint_id": checkpoint.checkpoint_id},
        ),
    )
    await first.close()

    second = SQLiteEventStore(path)
    assert (await second.get_run(run.run_id)).task == "test task"
    assert [event.sequence for event in await second.get_events(run.run_id)] == [1, 2]
    assert await second.get_latest_checkpoint(run.run_id) is not None
    await second.close()


def test_sqlite_initialization_errors_have_path_context(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match=str(tmp_path)):
        SQLiteEventStore(tmp_path)


@pytest.mark.asyncio
async def test_sqlite_async_context_manager_closes_connection(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "context.db")
    async with store:
        await seed_run(store)

    with pytest.raises(StorageError, match="closed"):
        await store.list_runs()


@pytest.mark.asyncio
async def test_store_atomic_api_guards_have_backend_parity(event_store: EventStore) -> None:
    run = await seed_run(event_store)
    creation = AgentEvent(run_id=run.run_id, event_type=EventType.RUN_CREATED)
    with pytest.raises(StorageError, match="already exists"):
        await event_store.create_run(run, creation)

    other = RunRecord(run_id="other", task="other")
    with pytest.raises(StorageError, match="different run IDs"):
        await event_store.create_run(
            other,
            AgentEvent(run_id="wrong", event_type=EventType.RUN_CREATED),
        )
    with pytest.raises(StorageError, match="RUN_CREATED"):
        await event_store.create_run(
            other,
            AgentEvent(run_id=other.run_id, event_type=EventType.MODEL_REQUESTED),
        )

    with pytest.raises(StorageError, match="different run IDs"):
        await event_store.append_event_and_update_run(
            AgentEvent(run_id="wrong", event_type=EventType.MODEL_REQUESTED),
            run,
        )
    with pytest.raises(StorageError, match="from_state"):
        await event_store.append_event_and_update_run(
            AgentEvent(
                run_id=run.run_id,
                event_type=EventType.STATE_CHANGED,
                payload={"from_state": "model_pending", "to_state": "model_pending"},
            ),
            run,
        )
    with pytest.raises(StorageError, match="to_state"):
        await event_store.append_event_and_update_run(
            AgentEvent(
                run_id=run.run_id,
                event_type=EventType.STATE_CHANGED,
                payload={"from_state": "created", "to_state": "model_pending"},
            ),
            run,
        )

    checkpoint = Checkpoint(
        run_id=run.run_id,
        state=run.state,
        messages=[],
        next_step=1,
        policy=LoopPolicy().model_dump(mode="json"),
    )
    with pytest.raises(StorageError, match="different run IDs"):
        await event_store.save_checkpoint(
            checkpoint,
            AgentEvent(run_id="wrong", event_type=EventType.CHECKPOINT_CREATED),
        )
    with pytest.raises(StorageError, match="CHECKPOINT_CREATED"):
        await event_store.save_checkpoint(
            checkpoint,
            AgentEvent(run_id=run.run_id, event_type=EventType.MODEL_REQUESTED),
        )

    failed = RunRecord.model_validate(
        {
            **run.model_dump(),
            "state": RunState.FAILED,
            "stop_reason": StopReason.INTERNAL_ERROR,
        }
    )
    valid_state = AgentEvent(
        run_id=run.run_id,
        event_type=EventType.STATE_CHANGED,
        payload={"from_state": "created", "to_state": "failed"},
    )
    valid_terminal = AgentEvent(
        run_id=run.run_id,
        event_type=EventType.RUN_FAILED,
        payload={"state": "failed", "stop_reason": "internal_error"},
    )
    with pytest.raises(StorageError, match="terminal run metadata"):
        await event_store.finalize_run(run, valid_state, valid_terminal)
    with pytest.raises(StorageError, match="STATE_CHANGED"):
        await event_store.finalize_run(
            failed,
            AgentEvent(run_id=run.run_id, event_type=EventType.MODEL_REQUESTED),
            valid_terminal,
        )
    with pytest.raises(StorageError, match="start from"):
        await event_store.finalize_run(
            failed,
            valid_state.model_copy(
                update={"payload": {"from_state": "model_pending", "to_state": "failed"}}
            ),
            valid_terminal,
        )
    with pytest.raises(StorageError, match="end at"):
        await event_store.finalize_run(
            failed,
            valid_state.model_copy(
                update={"payload": {"from_state": "created", "to_state": "cancelled"}}
            ),
            valid_terminal,
        )
    with pytest.raises(StorageError, match="different run IDs"):
        await event_store.finalize_run(
            failed,
            valid_state,
            valid_terminal.model_copy(update={"run_id": "wrong"}),
        )

    await event_store.finalize_run(failed, valid_state, valid_terminal)
    with pytest.raises(StorageError, match="already terminal"):
        await event_store.finalize_run(failed, valid_state, valid_terminal)


@pytest.mark.asyncio
async def test_in_memory_async_context_manager_closes_store() -> None:
    store = InMemoryEventStore()
    async with store:
        await seed_run(store)

    with pytest.raises(StorageError, match="closed"):
        await store.list_runs()


@pytest.mark.asyncio
async def test_sqlite_wraps_low_level_read_error_with_cause(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "broken-connection.db")
    store._connection.close()

    with pytest.raises(StorageError, match="list runs") as captured:
        await store.list_runs()
    assert captured.value.__cause__ is not None
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_user_version_and_migration_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "versioned.db"
    store = SQLiteEventStore(db_path)
    # Check user_version is initialized to 2
    row = store._connection.execute("PRAGMA user_version").fetchone()
    assert row[0] == 2
    await store.close()

    # Simulate older version 1 database with content
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    # Reopening should detect version 1, create backup .bak.1, and upgrade to 2
    store2 = SQLiteEventStore(db_path)
    row2 = store2._connection.execute("PRAGMA user_version").fetchone()
    assert row2[0] == 2
    await store2.close()
    assert (tmp_path / "versioned.bak.1").exists()
