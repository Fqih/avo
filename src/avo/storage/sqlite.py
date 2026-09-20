"""Durable SQLite event store with explicit connection ownership."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sqlite3
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from avo.events import AgentEvent, EventType, validate_event_append
from avo.exceptions import AvoError, RunNotFoundError, StorageError
from avo.models import Checkpoint, RunRecord
from avo.state import is_terminal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL,
    record_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_events_run_sequence
    ON events(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run_sequence
    ON checkpoints(run_id, event_sequence DESC);
CREATE INDEX IF NOT EXISTS idx_events_type_run
    ON events(event_type, run_id);
CREATE INDEX IF NOT EXISTS idx_runs_state
    ON runs(state);
"""


class SQLiteEventStore:
    """Single-connection SQLite store for normal single-process execution."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self._connection = sqlite3.connect(self.path, isolation_level=None)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            with contextlib.suppress(sqlite3.OperationalError):
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(_SCHEMA)
            self._apply_migrations()
        except sqlite3.Error as exc:
            raise StorageError(
                f"Could not initialize SQLite event store at {self.path!s}: {exc}"
            ) from exc
        self._lock = asyncio.Lock()
        self._closed = False

    def _apply_migrations(self) -> None:
        """Verify schema version and apply migrations with automated backup and validation."""
        row = self._connection.execute("PRAGMA user_version").fetchone()
        current_version = int(row[0]) if row else 0
        target_version = 2

        if current_version > target_version:
            raise StorageError(
                f"Database version {current_version} at {self.path!s} is newer "
                f"than supported version {target_version}."
            )

        if current_version == 0:
            self._validate_schema_integrity()
            self._connection.execute(f"PRAGMA user_version = {target_version}")
            return

        if current_version < target_version:
            if self.path.exists() and self.path.stat().st_size > 0:
                backup_path = self.path.with_suffix(f".bak.{current_version}")
                with contextlib.suppress(Exception):
                    shutil.copy2(self.path, backup_path)

            if current_version < 2:
                self._migrate_v1_to_v2()

            self._validate_schema_integrity()
            self._connection.execute(f"PRAGMA user_version = {target_version}")

    def _migrate_v1_to_v2(self) -> None:
        """Apply schema enhancements from v1 to v2: create covering indexes and verify tables."""
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type_run ON events(event_type, run_id)"
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state)")

    def _validate_schema_integrity(self) -> None:
        """Run integrity check and verify required tables exist."""
        row = self._connection.execute("PRAGMA quick_check").fetchone()
        if not row or row[0] != "ok":
            raise StorageError(f"Database integrity check failed at {self.path!s}: {row}")

        tables_res = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables_res}
        missing = {"runs", "events", "checkpoints"} - table_names
        if missing:
            raise StorageError(f"Database at {self.path!s} is missing required tables: {missing}")

    def _ensure_open(self) -> None:
        if self._closed:
            raise StorageError(f"SQLite event store at {self.path!s} is closed.")

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.execute("COMMIT")

    def _rollback(self) -> None:
        self._connection.execute("ROLLBACK")

    def _run_exists(self, run_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row is not None

    def _require_run_row(self, run_id: str) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT record_json FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone(),
        )
        if row is None:
            raise RunNotFoundError(f"Run {run_id!r} does not exist in {self.path!s}.")
        return row

    def _read_run(self, run_id: str) -> RunRecord:
        row = self._require_run_row(run_id)
        return RunRecord.model_validate_json(str(row["record_json"]))

    def _read_events(self, run_id: str) -> list[AgentEvent]:
        rows = self._connection.execute(
            "SELECT event_json FROM events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        return [AgentEvent.model_validate_json(str(row["event_json"])) for row in rows]

    def _next_sequence(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row is not None
        return int(row["next_sequence"])

    def _insert_event(self, event: AgentEvent) -> AgentEvent:
        existing = self._read_events(event.run_id)
        validate_event_append(existing, event)
        persisted = event.model_copy(
            update={"sequence": self._next_sequence(event.run_id)},
            deep=True,
        )
        self._connection.execute(
            """
            INSERT INTO events(
                run_id, sequence, event_id, event_type, created_at, event_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                persisted.run_id,
                persisted.sequence,
                persisted.event_id,
                persisted.event_type.value,
                persisted.created_at.isoformat(),
                persisted.model_dump_json(),
            ),
        )
        return persisted

    def _replace_run(self, run: RunRecord) -> None:
        self._connection.execute(
            "UPDATE runs SET state = ?, record_json = ? WHERE run_id = ?",
            (run.state.value, run.model_dump_json(), run.run_id),
        )

    def _raise_storage_error(self, action: str, exc: Exception) -> StorageError:
        return StorageError(f"SQLite event store could not {action} at {self.path!s}: {exc}")

    async def create_run(self, run: RunRecord, event: AgentEvent) -> AgentEvent:
        """Atomically create a run and allocate sequence one to RUN_CREATED."""

        async with self._lock:
            self._ensure_open()
            if event.run_id != run.run_id:
                raise StorageError("Run and creation event use different run IDs.")
            if event.event_type is not EventType.RUN_CREATED:
                raise StorageError("create_run requires a RUN_CREATED event.")
            try:
                self._begin()
                if self._run_exists(run.run_id):
                    raise StorageError(f"Run {run.run_id!r} already exists.")
                self._connection.execute(
                    """
                    INSERT INTO runs(run_id, created_at, state, record_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.created_at.isoformat(),
                        run.state.value,
                        run.model_dump_json(),
                    ),
                )
                persisted = self._insert_event(event)
                self._commit()
                return persisted
            except AvoError:
                self._rollback()
                raise
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                self._rollback()
                raise self._raise_storage_error("create a run", exc) from exc

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        """Append one event with transactionally allocated sequence."""

        async with self._lock:
            self._ensure_open()
            try:
                self._begin()
                self._require_run_row(event.run_id)
                persisted = self._insert_event(event)
                self._commit()
                return persisted
            except AvoError:
                self._rollback()
                raise
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                self._rollback()
                raise self._raise_storage_error("append an event", exc) from exc

    async def append_event_and_update_run(
        self,
        event: AgentEvent,
        run: RunRecord,
    ) -> AgentEvent:
        """Atomically append an event and replace run metadata."""

        async with self._lock:
            self._ensure_open()
            try:
                self._begin()
                current = self._read_run(run.run_id)
                if event.run_id != run.run_id:
                    raise StorageError("Run and event use different run IDs.")
                if event.event_type is EventType.STATE_CHANGED:
                    if event.payload.get("from_state") != current.state.value:
                        raise StorageError(
                            "STATE_CHANGED from_state does not match stored run state."
                        )
                    if event.payload.get("to_state") != run.state.value:
                        raise StorageError(
                            "STATE_CHANGED to_state does not match updated run state."
                        )
                persisted = self._insert_event(event)
                self._replace_run(run)
                self._commit()
                return persisted
            except AvoError:
                self._rollback()
                raise
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                self._rollback()
                raise self._raise_storage_error("append an event and update a run", exc) from exc

    async def save_checkpoint(
        self,
        checkpoint: Checkpoint,
        event: AgentEvent,
    ) -> tuple[Checkpoint, AgentEvent]:
        """Atomically append ``CHECKPOINT_CREATED`` and persist its snapshot."""

        from avo.storage.sqlite_checkpoints import save_checkpoint as _save

        return await _save(self, checkpoint, event)

    async def finalize_run(
        self,
        run: RunRecord,
        state_event: AgentEvent,
        terminal_event: AgentEvent,
    ) -> tuple[AgentEvent, AgentEvent]:
        """Atomically persist a terminal transition, event, and run record."""

        async with self._lock:
            self._ensure_open()
            try:
                self._begin()
                current = self._read_run(run.run_id)
                if not is_terminal(run.state):
                    raise StorageError("finalize_run requires terminal run metadata.")
                if is_terminal(current.state):
                    raise StorageError(f"Run {run.run_id!r} is already terminal.")
                if state_event.event_type is not EventType.STATE_CHANGED:
                    raise StorageError("finalize_run requires a STATE_CHANGED event.")
                if state_event.payload.get("from_state") != current.state.value:
                    raise StorageError("Final transition does not start from the stored state.")
                if state_event.payload.get("to_state") != run.state.value:
                    raise StorageError("Final transition does not end at the terminal run state.")
                if terminal_event.run_id != run.run_id or state_event.run_id != run.run_id:
                    raise StorageError("Final events and run metadata use different run IDs.")
                persisted_state = self._insert_event(state_event)
                persisted_terminal = self._insert_event(terminal_event)
                self._replace_run(run)
                self._commit()
                return persisted_state, persisted_terminal
            except AvoError:
                self._rollback()
                raise
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                self._rollback()
                raise self._raise_storage_error("finalize a run", exc) from exc

    async def get_run(self, run_id: str) -> RunRecord:
        """Return run metadata."""

        async with self._lock:
            self._ensure_open()
            try:
                return self._read_run(run_id)
            except AvoError:
                raise
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                raise self._raise_storage_error("read a run", exc) from exc

    async def update_run(self, run: RunRecord) -> None:
        """Update run metadata without permitting an unlogged state change."""

        async with self._lock:
            self._ensure_open()
            try:
                self._begin()
                current = self._read_run(run.run_id)
                if run.state is not current.state:
                    raise StorageError(
                        "update_run cannot change state; append a STATE_CHANGED event atomically."
                    )
                self._replace_run(run)
                self._commit()
            except AvoError:
                self._rollback()
                raise
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                self._rollback()
                raise self._raise_storage_error("update a run", exc) from exc

    async def list_runs(self) -> list[RunRecord]:
        """Return runs ordered by descending creation time."""

        async with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(
                    "SELECT record_json FROM runs ORDER BY created_at DESC"
                ).fetchall()
                return [RunRecord.model_validate_json(str(row["record_json"])) for row in rows]
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                raise self._raise_storage_error("list runs", exc) from exc

    async def get_events(self, run_id: str) -> list[AgentEvent]:
        """Return events in ascending sequence order."""

        async with self._lock:
            self._ensure_open()
            try:
                self._require_run_row(run_id)
                return self._read_events(run_id)
            except AvoError:
                raise
            except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
                raise self._raise_storage_error("read events", exc) from exc

    async def get_latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        """Return the latest checkpoint for a run, or ``None`` if none exists."""

        from avo.storage.sqlite_checkpoints import get_latest_checkpoint as _get

        return await _get(self, run_id)

    async def is_terminal_run(self, run_id: str) -> bool:
        """Return whether a run is terminal."""

        return is_terminal((await self.get_run(run_id)).state)

    async def close(self) -> None:
        """Close this store's explicit SQLite connection."""

        async with self._lock:
            if not self._closed:
                try:
                    self._connection.close()
                except sqlite3.Error as exc:
                    raise self._raise_storage_error("close the connection", exc) from exc
                finally:
                    self._closed = True

    async def __aenter__(self) -> SQLiteEventStore:
        """Enter an async context manager."""

        self._ensure_open()
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close the connection on async context-manager exit."""
        await self.close()
